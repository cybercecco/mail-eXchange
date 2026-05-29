"""Push sync of domain bundle (mailboxes, DKIM, MX hints) to a sibling Mail Exchange server."""

from __future__ import annotations

import json
import logging
import os
import re
import ssl
import urllib.error
import urllib.request
from typing import Any, Optional

from fastapi import HTTPException, status
from pydantic import BaseModel, Field

from app.db import db
from app.dkim_keys import (
    format_dkim_dns_txt,
    install_dkim_key_pair,
    read_dkim_private_key_pem,
    read_dkim_public_key_base64,
)
from app.domains import validate_domain_name
from app.regenerate import regenerate_files

logger = logging.getLogger(__name__)

_DEPRECATED_ENV_SYNC_SECRET = os.environ.get("SYNC_SHARED_SECRET", "").strip()
_DEPRECATED_ENV_WARNED = False
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


class DomainSyncBlock(BaseModel):
    dkim_selector: str = Field(default="mail", min_length=1, max_length=63)
    dkim_private_key_pem: Optional[str] = None
    dkim_public_key_dns_txt: Optional[str] = None


class MxRecord(BaseModel):
    priority: int = Field(default=10, ge=0, le=65535)
    host: str = Field(min_length=1, max_length=253)


class SyncDomainBundlePayload(BaseModel):
    domain_name: str
    mailboxes: list[dict[str, Any]] = Field(default_factory=list)
    domain_sync: Optional[DomainSyncBlock] = None
    mx_records: list[MxRecord] = Field(default_factory=list)


# Backward-compatible alias for existing imports/tests.
SyncMailboxesPayload = SyncDomainBundlePayload


def _deprecated_env_sync_secret() -> str:
    global _DEPRECATED_ENV_WARNED
    if _DEPRECATED_ENV_SYNC_SECRET and not _DEPRECATED_ENV_WARNED:
        logger.warning(
            "SYNC_SHARED_SECRET in .env is deprecated; configure sync_secret per domain "
            "in the Cluster tab"
        )
        _DEPRECATED_ENV_WARNED = True
    return _DEPRECATED_ENV_SYNC_SECRET


def _sync_secret_from_row(raw: str | None) -> Optional[str]:
    normalized = (raw or "").strip()
    return normalized or None


def resolve_sync_secret_for_domain_id(domain_id: int) -> Optional[str]:
    with db() as conn:
        row = conn.execute(
            "SELECT sync_secret FROM domains WHERE id = ?",
            (domain_id,),
        ).fetchone()
    if not row:
        return None
    secret = _sync_secret_from_row(row["sync_secret"])
    if secret:
        return secret
    return _deprecated_env_sync_secret() or None


def resolve_sync_secret_for_domain_name(domain_name: str) -> Optional[str]:
    normalized = validate_domain_name(domain_name)
    with db() as conn:
        row = conn.execute(
            "SELECT sync_secret FROM domains WHERE name = ? COLLATE NOCASE",
            (normalized,),
        ).fetchone()
    if row:
        secret = _sync_secret_from_row(row["sync_secret"])
        if secret:
            return secret
    return _deprecated_env_sync_secret() or None


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


def build_mx_records_for_sync() -> list[dict[str, Any]]:
    """MX records this node expects in public DNS for synced domains."""
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for priority, host in ((10, POSTFIX_HOSTNAME), (20, PUBLIC_HOSTNAME)):
        normalized = (host or "").strip().lower().rstrip(".")
        if not normalized or normalized in seen:
            continue
        if not HOSTNAME_PATTERN.match(normalized):
            continue
        records.append({"priority": priority, "host": normalized})
        seen.add(normalized)
    return records


def build_domain_sync_block(domain_name: str, dkim_selector: str) -> dict[str, Any]:
    selector = (dkim_selector or "mail").strip()
    block: dict[str, Any] = {"dkim_selector": selector}
    private_pem = read_dkim_private_key_pem(domain_name, selector)
    if private_pem:
        block["dkim_private_key_pem"] = private_pem
    pubkey = read_dkim_public_key_base64(domain_name)
    if pubkey:
        block["dkim_public_key_dns_txt"] = format_dkim_dns_txt(pubkey)
    return block


def _normalize_destination_label(label: str | None) -> str:
    return (label or "").strip()


