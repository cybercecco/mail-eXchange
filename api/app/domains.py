import re
import sqlite3
from typing import Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field

from app.db import DEFAULT_DKIM_SELECTOR, db
from app.relay_ips import (
    normalize_relay_source_ips,
    relay_source_ips_from_db,
    relay_source_ips_to_db,
)

DOMAIN_PATTERN = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$",
    re.IGNORECASE,
)


class DomainCreate(BaseModel):
    name: str = Field(min_length=3, max_length=253)
    enabled: bool = True
    dkim_selector: str = Field(default="mail", min_length=1, max_length=63)
    sibling_fqdn: Optional[str] = Field(default=None, max_length=253)
    relay_all_inbound: bool = False
    relay_source_ips: list[str] = Field(default_factory=list)


class DomainUpdate(BaseModel):
    enabled: Optional[bool] = None
    dkim_selector: Optional[str] = Field(default=None, min_length=1, max_length=63)
    sibling_fqdn: Optional[str] = Field(default=None, max_length=253)
    relay_all_inbound: Optional[bool] = None
    relay_source_ips: Optional[list[str]] = None


def normalize_domain(name: str) -> str:
    return name.strip().lower()


def validate_domain_name(name: str) -> str:
    normalized = normalize_domain(name)
    if not DOMAIN_PATTERN.match(normalized):
        raise HTTPException(status_code=400, detail="Invalid domain name")
    return normalized


def get_domain(domain_id: int) -> sqlite3.Row:
    with db() as conn:
        row = conn.execute("SELECT * FROM domains WHERE id = ?", (domain_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Domain not found")
    return row


def list_domains() -> list[dict]:
    from app.domain_destinations import list_all_destinations_by_domain

    with db() as conn:
        rows = conn.execute(
            """
            SELECT d.id, d.name, d.enabled, d.dkim_selector, d.sibling_fqdn,
                   d.relay_all_inbound, d.relay_source_ips, d.updated_at, d.created_at,
                   COUNT(m.id) AS mailbox_count
            FROM domains d
            LEFT JOIN mailboxes m ON m.domain_id = d.id
            GROUP BY d.id
            ORDER BY d.name
            """
        ).fetchall()
    destinations_by_domain = list_all_destinations_by_domain()
    result = []
    for row in rows:
        item = dict(row)
        item["destinations"] = destinations_by_domain.get(row["id"], [])
        item["relay_source_ips"] = relay_source_ips_from_db(row["relay_source_ips"])
        result.append(item)
    return result


def create_domain(payload: DomainCreate) -> dict:
    from app.sync import normalize_sibling_fqdn

    name = validate_domain_name(payload.name)
    selector = (payload.dkim_selector or DEFAULT_DKIM_SELECTOR).strip()
    sibling_fqdn = normalize_sibling_fqdn(payload.sibling_fqdn)
    relay_ips = normalize_relay_source_ips(payload.relay_source_ips)
    relay_ips_db = relay_source_ips_to_db(relay_ips)
    with db() as conn:
        try:
            cursor = conn.execute(
                """
                INSERT INTO domains (name, enabled, dkim_selector, sibling_fqdn,
                                     relay_all_inbound, relay_source_ips)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    int(payload.enabled),
                    selector,
                    sibling_fqdn,
                    int(payload.relay_all_inbound),
                    relay_ips_db,
                ),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="Domain already exists") from exc
    return {"id": cursor.lastrowid, "name": name, "sibling_fqdn": sibling_fqdn}


def update_domain(domain_id: int, payload: DomainUpdate) -> dict:
    from app.sync import normalize_sibling_fqdn

    row = get_domain(domain_id)
    enabled = int(payload.enabled) if payload.enabled is not None else row["enabled"]
    selector = payload.dkim_selector or row["dkim_selector"]
    if "sibling_fqdn" in payload.model_fields_set:
        sibling_fqdn = normalize_sibling_fqdn(payload.sibling_fqdn)
    else:
        sibling_fqdn = row["sibling_fqdn"]
    relay_all_inbound = (
        int(payload.relay_all_inbound)
        if payload.relay_all_inbound is not None
        else int(row["relay_all_inbound"])
    )
    if "relay_source_ips" in payload.model_fields_set:
        relay_ips = normalize_relay_source_ips(payload.relay_source_ips)
        relay_ips_db = relay_source_ips_to_db(relay_ips)
    else:
        relay_ips_db = row["relay_source_ips"]
    with db() as conn:
        conn.execute(
            """
            UPDATE domains
            SET enabled = ?, dkim_selector = ?, sibling_fqdn = ?,
                relay_all_inbound = ?, relay_source_ips = ?,
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (
                enabled,
                selector.strip(),
                sibling_fqdn,
                relay_all_inbound,
                relay_ips_db,
                domain_id,
            ),
        )
        conn.commit()
    return {"status": "updated", "sibling_fqdn": sibling_fqdn}


def delete_domain(domain_id: int) -> dict:
    get_domain(domain_id)
    with db() as conn:
        mailbox_count = conn.execute(
            "SELECT COUNT(*) AS c FROM mailboxes WHERE domain_id = ?",
            (domain_id,),
        ).fetchone()["c"]
        if mailbox_count:
            raise HTTPException(
                status_code=409,
                detail="Remove all mailboxes for this domain before deleting it",
            )
        conn.execute("DELETE FROM domains WHERE id = ?", (domain_id,))
        conn.commit()
    return {"status": "deleted"}


def email_belongs_to_domain(email: str, domain_name: str) -> bool:
    _, _, domain = email.partition("@")
    return domain.lower() == domain_name.lower()


def resolve_domain_for_mailbox(
    email: str, domain_id: Optional[int] = None
) -> tuple[int, str]:
    email = email.strip().lower()
    _, _, email_domain = email.partition("@")
    if not email_domain:
        raise HTTPException(status_code=400, detail="Invalid email address")

    if domain_id is not None:
        row = get_domain(domain_id)
        if not email_belongs_to_domain(email, row["name"]):
            raise HTTPException(
                status_code=400,
                detail=f"Email must belong to domain {row['name']}",
            )
        return domain_id, row["name"]

    with db() as conn:
        row = conn.execute(
            "SELECT id, name FROM domains WHERE name = ? COLLATE NOCASE AND enabled = 1",
            (email_domain.lower(),),
        ).fetchone()
    if not row:
        raise HTTPException(
            status_code=400,
            detail=f"No enabled domain configured for {email_domain}",
        )
    return row["id"], row["name"]
