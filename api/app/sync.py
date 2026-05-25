"""Push sync of mailbox lists to a sibling Mail Exchange server."""

from __future__ import annotations

import json
import logging
import os
import re
import ssl
import urllib.error
import urllib.request
from typing import Annotated, Any, Optional

from fastapi import HTTPException, Header, status
from pydantic import BaseModel, Field

from app.db import db
from app.domains import validate_domain_name
from app.regenerate import regenerate_files

logger = logging.getLogger(__name__)

SYNC_SHARED_SECRET = os.environ.get("SYNC_SHARED_SECRET", "").strip()
SYNC_TLS_VERIFY = os.environ.get("SYNC_TLS_VERIFY", "true").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
SYNC_HTTPS_PORT = int(os.environ.get("SYNC_HTTPS_PORT", "60443").strip() or "60443")
PUBLIC_HOSTNAME = os.environ.get("PUBLIC_HOSTNAME", "").strip().lower()
CADDY_DOMAIN = os.environ.get("CADDY_DOMAIN", "").strip().lower()
POSTFIX_HOSTNAME = os.environ.get("POSTFIX_HOSTNAME", "").strip().lower()

HOSTNAME_PATTERN = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$",
    re.IGNORECASE,
)


class SyncMailboxesPayload(BaseModel):
    domain_name: str
    mailboxes: list[dict[str, Any]] = Field(default_factory=list)


def normalize_sibling_fqdn(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    if not HOSTNAME_PATTERN.match(normalized):
        raise HTTPException(status_code=400, detail="Invalid sibling server FQDN")
    return normalized


def touch_domain_updated_at(domain_id: int) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE domains SET updated_at = datetime('now') WHERE id = ?",
            (domain_id,),
        )
        conn.commit()


def _local_hostnames() -> set[str]:
    return {h for h in (PUBLIC_HOSTNAME, CADDY_DOMAIN, POSTFIX_HOSTNAME) if h}


def is_self_sync_target(sibling_fqdn: str) -> bool:
    return sibling_fqdn.lower() in _local_hostnames()


def build_mailbox_sync_payload(domain_id: int) -> Optional[dict[str, Any]]:
    """Build mailbox-only sync payload. Returns None if domain missing or no sibling configured."""
    with db() as conn:
        row = conn.execute("SELECT name, sibling_fqdn FROM domains WHERE id = ?", (domain_id,)).fetchone()
        if not row:
            return None
        sibling = (row["sibling_fqdn"] or "").strip()
        if not sibling:
            return None
        mailboxes = conn.execute(
            """
            SELECT email, destination_host, destination_port, enabled
            FROM mailboxes
            WHERE domain_id = ?
            ORDER BY email
            """,
            (domain_id,),
        ).fetchall()

    return {
        "domain_name": row["name"],
        "mailboxes": [dict(m) for m in mailboxes],
    }


def verify_sync_secret(
    authorization: Annotated[Optional[str], Header(alias="Authorization")] = None,
) -> None:
    if not SYNC_SHARED_SECRET:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SYNC_SHARED_SECRET is not configured",
        )
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing sync authorization",
        )
    token = authorization[7:].strip()
    if token != SYNC_SHARED_SECRET:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid sync authorization",
        )


def _sync_url(sibling_fqdn: str) -> str:
    return f"https://{sibling_fqdn}:{SYNC_HTTPS_PORT}/api/sync/mailboxes"


def _http_post_json(url: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any] | str]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {SYNC_SHARED_SECRET}",
        },
        method="POST",
    )
    context = ssl.create_default_context()
    if not SYNC_TLS_VERIFY:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(request, context=context, timeout=30) as response:
            raw = response.read().decode("utf-8")
            if not raw:
                return response.status, {}
            try:
                return response.status, json.loads(raw)
            except json.JSONDecodeError:
                return response.status, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(raw)
        except json.JSONDecodeError:
            detail = raw or exc.reason
        return exc.code, detail
    except urllib.error.URLError as exc:
        raise ConnectionError(str(exc.reason or exc)) from exc


