import json
import os
import sqlite3
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "mailrouter.db"
GENERATED_DIR = DATA_DIR / "generated"
MAIL_DOMAIN = os.environ.get("MAIL_DOMAIN", "example.com").lower()
DKIM_SELECTOR = os.environ.get("DKIM_SELECTOR", "mail")
DEFAULT_DKIM_SELECTOR = DKIM_SELECTOR


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row["name"] for row in rows}


def _migrate_legacy_mailboxes(conn: sqlite3.Connection) -> None:
    domain_count = conn.execute("SELECT COUNT(*) AS c FROM domains").fetchone()["c"]
    mailbox_count = conn.execute("SELECT COUNT(*) AS c FROM mailboxes").fetchone()["c"]
    if domain_count > 0 or mailbox_count == 0:
        return

    cursor = conn.execute(
        """
        INSERT INTO domains (name, enabled, dkim_selector)
        VALUES (?, 1, ?)
        """,
        (MAIL_DOMAIN, DEFAULT_DKIM_SELECTOR),
    )
    domain_id = cursor.lastrowid
    conn.execute(
        "UPDATE mailboxes SET domain_id = ? WHERE domain_id IS NULL",
        (domain_id,),
    )


def _backfill_mailbox_domains(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT id, email FROM mailboxes WHERE domain_id IS NULL"
    ).fetchall()
    for row in rows:
        _, _, domain_name = row["email"].partition("@")
        domain_name = domain_name.lower().strip()
        if not domain_name:
            continue
        existing = conn.execute(
            "SELECT id FROM domains WHERE name = ? COLLATE NOCASE",
            (domain_name,),
        ).fetchone()
        if existing:
            domain_id = existing["id"]
        else:
            cursor = conn.execute(
                """
                INSERT INTO domains (name, enabled, dkim_selector)
                VALUES (?, 1, ?)
                """,
                (domain_name, DEFAULT_DKIM_SELECTOR),
            )
            domain_id = cursor.lastrowid
        conn.execute(
            "UPDATE mailboxes SET domain_id = ? WHERE id = ?",
            (domain_id, row["id"]),
        )


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS domains (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL COLLATE NOCASE,
                enabled INTEGER NOT NULL DEFAULT 1,
                dkim_selector TEXT NOT NULL DEFAULT 'mail',
                sibling_fqdn TEXT,
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        domain_cols = _table_columns(conn, "domains")
        if "sibling_fqdn" not in domain_cols:
            conn.execute("ALTER TABLE domains ADD COLUMN sibling_fqdn TEXT")
        if "updated_at" not in domain_cols:
            # SQLite ALTER TABLE does not allow non-constant defaults like datetime('now').
            conn.execute("ALTER TABLE domains ADD COLUMN updated_at TEXT")
            conn.execute(
                "UPDATE domains SET updated_at = datetime('now') WHERE updated_at IS NULL"
            )
        if "relay_all_inbound" not in domain_cols:
            conn.execute(
                "ALTER TABLE domains ADD COLUMN relay_all_inbound INTEGER NOT NULL DEFAULT 0"
            )
        if "relay_source_ips" not in domain_cols:
            conn.execute("ALTER TABLE domains ADD COLUMN relay_source_ips TEXT")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mailboxes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                destination_host TEXT NOT NULL,
                destination_port INTEGER NOT NULL DEFAULT 25,
                enabled INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        mailbox_cols = _table_columns(conn, "mailboxes")
        if "domain_id" not in mailbox_cols:
            conn.execute(
                "ALTER TABLE mailboxes ADD COLUMN domain_id INTEGER REFERENCES domains(id)"
            )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS spam_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                json_payload TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO spam_settings (id, json_payload) VALUES (1, ?)",
            (json.dumps({"whitelist_from": [], "blacklist_from": [], "scores": {}}),),
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS system_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                json_payload TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO system_settings (id, json_payload) VALUES (1, ?)",
            (
                json.dumps(
                    {
                        "public_url": os.environ.get(
                            "CADDY_DOMAIN", os.environ.get("POSTFIX_HOSTNAME", "mx.local")
                        ),
                        "acme_email": os.environ.get("ACME_EMAIL", "admin@example.com"),
                        "docker_dns": [
                            ip.strip()
                            for ip in os.environ.get(
                                "DOCKER_DNS_SERVERS", "208.67.222.222 208.67.220.220"
                            ).replace(",", " ").split()
                            if ip.strip()
                        ]
                        or ["208.67.222.222", "208.67.220.220"],
                    }
                ),
            ),
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS domain_destinations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                domain_id INTEGER NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
                label TEXT NOT NULL DEFAULT '',
                host TEXT NOT NULL,
                port INTEGER NOT NULL DEFAULT 25,
                UNIQUE(domain_id, host, port)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'admin',
                totp_secret TEXT,
                mfa_enabled INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        user_cols = _table_columns(conn, "users")
        if "notify_email" not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN notify_email TEXT")
        _migrate_legacy_mailboxes(conn)
        _backfill_mailbox_domains(conn)
        conn.commit()
