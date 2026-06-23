"""Encrypt relay passwords for SASL regeneration (bcrypt is not reversible)."""

import base64
import hashlib
import os

_SALT = b"mail-exchange-relay-sasl-v1"


def _derive_key() -> bytes:
    secret = os.environ.get("JWT_SECRET", "dev-insecure-relay")
    return hashlib.pbkdf2_hmac("sha256", secret.encode(), _SALT, 100_000, dklen=32)


def encrypt_relay_password(plain: str) -> str:
    key = _derive_key()
    data = plain.encode("utf-8")
    enc = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
    return base64.urlsafe_b64encode(enc).decode("ascii")


def decrypt_relay_password(enc: str) -> str:
    key = _derive_key()
    data = base64.urlsafe_b64decode(enc.encode("ascii"))
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data)).decode("utf-8")
