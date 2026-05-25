"""Collect mail stack errors from shared logs and email platform users."""

from __future__ import annotations

import hashlib
import json
import os
import re
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from app.db import DATA_DIR, db
from app.mail_test import (
    POSTFIX_SMTP_HOST,
    POSTFIX_SMTP_PORT,
    POSTFIX_SMTP_TIMEOUT,
    prepare_smtp_session,
)
from app.traffic_stats import _line_ts

LOGS_DIR = DATA_DIR / "logs"
STATE_PATH = DATA_DIR / "stats" / "error_digest_state.json"
MAIL_DOMAIN = os.environ.get("MAIL_DOMAIN", "local")
POSTFIX_HOSTNAME = os.environ.get("POSTFIX_HOSTNAME", "mx.local")

ERROR_WINDOW_MINUTES = 30

# Guasti / malfunzionamenti reali — esclusi warning generici Postfix/Amavis.
FAILURE_PATTERNS = [
    re.compile(r"\b(?:error|fatal|panic|crit(?:ical)?)\b", re.I),
    re.compile(r"(?:NOQUEUE:\s+)?(?:(?:milter-)?reject):", re.I),
    re.compile(r"status=(?:bounced|deferred)", re.I),
    re.compile(r"\bBlocked\s+(?:SPAM|INFECT)", re.I),
    re.compile(r"clamd.*(?:ERROR|FAILED)", re.I),
    re.compile(r"opendkim.*(?:error|fail)", re.I),
]

SOURCE_LABELS = {
    "postfix.log": "Postfix",
    "amavis.log": "Amavis",
    "clamav.log": "ClamAV",
    "opendkim.log": "OpenDKIM",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _tail_lines(path: Path, max_bytes: int = 256_000) -> list[str]:
    if not path.is_file():
        return []
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > max_bytes:
            handle.seek(size - max_bytes)
            handle.readline()
        data = handle.read().decode("utf-8", errors="replace")
    return data.splitlines()


def _is_failure_line(line: str) -> bool:
    stripped = line.strip()
    if len(stripped) < 12:
        return False
    return any(pattern.search(stripped) for pattern in FAILURE_PATTERNS)


def collect_error_lines(
    max_lines: int = 80,
    window_minutes: int = ERROR_WINDOW_MINUTES,
) -> list[dict[str, str]]:
    now = _now()
    cutoff = now - timedelta(minutes=window_minutes)
    entries: list[dict[str, Any]] = []
    for name in ("postfix.log", "amavis.log", "clamav.log", "opendkim.log"):
        path = LOGS_DIR / name
        for line in _tail_lines(path):
            if not _is_failure_line(line):
                continue
            ts = _line_ts(line, now)
            if ts is None or ts < cutoff:
                continue
            entries.append(
                {
                    "source": SOURCE_LABELS.get(name, name),
                    "line": line.strip()[-500:],
                    "_ts": ts,
                }
            )
    entries.sort(key=lambda item: item["_ts"])
    trimmed = entries[-max_lines:]
    return [
        {"source": item["source"], "line": item["line"]}
        for item in trimmed
    ]


def _digest_fingerprint(entries: list[dict[str, str]]) -> str:
    payload = "\n".join(f"{e['source']}|{e['line']}" for e in entries)
    return hashlib.sha256(payload.encode()).hexdigest()


def _load_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state), encoding="utf-8")


def list_notify_recipients() -> list[dict[str, str]]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT id, username, notify_email
            FROM users
            WHERE notify_email IS NOT NULL AND TRIM(notify_email) != ''
            ORDER BY username COLLATE NOCASE
            """
        ).fetchall()
    return [
        {"id": row["id"], "username": row["username"], "email": row["notify_email"].strip()}
        for row in rows
    ]


def send_error_digest(*, force: bool = False) -> dict[str, Any]:
    entries = collect_error_lines()
    recipients = list_notify_recipients()
    now = _now()

    if not entries:
        return {
            "status": "skipped",
            "reason": "no_errors",
            "message": (
                f"Nessun guasto rilevato negli ultimi {ERROR_WINDOW_MINUTES} minuti."
            ),
            "recipients": len(recipients),
            "sent": 0,
            "window_minutes": ERROR_WINDOW_MINUTES,
        }

    if not recipients:
        return {
            "status": "skipped",
            "reason": "no_recipients",
            "error_count": len(entries),
            "sent": 0,
        }

    fingerprint = _digest_fingerprint(entries)
    state = _load_state()
    if not force and state.get("last_fingerprint") == fingerprint:
        return {
            "status": "skipped",
            "reason": "unchanged",
            "recipients": len(recipients),
            "error_count": len(entries),
            "sent": 0,
        }

    body_lines = [
        "Report guasti Mail Exchange",
        f"Host: {POSTFIX_HOSTNAME}",
        f"Generato: {now.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"Finestra: ultimi {ERROR_WINDOW_MINUTES} minuti",
        f"Guasti rilevati: {len(entries)}",
        "",
    ]
    for item in entries:
        body_lines.append(f"[{item['source']}] {item['line']}")
    body = "\n".join(body_lines)

    sender = f"mx-alerts@{MAIL_DOMAIN}"
    subject = (
        f"[Mail Exchange] {len(entries)} guasti (ultimi {ERROR_WINDOW_MINUTES} min) "
        f"su {POSTFIX_HOSTNAME}"
    )
    sent = 0
    errors: list[str] = []

    try:
        with smtplib.SMTP(POSTFIX_SMTP_HOST, POSTFIX_SMTP_PORT, timeout=POSTFIX_SMTP_TIMEOUT) as smtp:
            prepare_smtp_session(smtp)
            for recipient in recipients:
                message = EmailMessage()
                message["From"] = sender
                message["To"] = recipient["email"]
                message["Subject"] = subject
                message.set_content(body)
                try:
                    smtp.send_message(message)
                    sent += 1
                except smtplib.SMTPException as exc:
                    errors.append(f"{recipient['email']}: {exc}")
    except (OSError, smtplib.SMTPException) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    _save_state(
        {
            "last_fingerprint": fingerprint,
            "last_sent_at": now.isoformat(),
            "last_error_count": len(entries),
            "last_recipients": sent,
        }
    )

    return {
        "status": "sent" if sent else "failed",
        "recipients": len(recipients),
        "sent": sent,
        "error_count": len(entries),
        "window_minutes": ERROR_WINDOW_MINUTES,
        "errors": errors,
    }
