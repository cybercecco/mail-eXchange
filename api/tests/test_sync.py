import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import db as db_module
from app.sync import (
    SYNC_HTTPS_PORT,
    apply_incoming_mailbox_sync,
    build_mailbox_sync_payload,
    is_self_sync_target,
)
from app.sync import SyncMailboxesPayload


class SyncPayloadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        data_dir = Path(self.tmp.name)
        self.db_path = data_dir / "mailrouter.db"
        self.patches = [
            patch.object(db_module, "DATA_DIR", data_dir),
            patch.object(db_module, "DB_PATH", self.db_path),
            patch.object(db_module, "GENERATED_DIR", data_dir / "generated"),
        ]
        for item in self.patches:
            item.start()
        db_module.init_db()
        with db_module.db() as conn:
            conn.execute(
                """
                INSERT INTO domains (name, enabled, dkim_selector, sibling_fqdn)
                VALUES ('example.com', 1, 'mail', 'mx2.example.com')
                """
            )
            domain_id = conn.execute(
                "SELECT id FROM domains WHERE name = 'example.com'"
            ).fetchone()["id"]
            conn.execute(
                """
                INSERT INTO domain_destinations (domain_id, label, host, port)
                VALUES (?, 'Primary', 'backend.example.com', 25)
                """,
                (domain_id,),
            )
            conn.execute(
                """
                INSERT INTO mailboxes (email, destination_host, destination_port, enabled, domain_id)
                VALUES ('user@example.com', 'backend.example.com', 25, 1, ?)
                """,
                (domain_id,),
            )
            conn.commit()
            self.domain_id = domain_id

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def test_build_mailbox_sync_payload(self) -> None:
        payload = build_mailbox_sync_payload(self.domain_id)
        assert payload is not None
        self.assertEqual(payload["domain_name"], "example.com")
        self.assertEqual(len(payload["mailboxes"]), 1)
        self.assertEqual(payload["mailboxes"][0]["email"], "user@example.com")
        self.assertEqual(payload["mailboxes"][0]["destination_host"], "backend.example.com")
        self.assertNotIn("destinations", payload)
        self.assertNotIn("deleted", payload)

    def test_build_payload_without_sibling_returns_none(self) -> None:
        with db_module.db() as conn:
            conn.execute(
                """
                INSERT INTO domains (name, enabled, dkim_selector, sibling_fqdn)
                VALUES ('solo.example.com', 1, 'mail', NULL)
                """
            )
            domain_id = conn.execute(
                "SELECT id FROM domains WHERE name = 'solo.example.com'"
            ).fetchone()["id"]
            conn.commit()
        self.assertIsNone(build_mailbox_sync_payload(domain_id))

    def test_self_sync_prevention(self) -> None:
        with patch("app.sync.PUBLIC_HOSTNAME", "mx1.example.com"):
            self.assertTrue(is_self_sync_target("mx1.example.com"))
            self.assertFalse(is_self_sync_target("mx2.example.com"))

    def test_sync_url_uses_configured_port(self) -> None:
        from app.sync import _sync_url

        self.assertEqual(
            _sync_url("mx2.example.com"),
            f"https://mx2.example.com:{SYNC_HTTPS_PORT}/api/sync/mailboxes",
        )

    def test_apply_incoming_does_not_touch_domain_or_destinations(self) -> None:
        with db_module.db() as conn:
            conn.execute(
                """
                INSERT INTO domains (name, enabled, dkim_selector, sibling_fqdn)
                VALUES ('remote.com', 0, 'custom', 'peer.example.com')
                """
            )
            domain_id = conn.execute(
                "SELECT id FROM domains WHERE name = 'remote.com'"
            ).fetchone()["id"]
            conn.execute(
                """
                INSERT INTO domain_destinations (domain_id, label, host, port)
                VALUES (?, 'Local', 'local.backend', 587)
                """,
                (domain_id,),
            )
            conn.execute(
                """
                INSERT INTO mailboxes (email, destination_host, destination_port, enabled, domain_id)
                VALUES ('old@remote.com', 'local.backend', 587, 1, ?)
                """,
                (domain_id,),
            )
            conn.commit()

        with patch("app.sync.regenerate_files"):
            apply_incoming_mailbox_sync(
                SyncMailboxesPayload(
                    domain_name="remote.com",
                    mailboxes=[
                        {
                            "email": "new@remote.com",
                            "destination_host": "other.backend",
                            "destination_port": 25,
                            "enabled": True,
                        }
                    ],
                )
            )

        with db_module.db() as conn:
            domain = conn.execute(
                "SELECT enabled, dkim_selector, sibling_fqdn FROM domains WHERE name = 'remote.com'"
            ).fetchone()
            self.assertEqual(domain["enabled"], 0)
            self.assertEqual(domain["dkim_selector"], "custom")
            self.assertEqual(domain["sibling_fqdn"], "peer.example.com")
            destinations = conn.execute(
                "SELECT host FROM domain_destinations WHERE domain_id = ?",
                (domain_id,),
            ).fetchall()
            self.assertEqual(len(destinations), 1)
            self.assertEqual(destinations[0]["host"], "local.backend")
            mailboxes = conn.execute(
                "SELECT email FROM mailboxes WHERE domain_id = ? ORDER BY email",
                (domain_id,),
            ).fetchall()
            self.assertEqual([row["email"] for row in mailboxes], ["new@remote.com"])


if __name__ == "__main__":
    unittest.main()