def _lookup_label_for_mailbox_route(
    conn,
    domain_id: int,
    destination_host: str,
    destination_port: int,
) -> str | None:
    host = destination_host.strip().lower()
    port = int(destination_port)
    row = conn.execute(
        """
        SELECT label FROM domain_destinations
        WHERE domain_id = ? AND host = ? AND port = ?
        ORDER BY id
        LIMIT 1
        """,
        (domain_id, host, port),
    ).fetchone()
    if not row:
        return None
    label = _normalize_destination_label(row["label"])
    return label or None


def _lookup_local_destination_by_label(
    conn,
    domain_id: int,
    label: str,
) -> dict[str, Any] | None:
    normalized = _normalize_destination_label(label)
    if not normalized:
        return None
    row = conn.execute(
        """
        SELECT id, host, port, label
        FROM domain_destinations
        WHERE domain_id = ? AND lower(trim(label)) = lower(trim(?))
        ORDER BY id
        LIMIT 1
        """,
        (domain_id, normalized),
    ).fetchone()
    return dict(row) if row else None


def build_domain_sync_payload(domain_id: int) -> Optional[dict[str, Any]]:
    """Build full domain bundle sync payload. Returns None if domain missing or no sibling."""
    with db() as conn:
        row = conn.execute(
            "SELECT name, dkim_selector, sibling_fqdn FROM domains WHERE id = ?",
            (domain_id,),
        ).fetchone()
        if not row:
            return None
        sibling = (row["sibling_fqdn"] or "").strip()
        if not sibling:
            return None
        mailbox_rows = conn.execute(
            """
            SELECT email, destination_host, destination_port, enabled
            FROM mailboxes
            WHERE domain_id = ?
            ORDER BY email
            """,
            (domain_id,),
        ).fetchall()
        mailboxes: list[dict[str, Any]] = []
        for mailbox in mailbox_rows:
            entry = dict(mailbox)
            label = _lookup_label_for_mailbox_route(
                conn,
                domain_id,
                entry["destination_host"],
                entry["destination_port"],
            )
            if label:
                entry["destination_label"] = label
            mailboxes.append(entry)

    domain_name = row["name"]
    return {
        "domain_name": domain_name,
        "mailboxes": mailboxes,
        "domain_sync": build_domain_sync_block(domain_name, row["dkim_selector"]),
        "mx_records": build_mx_records_for_sync(),
    }


def build_mailbox_sync_payload(domain_id: int) -> Optional[dict[str, Any]]:
    """Backward-compatible alias."""
    return build_domain_sync_payload(domain_id)


def verify_sync_auth(domain_name: str, authorization: Optional[str]) -> None:
    expected = resolve_sync_secret_for_domain_name(domain_name)
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Chiave precondivisa sync non configurata per questo dominio",
        )
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing sync authorization",
        )
    token = authorization[7:].strip()
    if token != expected:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid sync authorization",
        )


def _sync_url(sibling_fqdn: str) -> str:
    return f"https://{sibling_fqdn}:{SYNC_HTTPS_PORT}/api/sync/domain-bundle"