def push_to_sibling(domain_id: int) -> Optional[str]:
    """
    Push mailbox snapshot to sibling server.
    Returns a warning string on failure, None if sync skipped or succeeded.
    """
    if not SYNC_SHARED_SECRET:
        return "SYNC_SHARED_SECRET non configurato: sync saltato"

    sibling = normalize_sibling_fqdn(_sibling_for_domain(domain_id))
    if not sibling:
        return None
    if is_self_sync_target(sibling):
        return f"Sync saltato: il Server Cluster coincide con questo host ({sibling})"

    payload = build_mailbox_sync_payload(domain_id)
    if payload is None:
        return None

    url = _sync_url(sibling)
    try:
        status_code, body = _http_post_json(url, payload)
    except ConnectionError as exc:
        logger.warning("Sibling sync failed for domain %s: %s", domain_id, exc)
        return f"Server Cluster {sibling} non raggiungibile: {exc}"

    if status_code >= 400:
        detail = body.get("detail", body) if isinstance(body, dict) else body
        logger.warning(
            "Sibling sync rejected for domain %s (%s): %s", domain_id, status_code, detail
        )
        return f"Sync rifiutato da {sibling} ({status_code}): {detail}"

    return None


def _sibling_for_domain(domain_id: int) -> Optional[str]:
    with db() as conn:
        row = conn.execute(
            "SELECT sibling_fqdn FROM domains WHERE id = ?", (domain_id,)
        ).fetchone()
    if not row:
        return None
    return row["sibling_fqdn"]


def _ensure_domain_id(conn, domain_name: str) -> int:
    existing = conn.execute(
        "SELECT id FROM domains WHERE name = ? COLLATE NOCASE",
        (domain_name,),
    ).fetchone()
    if existing:
        return existing["id"]
    cursor = conn.execute(
        """
        INSERT INTO domains (name, enabled, dkim_selector, updated_at)
        VALUES (?, 1, 'mail', datetime('now'))
        """,
        (domain_name,),
    )
    return cursor.lastrowid


def apply_incoming_mailbox_sync(payload: SyncMailboxesPayload) -> dict[str, str]:
    """
    Apply a pushed mailbox snapshot on this server (receiver).
    Only mailboxes are upserted/deleted; domain settings and destinations are untouched.
    Creates the domain row if missing (name only, default enabled/dkim).
    """
    domain_name = validate_domain_name(payload.domain_name)
    incoming_emails = {
        str(mailbox["email"]).strip().lower()
        for mailbox in payload.mailboxes
        if mailbox.get("email")
    }

    with db() as conn:
        domain_id = _ensure_domain_id(conn, domain_name)

        existing_rows = conn.execute(
            "SELECT email FROM mailboxes WHERE domain_id = ?",
            (domain_id,),
        ).fetchall()
        existing_emails = {row["email"] for row in existing_rows}

        for mailbox in payload.mailboxes:
            email = str(mailbox.get("email") or "").strip().lower()
            if not email:
                continue
            destination_host = str(mailbox.get("destination_host") or "").strip()
            if not destination_host:
                continue
            destination_port = int(mailbox.get("destination_port") or 25)
            enabled = int(bool(mailbox.get("enabled", True)))
            if email in existing_emails:
                conn.execute(
                    """
                    UPDATE mailboxes
                    SET destination_host = ?, destination_port = ?, enabled = ?
                    WHERE domain_id = ? AND email = ?
                    """,
                    (destination_host, destination_port, enabled, domain_id, email),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO mailboxes (email, destination_host, destination_port, enabled, domain_id)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (email, destination_host, destination_port, enabled, domain_id),
                )

        for email in existing_emails - incoming_emails:
            conn.execute(
                "DELETE FROM mailboxes WHERE domain_id = ? AND email = ?",
                (domain_id, email),
            )

        conn.commit()

    regenerate_files()
    return {"status": "applied"}


def attach_sync_warning(result: dict, domain_id: int) -> dict:
    warning = push_to_sibling(domain_id)
    if warning:
        return {**result, "sync_warning": warning}
    return result
