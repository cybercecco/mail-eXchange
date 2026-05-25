"""Collect mail traffic counters from shared Postfix/Amavis logs and queue snapshot."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

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
POSTFIX_BLOCKED = re.compile(
    r"postfix/(?:smtpd|smtp|qmgr|cleanup)\[\d+\]:\s+"
    r"(?:NOQUEUE:\s+)?(?:(?:milter-)?reject|warning):\s|"
    r"postfix/smtp\[\d+\]:\s+(?P<qid>[A-F0-9]+):.*status=bounced",
    re.I,
)
POSTFIX_REJECT_DETAIL = re.compile(
    r"(?:NOQUEUE:\s+)?(?:(?:milter-)?reject|warning):\s(?P<reason>[^;]+)(?:;\s+from=<(?P<from>[^>]*)>)?"
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
AMAVIS_BLOCKED = re.compile(
    r"\)\s+Blocked\s+(?P<reason>SPAM|INFECT(?:ED)?|BAD\s+HEADER|NAME|BANNED|BLACKLISTED|"
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


def _now() -> datetime:
    return datetime.now(timezone.utc)


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
    try:
        ts = datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
    except ValueError:
        return None
    if ts > ref + timedelta(hours=2):
        ts = ts.replace(year=year - 1)
    return ts


def _parse_amavis_ts(line: str) -> datetime | None:
    match = AMAVIS_TS.match(line)
    if not match:
        return None
    time_part = match.group("time").replace(",", ".")
    try:
        return datetime.fromisoformat(f"{match.group('date')}T{time_part}").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def _line_ts(line: str, ref: datetime) -> datetime | None:
    return _parse_amavis_ts(line) or _parse_postfix_ts(line, ref)


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


def _is_outgoing_relay(detail: str) -> bool:
    lowered = detail.lower()
    if "amavis" in lowered:
        return False
    if "127.0.0.1" in lowered and "10025" in lowered:
        return False
    if "[127.0.0.1" in lowered:
        return False
    return True


def read_queue_snapshot() -> dict[str, Any]:
    """Return the live Postfix queue snapshot written by mx-postfix watch_queue."""
    empty: dict[str, Any] = {
        "total": 0,
        "active": 0,
        "deferred": 0,
        "hold": 0,
        "updated_at": None,
        "messages": {"active": [], "deferred": [], "hold": []},
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
    return {
        "total": int(payload.get("total", 0)),
        "active": int(payload.get("active", 0)),
        "deferred": int(payload.get("deferred", 0)),
        "hold": int(payload.get("hold", 0)),
        "updated_at": payload.get("updated_at"),
        "messages": {
            "active": list(raw_messages.get("active") or []),
            "deferred": list(raw_messages.get("deferred") or []),
            "hold": list(raw_messages.get("hold") or []),
        },
        "source_available": True,
        "collected_at": _now().isoformat(),
    }


def _read_queue_snapshot() -> tuple[int, dict[str, int], bool, dict[str, Any]]:
    empty_messages = {"active": [], "deferred": [], "hold": []}
    if not QUEUE_SNAPSHOT.is_file():
        return 0, {"active": 0, "deferred": 0, "hold": 0}, False, empty_messages
    try:
        payload = json.loads(QUEUE_SNAPSHOT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0, {"active": 0, "deferred": 0, "hold": 0}, False, empty_messages
    active = int(payload.get("active", 0))
    deferred = int(payload.get("deferred", 0))
    hold = int(payload.get("hold", 0))
    total = int(payload.get("total", active + deferred + hold))
    raw_messages = payload.get("messages") or {}
    messages = {
        "active": list(raw_messages.get("active") or []),
        "deferred": list(raw_messages.get("deferred") or []),
        "hold": list(raw_messages.get("hold") or []),
    }
    return total, {"active": active, "deferred": deferred, "hold": hold}, True, messages


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
    try:
        ts = datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
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


def _extract_blocked_from_line(line: str) -> dict[str, str] | None:
    reject = POSTFIX_REJECT_DETAIL.search(line)
    if reject:
        return {
            "from": reject.group("from") or "",
            "to": reject.group("to") or "",
            "reason": (reject.group("reason") or "").strip(),
        }
    bounced = POSTFIX_BOUNCED.search(line)
    if bounced:
        return {
            "queue_id": bounced.group("qid") or "",
            "from": "",
            "to": "",
            "reason": (bounced.group("reason") or "").strip(),
        }
    return None


def _collect_blocked_messages(window_minutes: int, now: datetime, cutoff: datetime) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    postfix_log = LOGS_DIR / "postfix.log"
    amavis_log = LOGS_DIR / "amavis.log"

    for line in _tail_lines(postfix_log):
        if not _in_window(line, now, cutoff):
            continue
        if not POSTFIX_BLOCKED.search(line):
            continue
        detail = _extract_blocked_from_line(line)
        ts = _line_ts(line, now)
        from_addr = detail["from"] if detail else ""
        to_addr = detail["to"] if detail else ""
        reason = detail["reason"] if detail else "blocked"
        if not from_addr:
            from_match = POSTFIX_FROM.search(line)
            from_addr = from_match.group("from") if from_match else ""
        if not to_addr:
            to_match = POSTFIX_TO.search(line)
            to_addr = to_match.group("to") if to_match else ""
        messages.append(
            {
                "timestamp": ts.isoformat() if ts else None,
                "source": "postfix",
                "queue_id": detail.get("queue_id", "") if detail else "",
                "from": from_addr,
                "to": [to_addr] if to_addr else [],
                "reason": reason,
                "summary": line.strip()[-240:],
            }
        )

    for line in _tail_lines(amavis_log):
        if not _in_window(line, now, cutoff):
            continue
        blocked = AMAVIS_BLOCKED.search(line)
        if not blocked:
            continue
        ts = _line_ts(line, now)
        route = AMAVIS_ROUTE.search(line)
        messages.append(
            {
                "timestamp": ts.isoformat() if ts else None,
                "source": "amavis",
                "queue_id": "",
                "from": route.group("from") if route else "",
                "to": [route.group("to")] if route else [],
                "reason": f"Blocked {blocked.group('reason')}",
                "summary": line.strip()[-240:],
            }
        )

    messages.sort(key=lambda row: row.get("timestamp") or "", reverse=True)
    return messages


def _collect_incoming_messages(window_minutes: int, now: datetime, cutoff: datetime) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in _tail_lines(LOGS_DIR / "postfix.log"):
        if not _in_window(line, now, cutoff):
            continue
        incoming = POSTFIX_INCOMING.search(line)
        if not incoming:
            continue
        qid = incoming.group("qid")
        if qid in seen:
            continue
        seen.add(qid)
        ts = _line_ts(line, now)
        client = POSTFIX_CLIENT.search(line)
        messages.append(
            {
                "timestamp": ts.isoformat() if ts else None,
                "source": "postfix",
                "queue_id": qid,
                "from": "",
                "to": [],
                "client": client.group("client").strip() if client else "",
                "summary": line.strip()[-240:],
            }
        )
    messages.sort(key=lambda row: row.get("timestamp") or "", reverse=True)
    return messages


def _collect_outgoing_messages(window_minutes: int, now: datetime, cutoff: datetime) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for line in _tail_lines(LOGS_DIR / "postfix.log"):
        if not _in_window(line, now, cutoff):
            continue
        sent = POSTFIX_SENT.search(line)
        if not sent or not _is_outgoing_relay(sent.group("detail")):
            continue
        ts = _line_ts(line, now)
        from_match = POSTFIX_FROM.search(line)
        to_match = POSTFIX_TO.search(line)
        messages.append(
            {
                "timestamp": ts.isoformat() if ts else None,
                "source": "postfix",
                "queue_id": sent.group("qid"),
                "from": from_match.group("from") if from_match else "",
                "to": [to_match.group("to")] if to_match else [],
                "reason": sent.group("detail").strip(),
                "summary": line.strip()[-240:],
            }
        )
    messages.sort(key=lambda row: row.get("timestamp") or "", reverse=True)
    return messages


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

    _, queue_detail, queue_ok, queue_messages = _read_queue_snapshot()
    updated_at = None
    if QUEUE_SNAPSHOT.is_file():
        try:
            updated_at = json.loads(QUEUE_SNAPSHOT.read_text(encoding="utf-8")).get("updated_at")
        except (OSError, json.JSONDecodeError):
            updated_at = None

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

    if queue_type == "blocked":
        messages = _collect_blocked_messages(window_minutes, now, cutoff)
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
        messages = _collect_incoming_messages(window_minutes, now, cutoff)
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
        messages = _collect_outgoing_messages(window_minutes, now, cutoff)
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
        return True
    return ts >= cutoff


def collect_traffic_stats(window_minutes: int = 60) -> dict[str, Any]:
    window_minutes = max(5, min(window_minutes, 24 * 60))
    now = _now()
    cutoff = now - timedelta(minutes=window_minutes)

    postfix_log = LOGS_DIR / "postfix.log"
    amavis_log = LOGS_DIR / "amavis.log"

    incoming_ids: set[str] = set()
    outgoing_count = 0
    postfix_blocked = 0

    for line in _tail_lines(postfix_log):
        if not _in_window(line, now, cutoff):
            continue
        incoming = POSTFIX_INCOMING.search(line)
        if incoming:
            incoming_ids.add(incoming.group("qid"))
            continue
        sent = POSTFIX_SENT.search(line)
        if sent and _is_outgoing_relay(sent.group("detail")):
            outgoing_count += 1
            continue
        if POSTFIX_BLOCKED.search(line):
            postfix_blocked += 1

    amavis_blocked = 0
    for line in _tail_lines(amavis_log):
        if not _in_window(line, now, cutoff):
            continue
        if AMAVIS_BLOCKED.search(line):
            amavis_blocked += 1

    in_coda, queue_detail, queue_ok, _queue_messages = _read_queue_snapshot()

    return {
        "window_minutes": window_minutes,
        "collected_at": now.isoformat(),
        "ingresso": len(incoming_ids),
        "in_coda": in_coda,
        "bloccate": postfix_blocked + amavis_blocked,
        "in_uscita": outgoing_count,
        "sources": {
            "postfix_log": postfix_log.is_file(),
            "amavis_log": amavis_log.is_file(),
            "queue_snapshot": queue_ok,
        },
        "queue_detail": queue_detail,
    }
