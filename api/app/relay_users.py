from typing import Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field

from app.auth import hash_password
from app.db import db
from app.relay_password import encrypt_relay_password


class RelayUserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9._-]+$")
    password: str = Field(min_length=8, max_length=128)
    enabled: bool = True


class RelayUserUpdate(BaseModel):
    password: Optional[str] = Field(default=None, min_length=8, max_length=128)
    enabled: Optional[bool] = None


def _row_to_public(row) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "enabled": bool(row["enabled"]),
        "created_at": row["created_at"],
    }


def list_relay_users() -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT id, username, enabled, created_at
            FROM relay_users
            ORDER BY username COLLATE NOCASE
            """
        ).fetchall()
    return [_row_to_public(row) for row in rows]


def create_relay_user(payload: RelayUserCreate) -> dict:
    username = payload.username.strip()
    with db() as conn:
        existing = conn.execute(
            "SELECT id FROM relay_users WHERE username = ? COLLATE NOCASE",
            (username,),
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="Relay username already exists")
        cursor = conn.execute(
            """
            INSERT INTO relay_users (username, password_hash, password_enc, enabled)
            VALUES (?, ?, ?, ?)
            """,
            (
                username,
                hash_password(payload.password),
                encrypt_relay_password(payload.password),
                int(payload.enabled),
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id, username, enabled, created_at FROM relay_users WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
    return _row_to_public(row)


def update_relay_user(user_id: int, payload: RelayUserUpdate) -> dict:
    with db() as conn:
        row = conn.execute(
            "SELECT id, username, password_hash, password_enc, enabled, created_at FROM relay_users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Relay user not found")

        password_hash = row["password_hash"]
        password_enc = row["password_enc"]
        if payload.password:
            password_hash = hash_password(payload.password)
            password_enc = encrypt_relay_password(payload.password)

        enabled = row["enabled"] if payload.enabled is None else int(payload.enabled)

        conn.execute(
            """
            UPDATE relay_users
            SET password_hash = ?, password_enc = ?, enabled = ?
            WHERE id = ?
            """,
            (password_hash, password_enc, enabled, user_id),
        )
        conn.commit()
        updated = conn.execute(
            "SELECT id, username, enabled, created_at FROM relay_users WHERE id = ?",
            (user_id,),
        ).fetchone()
    return _row_to_public(updated)


def delete_relay_user(user_id: int) -> dict:
    with db() as conn:
        row = conn.execute("SELECT id FROM relay_users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Relay user not found")
        conn.execute("DELETE FROM relay_users WHERE id = ?", (user_id,))
        conn.commit()
    return {"status": "deleted"}
