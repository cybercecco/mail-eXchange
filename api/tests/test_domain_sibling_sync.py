import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import db as db_module
from app.domains import DomainCreate, DomainUpdate, create_domain, update_domain
from app.main import api_create_domain, api_update_domain


class DomainSiblingSyncTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        data_dir = Path(self.tmp.name)
        self.db_path = data_dir / "mailrouter.db"
        self.patches = [
            patch.object(db_module, "DATA_DIR", data_dir),
            patch.object(db_module, "DB_PATH", self.db_path),
            patch.object(db_module, "GENERATED_DIR", data_dir / "generated"),
            patch("app.main.regenerate_files"),
            patch("app.sync.SYNC_SHARED_SECRET", "test-secret"),
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
                INSERT INTO mailboxes (email, destination_host, destination_port, enabled, domain_id)
                VALUES ('user@example.com', 'backend.example.com', 25, 1, ?)
                """,
                (self.domain_id,),
            )
            conn.commit()

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    @patch("app.sync._http_post_json")
    def test_put_sibling_fqdn_triggers_push(self, mock_http) -> None:
        mock_http.return_value = (200, {"status": "applied"})

        result = api_update_domain(
            self.domain_id,
            DomainUpdate(sibling_fqdn="mx2.example.com"),
            _user={"role": "admin"},
        )

        self.assertEqual(result["sibling_fqdn"], "mx2.example.com")
        self.assertNotIn("sync_warning", result)
        mock_http.assert_called_once()
        url, payload = mock_http.call_args[0]
        self.assertIn("mx2.example.com", url)
        self.assertEqual(payload["domain_name"], "example.com")
        self.assertEqual(len(payload["mailboxes"]), 1)

    @patch("app.sync._http_post_json")
    def test_put_clear_sibling_fqdn_skips_push(self, mock_http) -> None:
        update_domain(self.domain_id, DomainUpdate(sibling_fqdn="mx2.example.com"))

        result = api_update_domain(
            self.domain_id,
            DomainUpdate(sibling_fqdn=None),
            _user={"role": "admin"},
        )

        self.assertIsNone(result["sibling_fqdn"])
        self.assertNotIn("sync_warning", result)
        mock_http.assert_not_called()

    @patch("app.sync._http_post_json")
    def test_put_change_sibling_pushes_to_new_target(self, mock_http) -> None:
        update_domain(self.domain_id, DomainUpdate(sibling_fqdn="mx2.example.com"))
        mock_http.return_value = (200, {"status": "applied"})

        result = api_update_domain(
            self.domain_id,
            DomainUpdate(sibling_fqdn="mx3.example.com"),
            _user={"role": "admin"},
        )

        self.assertEqual(result["sibling_fqdn"], "mx3.example.com")
        mock_http.assert_called_once()
        url = mock_http.call_args[0][0]
        self.assertIn("mx3.example.com", url)
        self.assertNotIn("mx2.example.com", url)

    @patch("app.sync._http_post_json")
    def test_put_other_fields_skips_push(self, mock_http) -> None:
        result = api_update_domain(
            self.domain_id,
            DomainUpdate(enabled=False),
            _user={"role": "admin"},
        )

        self.assertEqual(result["status"], "updated")
        mock_http.assert_not_called()

    @patch("app.sync._http_post_json")
    def test_create_with_sibling_fqdn_triggers_push(self, mock_http) -> None:
        mock_http.return_value = (200, {"status": "applied"})

        result = api_create_domain(
            DomainCreate(name="new.example.com", sibling_fqdn="mx2.example.com"),
            _user={"role": "admin"},
        )

        self.assertEqual(result["sibling_fqdn"], "mx2.example.com")
        self.assertNotIn("sync_warning", result)
        mock_http.assert_called_once()
        payload = mock_http.call_args[0][1]
        self.assertEqual(payload["domain_name"], "new.example.com")
        self.assertEqual(payload["mailboxes"], [])

    @patch("app.sync._http_post_json")
    def test_zero_mailboxes_still_pushes_empty_list(self, mock_http) -> None:
        with db_module.db() as conn:
            conn.execute(
                """
                INSERT INTO domains (name, enabled, dkim_selector, sibling_fqdn)
                VALUES ('empty.example.com', 1, 'mail', NULL)
                """
            )
            empty_domain_id = conn.execute(
                "SELECT id FROM domains WHERE name = 'empty.example.com'"
            ).fetchone()["id"]
            conn.commit()
        mock_http.return_value = (200, {"status": "applied"})

        result = api_update_domain(
            empty_domain_id,
            DomainUpdate(sibling_fqdn="mx2.example.com"),
            _user={"role": "admin"},
        )

        self.assertNotIn("sync_warning", result)
        payload = mock_http.call_args[0][1]
        self.assertEqual(payload["domain_name"], "empty.example.com")
        self.assertEqual(payload["mailboxes"], [])

    @patch("app.sync._http_post_json")
    def test_push_failure_returns_sync_warning(self, mock_http) -> None:
        mock_http.return_value = (503, {"detail": "unavailable"})

        result = api_update_domain(
            self.domain_id,
            DomainUpdate(sibling_fqdn="mx2.example.com"),
            _user={"role": "admin"},
        )

        self.assertEqual(result["sibling_fqdn"], "mx2.example.com")
        self.assertIn("sync_warning", result)
        self.assertIn("mx2.example.com", result["sync_warning"])


if __name__ == "__main__":
    unittest.main()
