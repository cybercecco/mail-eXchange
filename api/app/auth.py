import base64
import io
import os
from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional

import bcrypt
import jwt
import pyotp
import qrcode
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from app.db import db

JWT_SECRET = os.environ.get("JWT_SECRET", "")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = int(os.environ.get("JWT_EXPIRE_MINUTES", "480"))
MFA_TEMP_EXPIRE_MINUTES = int(os.environ.get("MFA_TEMP_EXPIRE_MINUTES", "5"))
MFA_ISSUER = os.environ.get("MFA_ISSUER", "Mail Exchange")

security = HTTPBearer(auto_error=False)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1)


class MfaVerifyRequest(BaseModel):
    temp_token: str
    code: str = Field(min_length=6, max_length=8)


class MfaConfirmRequest(BaseModel):
    code: str = Field(min_length=6, max_length=8)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8, max_length=128)


class ProfileNotifyEmailUpdate(BaseModel):
    notify_email: str = Field(default="", max_length=254)


class MfaDisableRequest(BaseModel):
    password: str = Field(min_length=1)
    code: str = Field(min_length=6, max_length=8)


def _require_jwt_secret() -> str:
    if not JWT_SECRET or JWT_SECRET == "change-me-in-production":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="JWT_SECRET is not configured",
        )
    return JWT_SECRET


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def _encode_token(payload: dict, expires_minutes: int) -> str:
    secret = _require_jwt_secret()
    data = {
        **payload,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=expires_minutes),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(data, secret, algorithm=JWT_ALGORITHM)


def _decode_token(token: str) -> dict:
    secret = _require_jwt_secret()
    try:
        return jwt.decode(token, secret, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from exc


def user_row_to_public(row) -> dict:
    notify_email = ""
    try:
        if row["notify_email"]:
            notify_email = str(row["notify_email"]).strip()
    except (KeyError, IndexError, TypeError):
        notify_email = ""
    return {
        "id": row["id"],
        "username": row["username"],
        "role": row["role"],
        "mfa_enabled": bool(row["mfa_enabled"]),
        "notify_email": notify_email,
    }


def get_user_by_username(username: str):
    with db() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
            (username.strip(),),
        ).fetchone()


def create_access_token(user_row) -> str:
    return _encode_token(
        {"sub": user_row["username"], "role": user_row["role"], "type": "access"},
        JWT_EXPIRE_MINUTES,
    )


def create_mfa_temp_token(user_row) -> str:
    return _encode_token(
        {"sub": user_row["username"], "type": "mfa_pending"},
        MFA_TEMP_EXPIRE_MINUTES,
    )


