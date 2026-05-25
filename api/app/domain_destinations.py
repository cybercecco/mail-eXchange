import sqlite3

from fastapi import HTTPException
from pydantic import BaseModel, Field

from app.domains import get_domain
from app.db import db
from app.sync import touch_domain_updated_at


class DestinationCreate(BaseModel):
    label: str = Field(default="", max_length=120)
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=25, ge=1, le=65535)


class DestinationUpdate(BaseModel):
    label: str | None = Field(default=None, max_length=120)
    host: str | None = Field(default=None, min_length=1, max_length=253)
    port: int | None = Field(default=None, ge=1, le=65535)


def _normalize_host(host: str) -> str:
    return host.strip().lower()


def list_destinations_for_domain(domain_id: int) -> list[dict]:
    get_domain(domain_id)
    with db() as conn:
        rows = conn.execute(
            """
            SELECT id, domain_id, label, host, port
            FROM domain_destinations
            WHERE domain_id = ?
            ORDER BY label, host, port
            """,
            (domain_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_all_destinations_by_domain() -> dict[int, list[dict]]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT id, domain_id, label, host, port
            FROM domain_destinations
            ORDER BY domain_id, label, host, port
            """
        ).fetchall()
    grouped: dict[int, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["domain_id"], []).append(dict(row))
    return grouped


def create_destination(domain_id: int, payload: DestinationCreate) -> dict:
    get_domain(domain_id)
    host = _normalize_host(payload.host)
    label = (payload.label or host).strip()
    with db() as conn:
        try:
            cursor = conn.execute(
                """
                INSERT INTO domain_destinations (domain_id, label, host, port)
                VALUES (?, ?, ?, ?)
                """,
                (domain_id, label, host, int(payload.port)),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise HTTPException(
                status_code=409,
                detail="This destination server already exists for the domain",
            ) from exc
    touch_domain_updated_at(domain_id)
    return {"id": cursor.lastrowid, "domain_id": domain_id, "label": label, "host": host, "port": payload.port}


def update_destination(
    domain_id: int, destination_id: int, payload: DestinationUpdate
) -> dict:
    get_domain(domain_id)
    with db() as conn:
        row = conn.execute(
            """
            SELECT * FROM domain_destinations
            WHERE id = ? AND domain_id = ?
            """,
            (destination_id, domain_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Destination not found")
        old_host = row["host"]
        old_port = int(row["port"])
        label = payload.label if payload.label is not None else row["label"]
        host = _normalize_host(payload.host) if payload.host is not None else old_host
        port = int(payload.port) if payload.port is not None else old_port
        if not host:
            raise HTTPException(status_code=400, detail="Host is required")
        label = (label or host).strip() or host
        routing_changed = host != old_host or port != old_port
        try:
            conn.execute(
                """
                UPDATE domain_destinations
                SET label = ?, host = ?, port = ?
                WHERE id = ? AND domain_id = ?
                """,
                (label, host, port, destination_id, domain_id),
            )
            mailboxes_updated = 0
            if routing_changed:
                cursor = conn.execute(
                    """
                    UPDATE mailboxes
                    SET destination_host = ?, destination_port = ?
                    WHERE domain_id = ?
                      AND destination_port = ?
                      AND lower(trim(destination_host)) = ?
                    """,
                    (host, port, domain_id, old_port, old_host),
                )
                mailboxes_updated = cursor.rowcount
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise HTTPException(
                status_code=409,
                detail="This destination server already exists for the domain",
            ) from exc
    touch_domain_updated_at(domain_id)
    return {
        "status": "updated",
        "id": destination_id,
        "domain_id": domain_id,
        "label": label,
        "host": host,
        "port": port,
        "mailboxes_updated": mailboxes_updated,
    }


def delete_destination(domain_id: int, destination_id: int) -> dict:
    get_domain(domain_id)
    with db() as conn:
        row = conn.execute(
            "SELECT id FROM domain_destinations WHERE id = ? AND domain_id = ?",
            (destination_id, domain_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Destination not found")
        conn.execute(
            "DELETE FROM domain_destinations WHERE id = ? AND domain_id = ?",
            (destination_id, domain_id),
        )
        conn.commit()
    touch_domain_updated_at(domain_id)
    return {"status": "deleted"}


def resolve_destination_for_mailbox(
    domain_id: int, destination_host: str, destination_port: int
) -> None:
    host = _normalize_host(destination_host)
    port = int(destination_port)
    with db() as conn:
        match = conn.execute(
            """
            SELECT id FROM domain_destinations
            WHERE domain_id = ? AND host = ? AND port = ?
            """,
            (domain_id, host, port),
        ).fetchone()
    if not match:
        raise HTTPException(
            status_code=400,
            detail="Select a destination server configured for this domain",
        )
