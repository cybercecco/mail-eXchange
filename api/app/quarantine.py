"""File-based spam quarantine: ingest from Amavis, TTL purge, release via Postfix."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import smtplib
import uuid
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from app.db import DATA_DIR
from app.mail_test import POSTFIX_SMTP_HOST, POSTFIX_SMTP_PORT, POSTFIX_SMTP_TIMEOUT, prepare_smtp_session

logger = logging.getLogger(__name__)

QUARANTINE_DIR = DATA_DIR / "quarantine"
INCOMING_DIR = QUARANTINE_DIR / "incoming"
TTL_HOURS = int(os.environ.get("QUARANTINE_TTL_HOURS", "36"))
INGEST_INTERVAL_SECONDS = int(os.environ.get("QUARANTINE_INGEST_INTERVAL", "60"))

_HEADER_RECIPIENTS = (
    "x-envelope-to",
    "x-original-to",
    "delivered-to",
    "envelope-to",
    "x-rcpt-to",
)
_SPAM_SCORE_HEADERS = ("x-spam-score", "x-spam-level")


def ensure_quarantine_dirs() -> None:
    QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
    for sub in ("spam", "virus", "bad-header"):
        (INCOMING_DIR / sub).mkdir(parents=True, exist_ok=True)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_iso(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def _entry_dir(entry_id: str) -> Path:
    return QUARANTINE_DIR / entry_id


def _meta_path(entry_id: str) -> Path:
    return _entry_dir(entry_id) / "meta.json"


def _message_path(entry_id: str) -> Path:
    return _entry_dir(entry_id) / "message.eml"


def _header_values(msg, name: str) -> list[str]:
    values: list[str] = []
    for key, value in msg.items():
        if key.lower() == name.lower():
            values.append(str(value).strip())
    return values


def _extract_addresses(msg) -> tuple[str, list[str], str | None]:
    from_addrs = getaddresses(_header_values(msg, "From"))
    sender = from_addrs[0][1].strip().lower() if from_addrs else ""
    if not sender:
        return_path = _header_values(msg, "Return-Path")
        if return_path:
            rp = getaddresses(return_path)
            if rp:
                sender = rp[0][1].strip().lower()

    recipients: list[str] = []
    for header in _HEADER_RECIPIENTS:
        for value in _header_values(msg, header):
            for _, addr in getaddresses([value]):
                addr = addr.strip().lower()
                if addr and addr not in recipients:
                    recipients.append(addr)

    if not recipients:
        for header in ("To", "Cc", "Bcc"):
            for _, addr in getaddresses(_header_values(msg, header)):
                addr = addr.strip().lower()
                if addr and addr not in recipients:
                    recipients.append(addr)

    subject = msg.get("Subject", "").strip() or "(no subject)"
    return sender, recipients, subject


def _parse_message_date(msg) -> str:
    raw = msg.get("Date")
    if raw:
        try:
            return _iso(parsedate_to_datetime(raw).astimezone(timezone.utc))
        except (TypeError, ValueError, OverflowError):
            pass
    return _iso(_utcnow())


def _parse_spam_score(msg) -> float | None:
    for header in _SPAM_SCORE_HEADERS:
        for value in _header_values(msg, header):
            match = re.search(r"(-?\d+(?:\.\d+)?)", value)
            if match:
                try:
                    return float(match.group(1))
                except ValueError:
                    continue
    status = _header_values(msg, "X-Spam-Status")
    for value in status:
        match = re.search(r"score=(-?\d+(?:\.\d+)?)", value, re.I)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                continue
    return None


def _public_meta(meta: dict[str, Any]) -> dict[str, Any]:
    now = _utcnow()
    expires_at = _parse_iso(meta["expires_at"])
    remaining = max(0, int((expires_at - now).total_seconds()))
    return {
        **meta,
        "expires_in_seconds": remaining,
    }


def _load_meta(entry_id: str) -> dict[str, Any]:
    path = _meta_path(entry_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Quarantine entry not found")
    return json.loads(path.read_text(encoding="utf-8"))


def ingest_amavis_file(path: Path, *, reason: str) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    raw = path.read_bytes()
    if not raw.strip():
        path.unlink(missing_ok=True)
        return None

    msg = BytesParser(policy=policy.default).parsebytes(raw)
    sender, recipients, subject = _extract_addresses(msg)
    if not recipients:
        logger.warning("Quarantine ingest skipped (no recipients): %s", path)
        path.unlink(missing_ok=True)
        return None

    created = _utcnow()
    entry_id = uuid.uuid4().hex
    entry_dir = _entry_dir(entry_id)
    entry_dir.mkdir(parents=True, exist_ok=False)
    _message_path(entry_id).write_bytes(raw)

    meta = {
        "id": entry_id,
        "from": sender,
        "to": recipients,
        "subject": subject,
        "date": _parse_message_date(msg),
        "spam_score": _parse_spam_score(msg),
        "reason": reason,
        "created_at": _iso(created),
        "expires_at": _iso(created + timedelta(hours=TTL_HOURS)),
        "source_file": path.name,
    }
    _meta_path(entry_id).write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    path.unlink(missing_ok=True)
    return meta


def ingest_amavis_incoming() -> int:
    ensure_quarantine_dirs()
    reason_dirs = {
        "spam": "spam",
        "virus": "virus",
        "bad-header": "bad-header",
    }
    ingested = 0
    for subdir, reason in reason_dirs.items():
        base = INCOMING_DIR / subdir
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            try:
                if ingest_amavis_file(path, reason=reason):
                    ingested += 1
            except Exception:
                logger.exception("Failed to ingest quarantine file %s", path)
    return ingested


def purge_expired_entries() -> int:
    ensure_quarantine_dirs()
    now = _utcnow()
    removed = 0
    for child in QUARANTINE_DIR.iterdir():
        if not child.is_dir() or child.name == "incoming":
            continue
        meta_file = child / "meta.json"
        if not meta_file.is_file():
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
            continue
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            expires_at = _parse_iso(meta["expires_at"])
        except (json.JSONDecodeError, KeyError, ValueError):
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
            continue
        if expires_at <= now:
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
    return removed


def list_quarantine(
    *,
    from_filter: str = "",
    to_filter: str = "",
    query: str = "",
    limit: int = 200,
) -> dict[str, Any]:
    ensure_quarantine_dirs()
    from_q = from_filter.strip().lower()
    to_q = to_filter.strip().lower()
    text_q = query.strip().lower()
    items: list[dict[str, Any]] = []

    for child in sorted(QUARANTINE_DIR.iterdir(), key=lambda p: p.name, reverse=True):
        if not child.is_dir() or child.name == "incoming":
            continue
        meta_file = child / "meta.json"
        if not meta_file.is_file():
            continue
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if from_q and from_q not in (meta.get("from") or "").lower():
            continue
        if to_q:
            recipients = [r.lower() for r in meta.get("to") or []]
            if not any(to_q in recipient for recipient in recipients):
                continue
        if text_q:
            haystack = " ".join(
                [
                    meta.get("subject") or "",
                    meta.get("from") or "",
                    " ".join(meta.get("to") or []),
                    meta.get("reason") or "",
                ]
            ).lower()
            if text_q not in haystack:
                continue
        items.append(_public_meta(meta))
        if len(items) >= limit:
            break

    items.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return {"items": items, "count": len(items), "ttl_hours": TTL_HOURS}


def get_quarantine(entry_id: str) -> dict[str, Any]:
    meta = _load_meta(entry_id)
    msg_path = _message_path(entry_id)
    if not msg_path.is_file():
        raise HTTPException(status_code=404, detail="Quarantine message missing")
    raw = msg_path.read_bytes()
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    headers = [{"name": key, "value": value} for key, value in msg.items()]
    return {
        **_public_meta(meta),
        "headers": headers,
        "size_bytes": len(raw),
    }


def delete_quarantine(entry_id: str) -> dict[str, str]:
    entry_dir = _entry_dir(entry_id)
    if not entry_dir.is_dir():
        raise HTTPException(status_code=404, detail="Quarantine entry not found")
    shutil.rmtree(entry_dir, ignore_errors=True)
    return {"status": "deleted", "id": entry_id}


def release_quarantine(entry_id: str) -> dict[str, Any]:
    meta = _load_meta(entry_id)
    msg_path = _message_path(entry_id)
    if not msg_path.is_file():
        raise HTTPException(status_code=404, detail="Quarantine message missing")

    raw = msg_path.read_bytes()
    recipients = meta.get("to") or []
    if not recipients:
        raise HTTPException(status_code=400, detail="No recipient stored for release")

    envelope_from = meta.get("from") or ""
    try:
        with smtplib.SMTP(
            POSTFIX_SMTP_HOST, POSTFIX_SMTP_PORT, timeout=POSTFIX_SMTP_TIMEOUT
        ) as smtp:
            prepare_smtp_session(smtp)
            smtp.sendmail(envelope_from, recipients, raw)
    except (OSError, smtplib.SMTPException) as exc:
        raise HTTPException(status_code=502, detail=f"Release failed: {exc}") from exc

    delete_quarantine(entry_id)
    return {
        "status": "released",
        "id": entry_id,
        "to": recipients,
        "from": envelope_from,
    }


def quarantine_worker_once() -> None:
    try:
        ingested = ingest_amavis_incoming()
        removed = purge_expired_entries()
        if ingested or removed:
            logger.info("Quarantine worker: ingested=%s purged=%s", ingested, removed)
    except Exception:
        logger.exception("Quarantine worker failed")