def _http_post_json(
    url: str, payload: dict[str, Any], bearer_token: str
) -> tuple[int, dict[str, Any] | str]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {bearer_token}",
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
    Push domain bundle snapshot to sibling server.
    Returns a warning string on failure, None if sync skipped or succeeded.
    """
    sibling = normalize_sibling_fqdn(_sibling_for_domain(domain_id))
    if not sibling:
        return None
    if is_self_sync_target(sibling):
        return f"Sync saltato: il Server Cluster coincide con questo host ({sibling})"

    sync_secret = resolve_sync_secret_for_domain_id(domain_id)
    if not sync_secret:
        return "Chiave precondivisa sync non configurata: sync disabilitato"

    payload = build_domain_sync_payload(domain_id)
    if payload is None:
        return None

    url = _sync_url(sibling)
    try:
        status_code, body = _http_post_json(url, payload, sync_secret)
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


def domain_has_cluster_peer(domain_id: int) -> bool:
    sibling = normalize_sibling_fqdn(_sibling_for_domain(domain_id))
    return bool(sibling and not is_self_sync_target(sibling))


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


def merge_mx_hints(existing_json: str | None, incoming: list[dict[str, Any]]) -> str:
    by_host: dict[str, dict[str, Any]] = {}
    if existing_json:
        try:
            for item in json.loads(existing_json):
                host = str(item.get("host") or "").strip().lower().rstrip(".")
                if host:
                    by_host[host] = {
                        "priority": int(item.get("priority") or 10),
                        "host": host,
                    }
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    for item in incoming:
        host = str(item.get("host") or "").strip().lower().rstrip(".")
        if not host:
            continue
        by_host[host] = {
            "priority": int(item.get("priority") or 10),
            "host": host,
        }
    merged = sorted(by_host.values(), key=lambda row: (row["priority"], row["host"]))
    return json.dumps(merged)


def _apply_domain_sync_metadata(
    conn,
    domain_id: int,
    domain_sync: DomainSyncBlock | None,
    mx_records: list[MxRecord],
) -> None:
    if domain_sync is not None:
        selector = (domain_sync.dkim_selector or "mail").strip()
        conn.execute(
            "UPDATE domains SET dkim_selector = ? WHERE id = ?",
            (selector, domain_id),
        )
        if domain_sync.dkim_private_key_pem:
            row = conn.execute(
                "SELECT name FROM domains WHERE id = ?", (domain_id,)
            ).fetchone()
            install_dkim_key_pair(
                row["name"],
                selector,
                domain_sync.dkim_private_key_pem,
                domain_sync.dkim_public_key_dns_txt,
            )

    if mx_records:
        row = conn.execute(
            "SELECT dns_mx_hints FROM domains WHERE id = ?", (domain_id,)
        ).fetchone()
        incoming = [record.model_dump() for record in mx_records]
        merged = merge_mx_hints(row["dns_mx_hints"], incoming)
        conn.execute(
            "UPDATE domains SET dns_mx_hints = ? WHERE id = ?",
            (merged, domain_id),
        )


def _resolve_incoming_mailbox_route(
    conn,
    domain_id: int,
    mailbox: dict[str, Any],
) -> tuple[str, int] | None:
    """
    Map an incoming sync mailbox to local host/port.
    Prefer destination_label (cluster-safe); fall back to destination_host for legacy payloads.
    """
    destination_label = str(mailbox.get("destination_label") or "").strip()
    if destination_label:
        local = _lookup_local_destination_by_label(conn, domain_id, destination_label)
        if not local:
            return None
        return local["host"], int(local["port"])

    destination_host = str(mailbox.get("destination_host") or "").strip()
    if not destination_host:
        return None
    return destination_host, int(mailbox.get("destination_port") or 25)


def apply_incoming_domain_sync(payload: SyncDomainBundlePayload) -> dict[str, Any]:
    """
    Apply a pushed domain bundle on this server (receiver).
    Mailboxes are upserted/deleted; domain_sync updates dkim_selector and DKIM keys.
    sibling_fqdn, enabled, destinations and relay settings stay local.
    mx_records are merged into dns_mx_hints for the DNS sub-tab.
    """
    domain_name = validate_domain_name(payload.domain_name)
    incoming_emails = {
        str(mailbox["email"]).strip().lower()
        for mailbox in payload.mailboxes
        if mailbox.get("email")
    }
    warnings: list[str] = []

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

            destination_label = str(mailbox.get("destination_label") or "").strip()
            route = _resolve_incoming_mailbox_route(conn, domain_id, mailbox)
            if route is None:
                if destination_label:
                    warnings.append(
                        f"Cassetta {email}: destinazione con etichetta "
                        f"'{destination_label}' non trovata in locale; sync saltata"
                    )
                else:
                    warnings.append(
                        f"Cassetta {email}: destinazione mancante o non valida; sync saltata"
                    )
                continue

            destination_host, destination_port = route
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

        _apply_domain_sync_metadata(conn, domain_id, payload.domain_sync, payload.mx_records)
        conn.commit()

    regenerate_files()
    result: dict[str, Any] = {"status": "applied"}
    if warnings:
        result["warnings"] = warnings
    return result


def apply_incoming_mailbox_sync(payload: SyncDomainBundlePayload) -> dict[str, str]:
    """Backward-compatible alias."""
    return apply_incoming_domain_sync(payload)


def attach_sync_warning(result: dict, domain_id: int) -> dict:
    if not domain_has_cluster_peer(domain_id):
        return result
    warning = push_to_sibling(domain_id)
    if warning:
        return {**result, "sync_warning": warning}
    return result
