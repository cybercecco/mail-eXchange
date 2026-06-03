"""Collect mail traffic counters from shared Postfix/Amavis logs and queue snapshot."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.db import DATA_DIR

LOGS_DIR = DATA_DIR / "logs"
STATS_DIR = DATA_DIR / "stats"
QUEUE_SNAPSHOT = STATS_DIR / "queue.json"

POSTFIX_TS = re.compile(
    r"^(?P<mon>[A-Z][a-z]{2})\s+(?P<day>\d{1,2})\s+(?P<time>\d{2}:\d{2}:\d{2})\s+"
)
POSTFIX_INCOMING = re.compile(r"postfix/smtpd\[\d+\]:\s+(?P<qid>[A-F0-9]+):\s+client=", re.I)
POSTFIX_SENT = re.compile(
    r"postfix/smtp\[\d+\]:\s+(?P<qid>[A-F0-9]+):.*status=sent\s+\((?P<detail>[^)]+)\)",
    re.I,
)
POSTFIX_NOQUEUE_REJECT = re.compile(
    r"postfix/(?:smtpd|smtp|qmgr|cleanup)\[\d+\]:\s+NOQUEUE:\s+(?:milter-)?reject:",
    re.I,
)
POSTFIX_QID_REJECT = re.compile(
    r"postfix/(?:smtpd|smtp|qmgr|cleanup)\[\d+\]:\s+(?P<qid>[A-F0-9]+):.*?(?:milter-reject|reject):",
    re.I,
)
POSTFIX_REJECT_DETAIL = re.compile(
    r"(?:NOQUEUE:\s+)?(?:milter-)?reject:\s(?P<reason>[^;]+)(?:;\s+from=<(?P<from>[^>]*)>)?"
    r"(?:\s+to=<(?P<to>[^>]*)>)?",
    re.I,
)
POSTFIX_BOUNCED = re.compile(
    r"postfix/smtp\[\d+\]:\s+(?P<qid>[A-F0-9]+):.*status=bounced\s+\((?P<reason>[^)]+)\)",
    re.I,
)
POSTFIX_CLIENT = re.compile(
    r"postfix/smtpd\[\d+\]:\s+(?P<qid>[A-F0-9]+):\s+client=(?P<client>[^,]+)",
    re.I,
)
POSTFIX_FROM = re.compile(r"from=<(?P<from>[^>]*)>", re.I)
POSTFIX_TO = re.compile(r"to=<(?P<to>[^>]*)>", re.I)
POSTFIX_BLOCK_EXCLUDE = re.compile(
    r"dict_nis_init|NIS domain name not set|warning:\s|milter.*warning",
    re.I,
)
AMAVIS_BLOCKED = re.compile(
    r"(?P<msg_id>\([^)]+\))\s+Blocked\s+"
    r"(?P<reason>SPAM|INFECT(?:ED)?|BAD\s+HEADER|NAME|BANNED|BLACKLISTED|"
    r"FORGED|VIRUS|PHISHING|MIME|UNWANTED|MULTIPLE)",
    re.I,
)
AMAVIS_ROUTE = re.compile(
    r"<(?P<from>[^>]+)>\s*->\s*<(?P<to>[^>]+)>",
    re.I,
)
AMAVIS_TS = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<time>\d{2}:\d{2}:\d{2}(?:,\d+)?)"
)
AMAVIS_MSG_ID = re.compile(r"\((?P<id>\d+-\d+(?:-\d+)?)\)")
AMAVIS_DONE = re.compile(
    r"\((?P<id>\d+-\d+(?:-\d+)?)\)\s+(?:Passed|Blocked|Died|Fatal|discarded|aborted)",
    re.I,
)
AMAVIS_CLAM = re.compile(r"\((?P<id>\d+-\d+(?:-\d+)?)\).*ClamAV", re.I)
AMAVIS_SPAM = re.compile(
    r"\((?P<id>\d+-\d+(?:-\d+)?)\).*(?:SpamControl|SpamAssassin|check_init|spamd|SA\s)",
    re.I,
)

MONTHS = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}


def _log_timezone() -> timezone:
    tz_name = os.environ.get("TZ", "Europe/Rome")
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return timezone.utc


def _now() -> datetime:
    return datetime.now(_log_timezone())


def _parse_postfix_ts(line: str, ref: datetime) -> datetime | None:
    match = POSTFIX_TS.match(line)
    if not match:
        return None
    month = MONTHS.get(match.group("mon"))
    if not month:
        return None
    day = int(match.group("day"))
    hour, minute, second = (int(part) for part in match.group("time").split(":"))
    year = ref.year
    tz = ref.tzinfo or _log_timezone()
    try:
        ts = datetime(year, month, day, hour, minute, second, tzinfo=tz)
    except ValueError:
        return None
    if ts > ref + timedelta(hours=2):
        ts = ts.replace(year=year - 1)
    return ts


def _parse_amavis_ts(line: str, ref: datetime | None = None) -> datetime | None:
    match = AMAVIS_TS.match(line)
    if not match:
        return None
    time_part = match.group("time").replace(",", ".")
    tz = (ref.tzinfo if ref else None) or _log_timezone()
    try:
        return datetime.fromisoformat(f"{match.group('date')}T{time_part}").replace(tzinfo=tz)
    except ValueError:
        return None


def _line_ts(line: str, ref: datetime) -> datetime | None:
    return _parse_amavis_ts(line, ref) or _parse_postfix_ts(line, ref)


def _tail_bytes_for_window(window_minutes: int) -> int:
    return max(256_000, min(4_000_000, window_minutes * 12_000))


def _tail_lines(path: Path, max_bytes: int = 512_000) -> list[str]:
    if not path.is_file():
        return []
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > max_bytes:
            handle.seek(size - max_bytes)
            handle.readline()
        data = handle.read().decode("utf-8", errors="replace")
    return data.splitlines()


def _is_outgoing_relay(line: str, detail: str) -> bool:
    lowered = line.lower()
    if "amavis" in lowered:
        return False
    if "127.0.0.1" in lowered and "10025" in lowered:
        return False
    if "[127.0.0.1" in lowered:
        return False
    if "relay=localhost" in lowered and "10025" in lowered:
        return False
    _ = detail
    return True


def _empty_pipeline() -> dict[str, int]:
    return {
        "postfix_active": 0,
        "postfix_to_amavis": 0,
        "postfix_outbound": 0,
        "postfix_deferred": 0,
        "postfix_hold": 0,
        "amavis": 0,
        "clamav": 0,
        "spamassassin": 0,
    }


def _parse_amavis_pipeline(max_age_seconds: int = 180) -> dict[str, int]:
    """Count messages currently inside Amavis/ClamAV/SpamAssassin from recent log lines."""
    now = _now()
    cutoff = now - timedelta(seconds=max_age_seconds)
    inflight: dict[str, dict[str, Any]] = {}

    for line in _tail_lines(LOGS_DIR / "amavis.log", max_bytes=384_000):
        ts = _line_ts(line, now)
        if ts is not None and ts < cutoff:
            continue
        done = AMAVIS_DONE.search(line)
        if done:
            inflight.pop(done.group("id"), None)
            continue
        msg = AMAVIS_MSG_ID.search(line)
        if not msg:
            continue
        msg_id = msg.group("id")
        stage = "amavis"
        if AMAVIS_CLAM.search(line):
            stage = "clamav"
        elif AMAVIS_SPAM.search(line):
            stage = "spamassassin"
        inflight[msg_id] = {"stage": stage, "ts": ts or now}

    counts = {"amavis": 0, "clamav": 0, "spamassassin": 0}
    for item in inflight.values():
        stage = str(item.get("stage") or "amavis")
        if stage in counts:
            counts[stage] += 1
        else:
            counts["amavis"] += 1
    return counts


def read_pipeline_snapshot() -> dict[str, Any]:
    """Merge Postfix queue snapshot with Amavis in-flight processing counts."""
    snapshot = read_queue_snapshot()
    raw_pipeline = snapshot.get("raw_pipeline") or {}
    pipeline = _empty_pipeline()
    pipeline["postfix_to_amavis"] = int(raw_pipeline.get("postfix_to_amavis", 0))
    pipeline["postfix_outbound"] = int(raw_pipeline.get("postfix_outbound", 0))
    active = int(snapshot.get("active", 0))
    pipeline["postfix_active"] = max(0, active - pipeline["postfix_to_amavis"] - pipeline["postfix_outbound"])
    pipeline["postfix_deferred"] = int(snapshot.get("deferred", 0))
    pipeline["postfix_hold"] = int(snapshot.get("hold", 0))

    amavis_counts = _parse_amavis_pipeline()
    pipeline["amavis"] = amavis_counts["amavis"]
    pipeline["clamav"] = amavis_counts["clamav"]
    pipeline["spamassassin"] = amavis_counts["spamassassin"]

    snapshot["pipeline"] = pipeline
    snapshot["pipeline_updated_at"] = _now().isoformat()
    return snapshot


def read_queue_snapshot() -> dict[str, Any]:
    """Return the live Postfix queue snapshot written by mx-postfix watch_queue."""
    empty: dict[str, Any] = {
        "total": 0,
        "active": 0,
        "deferred": 0,
        "hold": 0,
        "updated_at": None,
        "messages": {"active": [], "deferred": [], "hold": []},
        "raw_pipeline": {},
        "source_available": False,
        "collected_at": _now().isoformat(),
    }
    if not QUEUE_SNAPSHOT.is_file():
        return empty
    try:
        payload = json.loads(QUEUE_SNAPSHOT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    raw_messages = payload.get("messages") or {}
    active = int(payload.get("active", 0))
    deferred = int(payload.get("deferred", 0))
    hold = int(payload.get("hold", 0))
    total = int(payload.get("total", active + deferred + hold))
    raw_pipeline = payload.get("pipeline") if isinstance(payload.get("pipeline"), dict) else {}
    return {
        "total": total,
        "active": active,
        "deferred": deferred,
        "hold": hold,
        "updated_at": payload.get("updated_at"),
        "messages": {
            "active": list(raw_messages.get("active") or []),
            "deferred": list(raw_messages.get("deferred") or []),
            "hold": list(raw_messages.get("hold") or []),
        },
        "raw_pipeline": raw_pipeline,
        "source_available": True,
        "collected_at": _now().isoformat(),
    }


def _parse_postfix_arrival(arrival: str, ref: datetime) -> datetime | None:
    parts = arrival.split()
    if len(parts) != 4:
        return None
    month = MONTHS.get(parts[0])
    if not month:
        return None
    try:
        day = int(parts[2])
        hour, minute, second = (int(part) for part in parts[3].split(":"))
    except ValueError:
        return None
    year = ref.year
    tz = ref.tzinfo or _log_timezone()
    try:
        ts = datetime(year, month, day, hour, minute, second, tzinfo=tz)
    except ValueError:
        return None
    if ts > ref + timedelta(hours=2):
        ts = ts.replace(year=year - 1)
    return ts


def _enrich_queue_messages(messages: list[dict[str, Any]], ref: datetime) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for item in messages:
        arrival = str(item.get("arrival") or "")
        arrival_ts = _parse_postfix_arrival(arrival, ref)
        age_seconds = None
        if arrival_ts is not None:
            age_seconds = max(0, int((ref - arrival_ts).total_seconds()))
        to_value = item.get("to")
        if isinstance(to_value, str):
            recipients = [to_value]
        elif isinstance(to_value, list):
            recipients = [str(value) for value in to_value if value]
        else:
            recipients = []
        enriched.append(
            {
                "queue_id": str(item.get("queue_id") or ""),
                "from": str(item.get("from") or ""),
                "to": recipients,
                "size_bytes": int(item.get("size_bytes") or 0),
                "status": str(item.get("status") or ""),
                "arrival": arrival,
                "age_seconds": age_seconds,
            }
        )
    enriched.sort(key=lambda row: row.get("age_seconds") is None or -(row.get("age_seconds") or 0))
    return enriched


def _is_postfix_block_excluded(line: str) -> bool:
    return bool(POSTFIX_BLOCK_EXCLUDE.search(line))


def _block_dedupe_key(*, qid: str = "", msg_id: str = "", from_addr: str = "", to_addr: str = "", reason: str = "") -> str:
    if qid:
        return f"qid:{qid.upper()}"
    if msg_id:
        return f"amavis:{msg_id}"
    return f"noqueue:{from_addr}:{to_addr}:{reason[:120]}"


def _extract_postfix_block(line: str) -> dict[str, str] | None:
    if _is_postfix_block_excluded(line):
        return None
    bounced = POSTFIX_BOUNCED.search(line)
    if bounced:
        return {
            "queue_id": bounced.group("qid") or "",
            "from": "",
            "to": "",
            "reason": (bounced.group("reason") or "").strip(),
        }
    qid_reject = POSTFIX_QID_REJECT.search(line)
    if qid_reject:
        reject = POSTFIX_REJECT_DETAIL.search(line)
        return {
            "queue_id": qid_reject.group("qid") or "",
            "from": reject.group("from") if reject and reject.group("from") else "",
            "to": reject.group("to") if reject and reject.group("to") else "",
            "reason": (reject.group("reason") if reject else "rejected").strip(),
        }
    if POSTFIX_NOQUEUE_REJECT.search(line):
        reject = POSTFIX_REJECT_DETAIL.search(line)
        from_addr = reject.group("from") if reject and reject.group("from") else ""
        to_addr = reject.group("to") if reject and reject.group("to") else ""
        if not from_addr:
            from_match = POSTFIX_FROM.search(line)
            from_addr = from_match.group("from") if from_match else ""
        if not to_addr:
            to_match = POSTFIX_TO.search(line)
            to_addr = to_match.group("to") if to_match else ""
        return {
            "queue_id": "",
            "from": from_addr,
            "to": to_addr,
            "reason": (reject.group("reason") if reject else "rejected").strip(),
        }
    return None


def _parse_window_transit(
    window_minutes: int,
    now: datetime,
    cutoff: datetime,
) -> dict[str, Any]:
    incoming_ids: set[str] = set()
    outgoing_ids: set[str] = set()
    blocked_keys: set[str] = set()
    blocked_messages: dict[str, dict[str, Any]] = {}
    incoming_messages: dict[str, dict[str, Any]] = {}
    outgoing_messages: dict[str, dict[str, Any]] = {}

    for line in _tail_lines(LOGS_DIR / "postfix.log", _tail_bytes_for_window(window_minutes)):
        if not _in_window(line, now, cutoff):
            continue
        incoming = POSTFIX_INCOMING.search(line)
        if incoming:
            qid = incoming.group("qid")
            incoming_ids.add(qid)
            if qid not in incoming_messages:
                ts = _line_ts(line, now)
                client = POSTFIX_CLIENT.search(line)
                incoming_messages[qid] = {
                    "timestamp": ts.isoformat() if ts else None,
                    "source": "postfix",
                    "queue_id": qid,
                    "from": "",
                    "to": [],
                    "client": client.group("client").strip() if client else "",
                    "summary": line.strip()[-240:],
                }
            continue
        sent = POSTFIX_SENT.search(line)
        if sent and _is_outgoing_relay(line, sent.group("detail")):
            qid = sent.group("qid")
            outgoing_ids.add(qid)
            if qid not in outgoing_messages:
                ts = _line_ts(line, now)
                from_match = POSTFIX_FROM.search(line)
                to_match = POSTFIX_TO.search(line)
                outgoing_messages[qid] = {
                    "timestamp": ts.isoformat() if ts else None,
                    "source": "postfix",
                    "queue_id": qid,
                    "from": from_match.group("from") if from_match else "",
                    "to": [to_match.group("to")] if to_match else [],
                    "reason": sent.group("detail").strip(),
                    "summary": line.strip()[-240:],
                }
            continue
        block = _extract_postfix_block(line)
        if not block:
            continue
        key = _block_dedupe_key(
            qid=block.get("queue_id", ""),
            from_addr=block.get("from", ""),
            to_addr=block.get("to", ""),
            reason=block.get("reason", ""),
        )
        if key in blocked_keys:
            continue
        blocked_keys.add(key)
        ts = _line_ts(line, now)
        blocked_messages[key] = {
            "timestamp": ts.isoformat() if ts else None,
            "source": "postfix",
            "queue_id": block.get("queue_id", ""),
            "from": block.get("from", ""),
            "to": [block.get("to", "")] if block.get("to") else [],
            "reason": block.get("reason", "blocked"),
            "summary": line.strip()[-240:],
        }

    for line in _tail_lines(LOGS_DIR / "amavis.log", _tail_bytes_for_window(window_minutes)):
        if not _in_window(line, now, cutoff):
            continue
        blocked = AMAVIS_BLOCKED.search(line)
        if not blocked:
            continue
        route = AMAVIS_ROUTE.search(line)
        from_addr = route.group("from") if route else ""
        to_addr = route.group("to") if route else ""
        reason = f"Blocked {blocked.group('reason')}"
        key = _block_dedupe_key(msg_id=blocked.group("msg_id"), from_addr=from_addr, to_addr=to_addr, reason=reason)
        if key in blocked_keys:
            continue
        blocked_keys.add(key)
        ts = _line_ts(line, now)
        blocked_messages[key] = {
            "timestamp": ts.isoformat() if ts else None,
            "source": "amavis",
            "queue_id": "",
            "from": from_addr,
            "to": [to_addr] if to_addr else [],
            "reason": reason,
            "summary": line.strip()[-240:],
        }

    def _sort_messages(rows: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        items = list(rows.values())
        items.sort(key=lambda row: row.get("timestamp") or "", reverse=True)
        return items

    return {
        "incoming_ids": incoming_ids,
        "outgoing_ids": outgoing_ids,
        "blocked_keys": blocked_keys,
        "incoming_messages": _sort_messages(incoming_messages),
        "outgoing_messages": _sort_messages(outgoing_messages),
        "blocked_messages": _sort_messages(blocked_messages),
    }


def _collect_blocked_messages(window_minutes: int, now: datetime, cutoff: datetime) -> list[dict[str, Any]]:
    return _parse_window_transit(window_minutes, now, cutoff)["blocked_messages"]


def _collect_incoming_messages(window_minutes: int, now: datetime, cutoff: datetime) -> list[dict[str, Any]]:
    return _parse_window_transit(window_minutes, now, cutoff)["incoming_messages"]


def _collect_outgoing_messages(window_minutes: int, now: datetime, cutoff: datetime) -> list[dict[str, Any]]:
    return _parse_window_transit(window_minutes, now, cutoff)["outgoing_messages"]


QUEUE_TYPE_LABELS = {
    "active": "Attive",
    "deferred": "Differite",
    "hold": "In hold",
    "all": "Tutte le code",
    "blocked": "Bloccate",
    "incoming": "Ingresso",
    "outgoing": "In uscita",
}


def collect_queue_listing(queue_type: str = "active", window_minutes: int = 60) -> dict[str, Any]:
    queue_type = (queue_type or "active").lower()
    window_minutes = max(5, min(window_minutes, 24 * 60))
    now = _now()
    cutoff = now - timedelta(minutes=window_minutes)

    snapshot = read_queue_snapshot()
    queue_messages = snapshot["messages"]
    queue_ok = snapshot["source_available"]
    updated_at = snapshot.get("updated_at")

    if queue_type in {"active", "deferred", "hold"}:
        messages = _enrich_queue_messages(queue_messages.get(queue_type, []), now)
        return {
            "type": queue_type,
            "label": QUEUE_TYPE_LABELS[queue_type],
            "count": len(messages),
            "updated_at": updated_at,
            "window_minutes": window_minutes,
            "collected_at": now.isoformat(),
            "source_available": queue_ok,
            "messages": messages,
        }

    if queue_type == "all":
        combined: list[dict[str, Any]] = []
        for key in ("active", "deferred", "hold"):
            combined.extend(_enrich_queue_messages(queue_messages.get(key, []), now))
        combined.sort(key=lambda row: row.get("age_seconds") is None or -(row.get("age_seconds") or 0))
        return {
            "type": "all",
            "label": QUEUE_TYPE_LABELS["all"],
            "count": len(combined),
            "updated_at": updated_at,
            "window_minutes": window_minutes,
            "collected_at": now.isoformat(),
            "source_available": queue_ok,
            "messages": combined,
        }

    transit = _parse_window_transit(window_minutes, now, cutoff)

    if queue_type == "blocked":
        messages = transit["blocked_messages"]
        return {
            "type": "blocked",
            "label": QUEUE_TYPE_LABELS["blocked"],
            "count": len(messages),
            "updated_at": None,
            "window_minutes": window_minutes,
            "collected_at": now.isoformat(),
            "source_available": (LOGS_DIR / "postfix.log").is_file()
            or (LOGS_DIR / "amavis.log").is_file(),
            "messages": messages,
        }

    if queue_type == "incoming":
        messages = transit["incoming_messages"]
        return {
            "type": "incoming",
            "label": QUEUE_TYPE_LABELS["incoming"],
            "count": len(messages),
            "updated_at": None,
            "window_minutes": window_minutes,
            "collected_at": now.isoformat(),
            "source_available": (LOGS_DIR / "postfix.log").is_file(),
            "messages": messages,
        }

    if queue_type == "outgoing":
        messages = transit["outgoing_messages"]
        return {
            "type": "outgoing",
            "label": QUEUE_TYPE_LABELS["outgoing"],
            "count": len(messages),
            "updated_at": None,
            "window_minutes": window_minutes,
            "collected_at": now.isoformat(),
            "source_available": (LOGS_DIR / "postfix.log").is_file(),
            "messages": messages,
        }

    return {
        "type": queue_type,
        "label": queue_type,
        "count": 0,
        "updated_at": updated_at,
        "window_minutes": window_minutes,
        "collected_at": now.isoformat(),
        "source_available": False,
        "messages": [],
        "error": "Tipo non supportato. Usa active, deferred, hold, all, blocked, incoming o outgoing.",
    }


def _in_window(line: str, ref: datetime, cutoff: datetime) -> bool:
    ts = _line_ts(line, ref)
    if ts is None:
        return False
    return ts >= cutoff


def collect_traffic_stats(window_minutes: int = 60) -> dict[str, Any]:
    window_minutes = max(5, min(window_minutes, 24 * 60))
    now = _now()
    cutoff = now - timedelta(minutes=window_minutes)

    postfix_log = LOGS_DIR / "postfix.log"
    amavis_log = LOGS_DIR / "amavis.log"

    transit = _parse_window_transit(window_minutes, now, cutoff)
    snapshot = read_pipeline_snapshot()
    pipeline = snapshot.get("pipeline") or _empty_pipeline()
    queue_detail = {
        "active": snapshot["active"],
        "deferred": snapshot["deferred"],
        "hold": snapshot["hold"],
    }
    in_transit = (
        pipeline.get("postfix_active", 0)
        + pipeline.get("postfix_to_amavis", 0)
        + pipeline.get("postfix_outbound", 0)
        + pipeline.get("postfix_deferred", 0)
        + pipeline.get("postfix_hold", 0)
        + pipeline.get("amavis", 0)
        + pipeline.get("clamav", 0)
        + pipeline.get("spamassassin", 0)
    )

    return {
        "window_minutes": window_minutes,
        "collected_at": now.isoformat(),
        "ingresso": len(transit["incoming_ids"]),
        "in_coda": in_transit,
        "bloccate": len(transit["blocked_keys"]),
        "in_uscita": len(transit["outgoing_ids"]),
        "pipeline": pipeline,
        "metric_scope": {
            "ingresso": "window",
            "in_coda": "live",
            "bloccate": "window",
            "in_uscita": "window",
        },
        "sources": {
            "postfix_log": postfix_log.is_file(),
            "amavis_log": amavis_log.is_file(),
            "queue_snapshot": snapshot["source_available"],
        },
        "queue_detail": queue_detail,
    }