def login(username: str, password: str) -> dict:
    row = get_user_by_username(username)
    if not row or not verify_password(password, row["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    public = user_row_to_public(row)
    if row["mfa_enabled"]:
        return {
            "mfa_required": True,
            "temp_token": create_mfa_temp_token(row),
            "user": public,
        }
    return {
        "mfa_required": False,
        "access_token": create_access_token(row),
        "token_type": "bearer",
        "user": public,
    }


def verify_mfa(temp_token: str, code: str) -> dict:
    payload = _decode_token(temp_token)
    if payload.get("type") != "mfa_pending":
        raise HTTPException(status_code=401, detail="Invalid MFA session")
    row = get_user_by_username(payload["sub"])
    if not row or not row["mfa_enabled"] or not row["totp_secret"]:
        raise HTTPException(status_code=401, detail="MFA not configured")
    totp = pyotp.TOTP(row["totp_secret"])
    if not totp.verify(code.strip(), valid_window=1):
        raise HTTPException(status_code=401, detail="Invalid MFA code")
    return {
        "access_token": create_access_token(row),
        "token_type": "bearer",
        "user": user_row_to_public(row),
    }


def get_current_user(
    credentials: Annotated[
        Optional[HTTPAuthorizationCredentials], Depends(security)
    ] = None,
):
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = _decode_token(credentials.credentials)
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid token type")
    row = get_user_by_username(payload["sub"])
    if not row:
        raise HTTPException(status_code=401, detail="User not found")
    return user_row_to_public(row)


def require_admin(user: Annotated[dict, Depends(get_current_user)]) -> dict:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return user


def mfa_setup(user: dict) -> dict:
    row = get_user_by_username(user["username"])
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    secret = pyotp.random_base32()
    with db() as conn:
        conn.execute(
            "UPDATE users SET totp_secret = ?, mfa_enabled = 0 WHERE id = ?",
            (secret, row["id"]),
        )
        conn.commit()
    totp = pyotp.TOTP(secret)
    provisioning_uri = totp.provisioning_uri(
        name=row["username"],
        issuer_name=MFA_ISSUER,
    )
    qr = qrcode.QRCode(box_size=4, border=2)
    qr.add_data(provisioning_uri)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    qr_data_uri = (
        "data:image/png;base64,"
        + base64.b64encode(buffer.getvalue()).decode()
    )
    return {
        "secret": secret,
        "provisioning_uri": provisioning_uri,
        "qr_data_uri": qr_data_uri,
        "message": "Scan QR in authenticator app, then POST /api/auth/mfa/confirm with a code",
    }


def mfa_confirm(user: dict, code: str) -> dict:
    row = get_user_by_username(user["username"])
    if not row or not row["totp_secret"]:
        raise HTTPException(status_code=400, detail="Run MFA setup first")
    totp = pyotp.TOTP(row["totp_secret"])
    if not totp.verify(code.strip(), valid_window=1):
        raise HTTPException(status_code=400, detail="Invalid MFA code")
    with db() as conn:
        conn.execute(
            "UPDATE users SET mfa_enabled = 1 WHERE id = ?",
            (row["id"],),
        )
        conn.commit()
    return {"status": "mfa_enabled", "mfa_enabled": True}


def change_password(user: dict, current_password: str, new_password: str) -> dict:
    row = get_user_by_username(user["username"])
    if not row or not verify_password(current_password, row["password_hash"]):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if current_password == new_password:
        raise HTTPException(
            status_code=400,
            detail="New password must differ from current password",
        )
    with db() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(new_password), row["id"]),
        )
        conn.commit()
    return {"status": "password_updated"}


def update_notify_email(user: dict, notify_email: str) -> dict:
    email = notify_email.strip().lower()
    if email and "@" not in email:
        raise HTTPException(status_code=400, detail="Invalid email address")
    with db() as conn:
        conn.execute(
            "UPDATE users SET notify_email = ? WHERE id = ?",
            (email or None, user["id"]),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
    return {"status": "updated", "user": user_row_to_public(row)}


def mfa_disable(user: dict, password: str, code: str) -> dict:
    row = get_user_by_username(user["username"])
    if not row or not verify_password(password, row["password_hash"]):
        raise HTTPException(status_code=400, detail="Password is incorrect")
    if not row["mfa_enabled"] or not row["totp_secret"]:
        raise HTTPException(status_code=400, detail="MFA is not enabled")
    totp = pyotp.TOTP(row["totp_secret"])
    if not totp.verify(code.strip(), valid_window=1):
        raise HTTPException(status_code=400, detail="Invalid MFA code")
    with db() as conn:
        conn.execute(
            "UPDATE users SET mfa_enabled = 0, totp_secret = NULL WHERE id = ?",
            (row["id"],),
        )
        conn.commit()
    return {"status": "mfa_disabled", "mfa_enabled": False}


def bootstrap_admin_if_needed() -> None:
    admin_username = os.environ.get("ADMIN_USERNAME", "").strip()
    admin_password = os.environ.get("ADMIN_PASSWORD", "")
    if not admin_username or not admin_password:
        return
    with db() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        if count > 0:
            return
        conn.execute(
            """
            INSERT INTO users (username, password_hash, role, totp_secret, mfa_enabled)
            VALUES (?, ?, 'admin', NULL, 0)
            """,
            (admin_username, hash_password(admin_password)),
        )
        conn.commit()


def ensure_jwt_configured_for_protected_routes() -> None:
    if not JWT_SECRET:
        import logging

        logging.getLogger("uvicorn.error").warning(
            "JWT_SECRET is empty; protected API routes will reject requests"
        )
