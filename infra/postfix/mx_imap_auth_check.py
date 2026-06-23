#!/usr/bin/env python3
"""PAM helper: verify SMTP AUTH credentials via IMAP LOGIN on the domain MDaemon."""

from __future__ import annotations

import imaplib
import json
import os
import sys
from pathlib import Path

DEFAULT_CONFIG = Path("/data/sasl/imap_auth.json")


def load_config(path: Path = DEFAULT_CONFIG) -> dict:
    if not path.is_file():
        return {"users": {}, "domains": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    users = data.get("users") or {}
    domains = data.get("domains") or {}
    return {
        "users": {str(k).strip().lower(): v for k, v in users.items()},
        "domains": {str(k).strip().lower(): v for k, v in domains.items()},
    }


def resolve_target(email: str, config: dict) -> dict | None:
    normalized = email.strip().lower()
    if "@" not in normalized:
        return None
    if normalized in config["users"]:
        return config["users"][normalized]
    domain = normalized.rsplit("@", 1)[1]
    return config["domains"].get(domain)


def verify_imap_login(host: str, port: int, use_ssl: bool, user: str, password: str) -> bool:
    if not host or not user or not password:
        return False
    client = None
    try:
        if use_ssl:
            client = imaplib.IMAP4_SSL(host, port, timeout=20)
        else:
            client = imaplib.IMAP4(host, port, timeout=20)
            client.starttls()
        client.login(user, password)
        return True
    except Exception:
        return False
    finally:
        if client is not None:
            try:
                client.logout()
            except Exception:
                pass


def authenticate(user: str, password: str, config_path: Path = DEFAULT_CONFIG) -> bool:
    target = resolve_target(user, load_config(config_path))
    if not target:
        return False
    host = str(target.get("host") or "").strip()
    port = int(target.get("port") or 993)
    use_ssl = bool(target.get("ssl", port == 993))
    return verify_imap_login(host, port, use_ssl, user.strip(), password)


def main() -> int:
    user = os.environ.get("PAM_USER", "").strip()
    password = os.environ.get("PAM_AUTHTOK", "")
    if not user or not password:
        return 1
    config_path = Path(os.environ.get("MX_IMAP_AUTH_CONFIG", str(DEFAULT_CONFIG)))
    return 0 if authenticate(user, password, config_path) else 1


if __name__ == "__main__":
    sys.exit(main())
