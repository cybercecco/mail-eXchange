import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app import db as db_module
from app.mailbox_import import import_mailboxes_csv


class MailboxImportTest(unittest.TestCase):
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
                INSERT INTO domains (name, enabled, dkim_selector, sibling_fqdn)
                VALUES ('example.com', 1, 'mail', NULL)
                """
            )
            self.domain_id = conn.execute(
                "SELECT id FROM domains WHERE name = 'example.com'"
            ).fetchone()["id"]
            conn.execute(
                """
                INSERT INTO domain_destinations (domain_id, label, host, port)
                VALUES (?, 'Primary', 'backend.example.com', 25),
                       (?, 'Backup MX', 'backup.example.com', 587)
                """,
                (self.domain_id, self.domain_id),
            )
            conn.commit()

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def test_import_maps_label_to_local_host(self) -> None:
        csv_text = """mail,destination_label
new@example.com,primary
other@example.com,Backup MX
"""
        result = import_mailboxes_csv(csv_text, update_existing=False)
        self.assertEqual(result["created"], 2)
        self.assertEqual(result["errors"], [])

        with db_module.db() as conn:
            row = conn.execute(
                """
                SELECT destination_host, destination_port
                FROM mailboxes WHERE email = 'new@example.com'
                """
            ).fetchone()
            self.assertEqual(row["destination_host"], "backend.example.com")
            self.assertEqual(row["destination_port"], 25)

            row2 = conn.execute(
                """
                SELECT destination_host, destination_port
                FROM mailboxes WHERE email = 'other@example.com'
                """
            ).fetchone()
            self.assertEqual(row2["destination_host"], "backup.example.com")
            self.assertEqual(row2["destination_port"], 587)

    def test_unknown_label_reported_as_error(self) -> None:
        csv_text = """mail,destination_label
bad@example.com,Missing Label
"""
        result = import_mailboxes_csv(csv_text)
        self.assertEqual(result["created"], 0)
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("Missing Label", result["errors"][0]["error"])

    def test_local_and_domain_columns(self) -> None:
        csv_text = """local,domain,label
alice,example.com,Primary
"""
        result = import_mailboxes_csv(csv_text)
        self.assertEqual(result["created"], 1)
        with db_module.db() as conn:
            exists = conn.execute(
                "SELECT 1 FROM mailboxes WHERE email = 'alice@example.com'"
            ).fetchone()
            self.assertIsNotNone(exists)

    def test_legacy_host_port_columns_still_work(self) -> None:
        csv_text = """mail,destination_host,port
legacy@example.com,backend.example.com,25
"""
        result = import_mailboxes_csv(csv_text)
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["errors"], [])

    def test_legacy_destinazione_with_port_detected(self) -> None:
        csv_text = """mail,destinazione,porta
legacy2@example.com,backup.example.com,587
"""
        result = import_mailboxes_csv(csv_text)
        self.assertEqual(result["created"], 1)

    def test_skip_header_two_column_label_format(self) -> None:
        csv_text = """mail,destination_label
skip@example.com,Primary
"""
        result = import_mailboxes_csv(csv_text, skip_header=True)
        self.assertEqual(result["created"], 1)

    def test_missing_label_column_raises(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            import_mailboxes_csv("mail\nuser@example.com\n", skip_header=True)
        self.assertIn("destination_label", str(ctx.exception.detail).lower())


if __name__ == "__main__":
    unittest.main()
