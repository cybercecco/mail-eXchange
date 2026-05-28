import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app import db as db_module
from app.domain_destinations import DestinationCreate, create_destination
from app.domains import DomainUpdate, assert_mailboxes_allowed, update_domain
from app.mailbox_import import import_mailboxes_csv


class RelayAllInboundTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        data_dir = Path(self.tmp.name)
        self.db_path = data_dir / "mailrouter.db"
        self.patches = [
            patch.object(db_module, "DATA_DIR", data_dir),
            patch.object(db_module, "DB_PATH", self.db_path),
            patch.object(db_module, "GENERATED_DIR", data_dir / "generated"),
            patch("app.sync.touch_domain_updated_at"),
        ]
        for item in self.patches:
            item.start()
        db_module.init_db()
        with db_module.db() as conn:
            conn.execute(
                """
                INSERT INTO domains (name, enabled, dkim_selector, relay_all_inbound)
                VALUES ('relay.example', 1, 'mail', 0)
                """
            )
            self.domain_id = conn.execute(
                "SELECT id FROM domains WHERE name = 'relay.example'"
            ).fetchone()["id"]
            conn.execute(
                """
                INSERT INTO domain_destinations (domain_id, label, host, port)
                VALUES (?, 'Primary', 'backend.example.com', 25),
                       (?, 'Secondary', 'backup.example.com', 587)
                """,
                (self.domain_id, self.domain_id),
            )
            conn.commit()

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def test_enable_relay_all_rejected_with_multiple_destinations(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            update_domain(self.domain_id, DomainUpdate(relay_all_inbound=True))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("Remove extra destination", str(ctx.exception.detail))

    def test_enable_relay_all_allowed_with_single_destination(self) -> None:
        with db_module.db() as conn:
            conn.execute(
                "DELETE FROM domain_destinations WHERE host = 'backup.example.com'"
            )
            conn.commit()

        result = update_domain(self.domain_id, DomainUpdate(relay_all_inbound=True))
        self.assertEqual(result["status"], "updated")

        with db_module.db() as conn:
            row = conn.execute(
                "SELECT relay_all_inbound FROM domains WHERE id = ?",
                (self.domain_id,),
            ).fetchone()
            self.assertEqual(int(row["relay_all_inbound"]), 1)

    def test_create_second_destination_rejected_when_relay_all_enabled(self) -> None:
        with db_module.db() as conn:
            conn.execute(
                "DELETE FROM domain_destinations WHERE host = 'backup.example.com'"
            )
            conn.execute(
                "UPDATE domains SET relay_all_inbound = 1 WHERE id = ?",
                (self.domain_id,),
            )
            conn.commit()

        with self.assertRaises(HTTPException) as ctx:
            create_destination(
                self.domain_id,
                DestinationCreate(label="Extra", host="extra.example.com", port=25),
            )
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("Only one destination", str(ctx.exception.detail))

    def test_mailbox_import_rejected_when_relay_all_enabled(self) -> None:
        with db_module.db() as conn:
            conn.execute(
                "DELETE FROM domain_destinations WHERE host = 'backup.example.com'"
            )
            conn.execute(
                "UPDATE domains SET relay_all_inbound = 1 WHERE id = ?",
                (self.domain_id,),
            )
            conn.commit()

        csv_text = """mail,destination_label
user@relay.example,Primary
"""
        with self.assertRaises(HTTPException) as ctx:
            import_mailboxes_csv(csv_text)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("Mailbox add/import/edit is disabled", str(ctx.exception.detail))

    def test_assert_mailboxes_allowed_blocks_relay_domain(self) -> None:
        with db_module.db() as conn:
            conn.execute(
                "DELETE FROM domain_destinations WHERE host = 'backup.example.com'"
            )
            conn.execute(
                "UPDATE domains SET relay_all_inbound = 1 WHERE id = ?",
                (self.domain_id,),
            )
            conn.commit()

        with self.assertRaises(HTTPException) as ctx:
            assert_mailboxes_allowed(self.domain_id)
        self.assertEqual(ctx.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
