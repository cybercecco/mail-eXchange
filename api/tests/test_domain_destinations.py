import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app import db as db_module
from app.domain_destinations import DestinationCreate, DestinationUpdate, update_destination


class DestinationUpdateTest(unittest.TestCase):
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
            self.domain_id = conn.execute(
                "SELECT id FROM domains WHERE name = 'example.com'"
            ).fetchone()["id"]
            conn.execute(
                """
                INSERT INTO domain_destinations (domain_id, label, host, port)
                VALUES (?, 'Primary', 'backend.example.com', 25)
                """,
                (self.domain_id,),
            )
            self.destination_id = conn.execute(
                "SELECT id FROM domain_destinations WHERE domain_id = ?",
                (self.domain_id,),
            ).fetchone()["id"]
            conn.execute(
                """
                INSERT INTO mailboxes (email, destination_host, destination_port, enabled, domain_id)
                VALUES ('user@example.com', 'Backend.Example.com', 25, 1, ?)
                """,
                (self.domain_id,),
            )
            conn.execute(
                """
                INSERT INTO mailboxes (email, destination_host, destination_port, enabled, domain_id)
                VALUES ('other@example.com', 'other.backend', 587, 1, ?)
                """,
                (self.domain_id,),
            )
            conn.commit()

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def test_update_host_port_propagates_to_matching_mailboxes(self) -> None:
        result = update_destination(
            self.domain_id,
            self.destination_id,
            DestinationUpdate(host="new.backend.example", port=2525),
        )
        self.assertEqual(result["host"], "new.backend.example")
        self.assertEqual(result["port"], 2525)
        self.assertEqual(result["mailboxes_updated"], 1)

        with db_module.db() as conn:
            mailbox = conn.execute(
                """
                SELECT destination_host, destination_port
                FROM mailboxes WHERE email = 'user@example.com'
                """
            ).fetchone()
            self.assertEqual(mailbox["destination_host"], "new.backend.example")
            self.assertEqual(mailbox["destination_port"], 2525)

            other = conn.execute(
                """
                SELECT destination_host, destination_port
                FROM mailboxes WHERE email = 'other@example.com'
                """
            ).fetchone()
            self.assertEqual(other["destination_host"], "other.backend")
            self.assertEqual(other["destination_port"], 587)

    def test_label_only_update_does_not_touch_mailboxes(self) -> None:
        result = update_destination(
            self.domain_id,
            self.destination_id,
            DestinationUpdate(label="Updated label"),
        )
        self.assertEqual(result["label"], "Updated label")
        self.assertEqual(result["mailboxes_updated"], 0)

        with db_module.db() as conn:
            mailbox = conn.execute(
                """
                SELECT destination_host, destination_port
                FROM mailboxes WHERE email = 'user@example.com'
                """
            ).fetchone()
            self.assertEqual(mailbox["destination_host"], "Backend.Example.com")
            self.assertEqual(mailbox["destination_port"], 25)

    def test_duplicate_host_port_rejected(self) -> None:
        with db_module.db() as conn:
            conn.execute(
                """
                INSERT INTO domain_destinations (domain_id, label, host, port)
                VALUES (?, 'Secondary', 'other.backend', 587)
                """,
                (self.domain_id,),
            )
            conn.commit()

        with self.assertRaises(HTTPException) as ctx:
            update_destination(
                self.domain_id,
                self.destination_id,
                DestinationUpdate(host="other.backend", port=587),
            )
        self.assertEqual(ctx.exception.status_code, 409)

    def test_create_destination_rejects_duplicate(self) -> None:
        from app.domain_destinations import create_destination

        with self.assertRaises(HTTPException) as ctx:
            create_destination(
                self.domain_id,
                DestinationCreate(label="Dup", host="backend.example.com", port=25),
            )
        self.assertEqual(ctx.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
