import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import db as db_module
from app import mail_config as mail_config_module
from app.imap_auth import DEFAULT_IMAP_AUTH_PORT, build_imap_auth_config, resolve_imap_target
from app.regenerate import regenerate_files, write_imap_auth_config


class ImapTargetTest(unittest.TestCase):
    def test_defaults_to_smtp_host_and_993(self) -> None:
        target = resolve_imap_target("mdaemon.example.com", 25, None, None)
        self.assertEqual(target["host"], "mdaemon.example.com")
        self.assertEqual(target["port"], DEFAULT_IMAP_AUTH_PORT)
        self.assertTrue(target["ssl"])

    def test_overrides(self) -> None:
        target = resolve_imap_target("smtp.example.com", 25, "imap.example.com", 143)
        self.assertEqual(target["host"], "imap.example.com")
        self.assertEqual(target["port"], 143)
        self.assertFalse(target["ssl"])


class ImapAuthConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        data_dir = Path(self.tmp.name)
        self.data_dir = data_dir
        self.patches = [
            patch.object(db_module, "DATA_DIR", data_dir),
            patch.object(db_module, "DB_PATH", data_dir / "mailrouter.db"),
            patch.object(db_module, "GENERATED_DIR", data_dir / "generated"),
        ]
        for item in self.patches:
            item.start()
        db_module.init_db()

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def test_build_config_for_catch_all_and_mailbox(self) -> None:
        with db_module.db() as conn:
            conn.execute(
                "INSERT INTO domains (name, enabled, relay_all_inbound) VALUES ('catch.example', 1, 1)"
            )
            catch_id = conn.execute(
                "SELECT id FROM domains WHERE name = 'catch.example'"
            ).fetchone()["id"]
            conn.execute(
                """
                INSERT INTO domain_destinations (domain_id, label, host, port, imap_auth_host, imap_auth_port)
                VALUES (?, 'md', 'relay.backend', 2525, NULL, NULL)
                """,
                (catch_id,),
            )
            conn.execute(
                "INSERT INTO domains (name, enabled, relay_all_inbound) VALUES ('mbox.example', 1, 0)"
            )
            mbox_domain_id = conn.execute(
                "SELECT id FROM domains WHERE name = 'mbox.example'"
            ).fetchone()["id"]
            conn.execute(
                """
                INSERT INTO domain_destinations (domain_id, label, host, port, imap_auth_host, imap_auth_port)
                VALUES (?, 'md', 'mail.backend', 25, 'imap.backend', 143)
                """,
                (mbox_domain_id,),
            )
            conn.execute(
                """
                INSERT INTO mailboxes (email, destination_host, destination_port, enabled, domain_id)
                VALUES ('user@mbox.example', 'mail.backend', 25, 1, ?)
                """,
                (mbox_domain_id,),
            )
            conn.commit()
            config = build_imap_auth_config(conn)

        self.assertEqual(
            config["domains"]["catch.example"],
            {"host": "relay.backend", "port": 993, "ssl": True},
        )
        self.assertEqual(
            config["users"]["user@mbox.example"],
            {"host": "imap.backend", "port": 143, "ssl": False},
        )
        self.assertEqual(
            config["domains"]["mbox.example"],
            {"host": "imap.backend", "port": 143, "ssl": False},
        )

    def test_write_imap_auth_config(self) -> None:
        with db_module.db() as conn:
            conn.execute(
                "INSERT INTO domains (name, enabled, relay_all_inbound) VALUES ('x.example', 1, 1)"
            )
            domain_id = conn.execute("SELECT id FROM domains WHERE name = 'x.example'").fetchone()["id"]
            conn.execute(
                "INSERT INTO domain_destinations (domain_id, label, host, port) VALUES (?, 'md', 'md.example', 25)",
                (domain_id,),
            )
            conn.commit()

        write_imap_auth_config()
        payload = (self.data_dir / "sasl" / "imap_auth.json").read_text(encoding="utf-8")
        self.assertIn('"x.example"', payload)
        self.assertIn('"md.example"', payload)


class ImapAuthRegenerateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        data_dir = Path(self.tmp.name)
        self.data_dir = data_dir
        self.generated_dir = data_dir / "generated"
        mail_cfg = data_dir / "mail-config"
        self.postfix_generated = mail_cfg / "postfix" / "generated"
        self.patches = [
            patch.object(db_module, "DATA_DIR", data_dir),
            patch.object(db_module, "DB_PATH", data_dir / "mailrouter.db"),
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
        ]
        for item in self.patches:
            item.start()
        db_module.init_db()

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def test_regenerate_files_writes_imap_auth_json(self) -> None:
        with db_module.db() as conn:
            conn.execute(
                "INSERT INTO domains (name, enabled, relay_all_inbound) VALUES ('relay.example', 1, 1)"
            )
            domain_id = conn.execute(
                "SELECT id FROM domains WHERE name = 'relay.example'"
            ).fetchone()["id"]
            conn.execute(
                "INSERT INTO domain_destinations (domain_id, label, host, port) VALUES (?, 'md', 'md.example', 25)",
                (domain_id,),
            )
            conn.commit()

        regenerate_files()
        content = (self.data_dir / "sasl" / "imap_auth.json").read_text(encoding="utf-8")
        self.assertIn("relay.example", content)
        self.assertIn("md.example", content)


class MxImapAuthCheckTest(unittest.TestCase):
    def test_resolve_target_prefers_user_entry(self) -> None:
        import importlib.util
        import sys

        script_path = Path(__file__).resolve().parents[2] / "infra" / "postfix" / "mx_imap_auth_check.py"
        spec = importlib.util.spec_from_file_location("mx_imap_auth_check", script_path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules["mx_imap_auth_check"] = module
        spec.loader.exec_module(module)

        config = {
            "users": {"a@x.example": {"host": "user-imap", "port": 993, "ssl": True}},
            "domains": {"x.example": {"host": "domain-imap", "port": 993, "ssl": True}},
        }
        self.assertEqual(
            module.resolve_target("a@x.example", config)["host"],
            "user-imap",
        )
        self.assertEqual(
            module.resolve_target("b@x.example", config)["host"],
            "domain-imap",
        )

    @patch("mx_imap_auth_check.imaplib.IMAP4_SSL")
    def test_verify_imap_login_success(self, mock_ssl) -> None:
        import importlib.util
        import sys

        script_path = Path(__file__).resolve().parents[2] / "infra" / "postfix" / "mx_imap_auth_check.py"
        spec = importlib.util.spec_from_file_location("mx_imap_auth_check", script_path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules["mx_imap_auth_check"] = module
        spec.loader.exec_module(module)

        client = mock_ssl.return_value
        self.assertTrue(
            module.verify_imap_login("imap.example.com", 993, True, "u@d.example", "secret")
        )
        client.login.assert_called_once_with("u@d.example", "secret")


if __name__ == "__main__":
    unittest.main()
