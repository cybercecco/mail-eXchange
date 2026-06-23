import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import db as db_module
from app import mail_config as mail_config_module
from app.auth import hash_password, verify_password
from app.relay_password import decrypt_relay_password, encrypt_relay_password
from app.relay_users import (
    RelayUserCreate,
    RelayUserUpdate,
    create_relay_user,
    delete_relay_user,
    list_relay_users,
    update_relay_user,
)
from app.regenerate import regenerate_files, write_relay_sasl_passwd


class RelayPasswordTest(unittest.TestCase):
    def test_encrypt_decrypt_roundtrip(self) -> None:
        with patch.dict(os.environ, {"JWT_SECRET": "test-secret-for-relay"}):
            enc = encrypt_relay_password("mobile-pass-123")
            self.assertEqual(decrypt_relay_password(enc), "mobile-pass-123")


class RelayUsersApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        data_dir = Path(self.tmp.name)
        self.patches = [
            patch.object(db_module, "DATA_DIR", data_dir),
            patch.object(db_module, "DB_PATH", data_dir / "mailrouter.db"),
            patch.object(db_module, "GENERATED_DIR", data_dir / "generated"),
            patch.dict(os.environ, {"JWT_SECRET": "test-secret-for-relay"}),
        ]
        for item in self.patches:
            item.start()
        db_module.init_db()

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def test_create_list_update_delete(self) -> None:
        created = create_relay_user(
            RelayUserCreate(username="mobile1", password="secretpass", enabled=True)
        )
        self.assertEqual(created["username"], "mobile1")
        self.assertTrue(created["enabled"])

        users = list_relay_users()
        self.assertEqual(len(users), 1)
        self.assertEqual(users[0]["username"], "mobile1")

        updated = update_relay_user(
            created["id"],
            RelayUserUpdate(password="newsecretpass", enabled=False),
        )
        self.assertFalse(updated["enabled"])

        with db_module.db() as conn:
            row = conn.execute(
                "SELECT password_hash, password_enc FROM relay_users WHERE id = ?",
                (created["id"],),
            ).fetchone()
        self.assertTrue(verify_password("newsecretpass", row["password_hash"]))
        self.assertEqual(decrypt_relay_password(row["password_enc"]), "newsecretpass")

        delete_relay_user(created["id"])
        self.assertEqual(list_relay_users(), [])


class RelaySaslRegenerateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        data_dir = Path(self.tmp.name)
        self.data_dir = data_dir
        self.generated_dir = data_dir / "generated"
        mail_cfg = data_dir / "mail-config"
        self.postfix_generated = mail_cfg / "postfix" / "generated"
        self.db_path = data_dir / "mailrouter.db"
        self.patches = [
            patch.object(db_module, "DATA_DIR", data_dir),
            patch.object(db_module, "DB_PATH", self.db_path),
            patch.object(db_module, "GENERATED_DIR", self.generated_dir),
            patch.object(mail_config_module, "MAIL_CONFIG_DIR", mail_cfg),
            patch.object(mail_config_module, "POSTFIX_GENERATED_DIR", self.postfix_generated),
            patch.object(
                mail_config_module,
                "SPAMASSASSIN_LOCAL_CF",
                mail_cfg / "spamassassin" / "local.cf",
            ),
            patch.object(
                mail_config_module,
                "AMAVIS_SPAM_OVERRIDES",
                mail_cfg / "amavis" / "spam-overrides.conf",
            ),
            patch("app.regenerate.GENERATED_DIR", self.generated_dir),
            patch("app.regenerate.POSTFIX_GENERATED_DIR", self.postfix_generated),
            patch("app.regenerate.SPAMASSASSIN_LOCAL_CF", mail_cfg / "spamassassin" / "local.cf"),
            patch("app.regenerate.AMAVIS_SPAM_OVERRIDES", mail_cfg / "amavis" / "spam-overrides.conf"),
            patch("app.regenerate.OPENDKIM_DIR", self.generated_dir / "opendkim"),
            patch("app.regenerate.DKIM_PUB_DIR", self.generated_dir / "dkim"),
            patch("app.regenerate.write_caddyfile"),
            patch("app.regenerate.write_docker_dns_compose_override"),
            patch.dict(os.environ, {"JWT_SECRET": "test-secret-for-relay"}),
        ]
        for item in self.patches:
            item.start()
        db_module.init_db()

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def test_write_relay_sasl_passwd_enabled_only(self) -> None:
        with db_module.db() as conn:
            conn.execute(
                """
                INSERT INTO relay_users (username, password_hash, password_enc, enabled)
                VALUES (?, ?, ?, ?)
                """,
                (
                    "mobile1",
                    hash_password("pass-one"),
                    encrypt_relay_password("pass-one"),
                    1,
                ),
            )
            conn.execute(
                """
                INSERT INTO relay_users (username, password_hash, password_enc, enabled)
                VALUES (?, ?, ?, ?)
                """,
                (
                    "disabled-user",
                    hash_password("pass-two"),
                    encrypt_relay_password("pass-two"),
                    0,
                ),
            )
            conn.commit()

        write_relay_sasl_passwd()
        content = (self.data_dir / "sasl" / "relay_passwd").read_text(encoding="utf-8")
        self.assertIn("mobile1:pass-one", content)
        self.assertNotIn("disabled-user", content)
        self.assertTrue(content.startswith("# Generated by Mail Exchange"))

    def test_regenerate_files_writes_relay_passwd(self) -> None:
        with db_module.db() as conn:
            conn.execute(
                """
                INSERT INTO relay_users (username, password_hash, password_enc, enabled)
                VALUES (?, ?, ?, 1)
                """,
                (
                    "relaytest",
                    hash_password("relay-secret"),
                    encrypt_relay_password("relay-secret"),
                ),
            )
            conn.commit()

        regenerate_files()
        content = (self.data_dir / "sasl" / "relay_passwd").read_text(encoding="utf-8")
        self.assertIn("relaytest:relay-secret", content)


if __name__ == "__main__":
    unittest.main()
