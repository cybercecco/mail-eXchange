import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app import db as db_module
from app.sync import (
    SYNC_HTTPS_PORT,
    SyncDomainBundlePayload,
    apply_incoming_domain_sync,
    attach_sync_warning,
    build_domain_sync_payload,
    build_mailbox_sync_payload,
    is_self_sync_target,
    merge_mx_hints,
    push_to_sibling,
    verify_sync_auth,
)
from app.sync import SyncMailboxesPayload


class SyncPayloadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        data_dir = Path(self.tmp.name)
        self.db_path = data_dir / "mailrouter.db"
        self.keys_dir = data_dir / "opendkim-keys"
        self.keys_dir.mkdir()
        self.patches = [
            patch.object(db_module, "DATA_DIR", data_dir),
            patch.object(db_module, "DB_PATH", self.db_path),
            patch.object(db_module, "GENERATED_DIR", data_dir / "generated"),
            patch("app.dkim_keys.OPENDKIM_KEYS_DIR", self.keys_dir),
            patch("app.sync.POSTFIX_HOSTNAME", "mx1.example.com"),
            patch("app.sync.PUBLIC_HOSTNAME", "smtp.example.com"),
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

    def test_build_domain_sync_payload(self) -> None:
        private_path = self.keys_dir / "example.com" / "mail.private"
        private_path.parent.mkdir(parents=True)
        private_path.write_text("-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----\n")
        (db_module.GENERATED_DIR / "dkim").mkdir(parents=True, exist_ok=True)
        (db_module.GENERATED_DIR / "dkim" / "example.com.pub").write_text("abc123")

        payload = build_domain_sync_payload(self.domain_id)
        assert payload is not None
        self.assertEqual(payload["domain_name"], "example.com")
        self.assertEqual(len(payload["mailboxes"]), 1)
        self.assertEqual(payload["mailboxes"][0]["email"], "user@example.com")
        self.assertEqual(payload["mailboxes"][0]["destination_label"], "Primary")
        self.assertEqual(payload["domain_sync"]["dkim_selector"], "mail")
        self.assertIn("dkim_private_key_pem", payload["domain_sync"])
        self.assertIn("v=DKIM1", payload["domain_sync"]["dkim_public_key_dns_txt"])
        self.assertEqual(
            payload["mx_records"],
            [
                {"priority": 10, "host": "mx1.example.com"},
                {"priority": 20, "host": "smtp.example.com"},
            ],
        )

    def test_build_mailbox_sync_payload_alias(self) -> None:
        payload = build_mailbox_sync_payload(self.domain_id)
        assert payload is not None
        self.assertIn("domain_sync", payload)
        self.assertIn("mx_records", payload)

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
        self.assertIsNone(build_domain_sync_payload(domain_id))

    def test_self_sync_prevention(self) -> None:
        with patch("app.sync.PUBLIC_HOSTNAME", "mx1.example.com"):
            self.assertTrue(is_self_sync_target("mx1.example.com"))
            self.assertFalse(is_self_sync_target("mx2.example.com"))

    def test_sync_url_uses_domain_bundle_endpoint(self) -> None:
        from app.sync import _sync_url

        self.assertEqual(
            _sync_url("mx2.example.com"),
            f"https://mx2.example.com:{SYNC_HTTPS_PORT}/api/sync/domain-bundle",
        )

    def test_merge_mx_hints_unions_by_host(self) -> None:
        merged = merge_mx_hints(
            json.dumps([{"priority": 10, "host": "mx1.example.com"}]),
            [{"priority": 20, "host": "mx2.example.com"}],
        )
        data = json.loads(merged)
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]["host"], "mx1.example.com")

    def test_apply_incoming_updates_dkim_and_mx_without_touching_sibling(self) -> None:
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
            conn.commit()

        with patch("app.sync.regenerate_files"):
            apply_incoming_domain_sync(
                SyncDomainBundlePayload(
                    domain_name="remote.com",
                    mailboxes=[
                        {
                            "email": "new@remote.com",
                            "destination_host": "other.backend",
                            "destination_port": 25,
                            "enabled": True,
                        }
                    ],
                    domain_sync={
                        "dkim_selector": "selector2024",
                        "dkim_private_key_pem": "-----BEGIN PRIVATE KEY-----\nsynced\n-----END PRIVATE KEY-----\n",
                        "dkim_public_key_dns_txt": "v=DKIM1; k=rsa; p=syncedpub",
                    },
                    mx_records=[{"priority": 10, "host": "mx-peer.example.com"}],
                )
            )

        with db_module.db() as conn:
            domain = conn.execute(
                """
                SELECT enabled, dkim_selector, sibling_fqdn, dns_mx_hints
                FROM domains WHERE name = 'remote.com'
                """
            ).fetchone()
            self.assertEqual(domain["enabled"], 0)
            self.assertEqual(domain["dkim_selector"], "selector2024")
            self.assertEqual(domain["sibling_fqdn"], "peer.example.com")
            hints = json.loads(domain["dns_mx_hints"])
            self.assertEqual(hints[0]["host"], "mx-peer.example.com")

        private_path = self.keys_dir / "remote.com" / "selector2024.private"
        self.assertTrue(private_path.is_file())
        self.assertIn("synced", private_path.read_text())
        pub_path = db_module.GENERATED_DIR / "dkim" / "remote.com.pub"
        self.assertTrue(pub_path.is_file())
        self.assertEqual(pub_path.read_text(), "syncedpub")

        with db_module.db() as conn:
            mailbox = conn.execute(
                """
                SELECT destination_host, destination_port
                FROM mailboxes WHERE email = 'new@remote.com'
                """
            ).fetchone()
            self.assertEqual(mailbox["destination_host"], "other.backend")
            self.assertEqual(mailbox["destination_port"], 25)

    def test_apply_incoming_maps_destination_label_to_local_host(self) -> None:
        with db_module.db() as conn:
            conn.execute(
                """
                INSERT INTO domains (name, enabled, dkim_selector, sibling_fqdn)
                VALUES ('cluster.com', 1, 'mail', 'peer.example.com')
                """
            )
            domain_id = conn.execute(
                "SELECT id FROM domains WHERE name = 'cluster.com'"
            ).fetchone()["id"]
            conn.execute(
                """
                INSERT INTO domain_destinations (domain_id, label, host, port)
                VALUES (?, 'Primary', 'node-local.backend', 587)
                """,
                (domain_id,),
            )
            conn.commit()

        with patch("app.sync.regenerate_files"):
            result = apply_incoming_domain_sync(
                SyncDomainBundlePayload(
                    domain_name="cluster.com",
                    mailboxes=[
                        {
                            "email": "user@cluster.com",
                            "destination_label": "primary",
                            "destination_host": "remote-node.backend",
                            "destination_port": 25,
                            "enabled": True,
                        }
                    ],
                )
            )

        self.assertEqual(result["status"], "applied")
        self.assertNotIn("warnings", result)
        with db_module.db() as conn:
            mailbox = conn.execute(
                """
                SELECT destination_host, destination_port, enabled
                FROM mailboxes WHERE email = 'user@cluster.com'
                """
            ).fetchone()
            self.assertEqual(mailbox["destination_host"], "node-local.backend")
            self.assertEqual(mailbox["destination_port"], 587)
            self.assertEqual(mailbox["enabled"], 1)

    def test_apply_incoming_skips_unknown_label_with_warning(self) -> None:
        with db_module.db() as conn:
            conn.execute(
                """
                INSERT INTO domains (name, enabled, dkim_selector, sibling_fqdn)
                VALUES ('missing-label.com', 1, 'mail', 'peer.example.com')
                """
            )
            conn.commit()

        with patch("app.sync.regenerate_files"):
            result = apply_incoming_domain_sync(
                SyncDomainBundlePayload(
                    domain_name="missing-label.com",
                    mailboxes=[
                        {
                            "email": "user@missing-label.com",
                            "destination_label": "Unknown",
                            "destination_host": "remote.backend",
                            "destination_port": 25,
                            "enabled": True,
                        }
                    ],
                )
            )

        self.assertEqual(result["status"], "applied")
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("Unknown", result["warnings"][0])
        with db_module.db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS c FROM mailboxes WHERE email = 'user@missing-label.com'"
            ).fetchone()["c"]
            self.assertEqual(count, 0)

    def test_legacy_mailbox_payload_still_applies(self) -> None:
        with patch("app.sync.regenerate_files"):
            apply_incoming_domain_sync(
                SyncMailboxesPayload(
                    domain_name="remote.com",
                    mailboxes=[],
                )
            )

    def test_verify_sync_auth_accepts_matching_domain_secret(self) -> None:
        with db_module.db() as conn:
            conn.execute(
                "UPDATE domains SET sync_secret = 'domain-key' WHERE id = ?",
                (self.domain_id,),
            )
            conn.commit()
        verify_sync_auth("example.com", "Bearer domain-key")

    def test_verify_sync_auth_rejects_missing_secret(self) -> None:
        with db_module.db() as conn:
            conn.execute(
                "UPDATE domains SET sync_secret = NULL WHERE id = ?",
                (self.domain_id,),
            )
            conn.commit()
        with self.assertRaises(HTTPException) as ctx:
            verify_sync_auth("example.com", "Bearer anything")
        self.assertEqual(ctx.exception.status_code, 503)

    def test_verify_sync_auth_rejects_invalid_token(self) -> None:
        with db_module.db() as conn:
            conn.execute(
                "UPDATE domains SET sync_secret = 'domain-key' WHERE id = ?",
                (self.domain_id,),
            )
            conn.commit()
        with self.assertRaises(HTTPException) as ctx:
            verify_sync_auth("example.com", "Bearer wrong")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_push_skipped_without_sibling_even_without_sync_secret(self) -> None:
        with db_module.db() as conn:
            conn.execute(
                """
                INSERT INTO domains (name, enabled, dkim_selector, sibling_fqdn, sync_secret)
                VALUES ('local-only.com', 1, 'mail', NULL, NULL)
                """,
            )
            domain_id = conn.execute(
                "SELECT id FROM domains WHERE name = 'local-only.com'"
            ).fetchone()["id"]
            conn.commit()
        self.assertIsNone(push_to_sibling(domain_id))
        result = attach_sync_warning({"status": "ok"}, domain_id)
        self.assertNotIn("sync_warning", result)

    def test_per_domain_sync_secret_isolation(self) -> None:
        with db_module.db() as conn:
            conn.execute(
                "UPDATE domains SET sync_secret = 'secret-a' WHERE id = ?",
                (self.domain_id,),
            )
            conn.execute(
                """
                INSERT INTO domains (name, enabled, dkim_selector, sibling_fqdn, sync_secret)
                VALUES ('other.com', 1, 'mail', 'mx2.example.com', 'secret-b')
                """,
            )
            conn.commit()
        verify_sync_auth("example.com", "Bearer secret-a")
        verify_sync_auth("other.com", "Bearer secret-b")
        with self.assertRaises(HTTPException) as ctx:
            verify_sync_auth("other.com", "Bearer secret-a")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_apply_incoming_does_not_touch_other_domains(self) -> None:
        with db_module.db() as conn:
            conn.execute(
                """
                INSERT INTO domains (name, enabled, dkim_selector, sibling_fqdn)
                VALUES ('untouched.com', 1, 'mail', NULL)
                """
            )
            untouched_id = conn.execute(
                "SELECT id FROM domains WHERE name = 'untouched.com'"
            ).fetchone()["id"]
            conn.execute(
                """
                INSERT INTO mailboxes (email, destination_host, destination_port, enabled, domain_id)
                VALUES ('keep@untouched.com', 'backend.example.com', 25, 1, ?)
                """,
                (untouched_id,),
            )
            conn.commit()

        with patch("app.sync.regenerate_files"):
            apply_incoming_domain_sync(
                SyncDomainBundlePayload(
                    domain_name="example.com",
                    mailboxes=[
                        {
                            "email": "synced@example.com",
                            "destination_host": "backend.example.com",
                            "destination_port": 25,
                            "enabled": True,
                        }
                    ],
                )
            )

        with db_module.db() as conn:
            untouched = conn.execute(
                "SELECT COUNT(*) AS c FROM mailboxes WHERE email = 'keep@untouched.com'"
            ).fetchone()["c"]
            synced = conn.execute(
                "SELECT COUNT(*) AS c FROM mailboxes WHERE email = 'synced@example.com'"
            ).fetchone()["c"]
        self.assertEqual(untouched, 1)
        self.assertEqual(synced, 1)


if __name__ == "__main__":
    unittest.main()
