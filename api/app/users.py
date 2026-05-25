from typing import Literal, Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field

from app.auth import hash_password, user_row_to_public
from app.db import db


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9._-]+$")
    password: str = Field(min_length=8, max_length=128)
    role: Literal["admin", "user"] = "user"
    notify_email: str = Field(default="", max_length=254)


class UserUpdate(BaseModel):
    role: Optional[Literal["admin", "user"]] = None
    password: Optional[str] = Field(default=None, min_length=8, max_length=128)
    reset_mfa: bool = False
    notify_email: Optional[str] = Field(default=None, max_length=254)


def _get_user_row(user_id: int):
    with db() as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def _count_admins(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS c FROM users WHERE role = 'admin'"
    ).fetchone()["c"]


def list_users() -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT id, username, role, mfa_enabled, notify_email, created_at
            FROM users
            ORDER BY username COLLATE NOCASE
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _normalize_notify_email(value: str | None) -> str | None:
    email = (value or "").strip().lower()
    if not email:
        return None
    if "@" not in email:
        raise HTTPException(status_code=400, detail="Invalid notify email")
    return email


def create_user(payload: UserCreate) -> dict:
    username = payload.username.strip()
    notify_email = _normalize_notify_email(payload.notify_email)
    with db() as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
            (username,),
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="Username already exists")
        cursor = conn.execute(
            """
            INSERT INTO users (username, password_hash, role, totp_secret, mfa_enabled, notify_email)
            VALUES (?, ?, ?, NULL, 0, ?)
            """,
            (username, hash_password(payload.password), payload.role, notify_email),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return user_row_to_public(row)


def update_user(user_id: int, payload: UserUpdate, actor: dict) -> dict:
    row = _get_user_row(user_id)
    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    new_role = payload.role if payload.role is not None else row["role"]

    with db() as conn:
        if row["role"] == "admin" and new_role != "admin":
            if _count_admins(conn) <= 1:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot demote the last admin user",
                )

        if payload.password:
            password_hash = hash_password(payload.password)
        else:
            password_hash = row["password_hash"]

        totp_secret = row["totp_secret"]
        mfa_enabled = row["mfa_enabled"]
        if payload.reset_mfa:
            totp_secret = None
            mfa_enabled = 0

        notify_email = row["notify_email"]
        if payload.notify_email is not None:
            notify_email = _normalize_notify_email(payload.notify_email)

        conn.execute(
            """
            UPDATE users
            SET role = ?, password_hash = ?, totp_secret = ?, mfa_enabled = ?, notify_email = ?
            WHERE id = ?
            """,
            (new_role, password_hash, totp_secret, int(mfa_enabled), notify_email, user_id),
        )
        conn.commit()
        updated = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    return user_row_to_public(updated)


def delete_user(user_id: int, actor: dict) -> dict:
    row = _get_user_row(user_id)
    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    if row["id"] == actor["id"]:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")

    with db() as conn:
        if row["role"] == "admin" and _count_admins(conn) <= 1:
            raise HTTPException(
                status_code=400,
                detail="Cannot delete the last admin user",
            )
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()

    return {"status": "deleted"}
