import email
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app import db as db_module
from app import quarantine as quarantine_module
from app.quarantine import (
    delete_quarantine,
    ingest_amavis_file,
    list_quarantine,
    purge_expired_entries,
    release_quarantine,
)


def _build_message(
    *,
    sender: str = "spam@evil.example",
    recipient: str = "user@example.com",
    subject: str = "Test spam",
    spam_score: str = "12.5",
) -> bytes:
    msg = email.message.EmailMessage()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = subject
    msg["X-Envelope-To"] = recipient
    msg["X-Spam-Score"] = spam_score
    msg.set_content("Spam body")
    return msg.as_bytes()


class QuarantineStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.quarantine_dir = Path(self.tmp.name) / "quarantine"
        self.patches = [
            patch.object(quarantine_module, "QUARANTINE_DIR", self.quarantine_dir),
            patch.object(quarantine_module, "INCOMING_DIR", self.quarantine_dir / "incoming"),
            patch.object(quarantine_module, "TTL_HOURS", 36),
        ]
        for item in self.patches:
            item.start()
        quarantine_module.ensure_quarantine_dirs()

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def test_ingest_list_and_delete(self) -> None:
        incoming = self.quarantine_dir / "incoming" / "spam" / "qps-test"
        incoming.parent.mkdir(parents=True, exist_ok=True)
        incoming.write_bytes(_build_message())

        meta = ingest_amavis_file(incoming, reason="spam")
        self.assertIsNotNone(meta)
        self.assertFalse(incoming.exists())
        self.assertEqual(meta["from"], "spam@evil.example")
        self.assertEqual(meta["to"], ["user@example.com"])
        self.assertEqual(meta["spam_score"], 12.5)

        listing = list_quarantine(from_filter="evil", query="Test")
        self.assertEqual(listing["count"], 1)
        self.assertEqual(listing["items"][0]["id"], meta["id"])

        delete_quarantine(meta["id"])
        self.assertEqual(list_quarantine()["count"], 0)

    def test_purge_expired(self) -> None:
        incoming = self.quarantine_dir / "incoming" / "spam" / "old-msg"
        incoming.parent.mkdir(parents=True, exist_ok=True)
        incoming.write_bytes(_build_message(subject="Old spam"))

        meta = ingest_amavis_file(incoming, reason="spam")
        assert meta is not None
        meta_path = self.quarantine_dir / meta["id"] / "meta.json"
        stored = json.loads(meta_path.read_text(encoding="utf-8"))
        expired = datetime.now(timezone.utc) - timedelta(hours=1)
        stored["expires_at"] = expired.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        meta_path.write_text(json.dumps(stored), encoding="utf-8")

        removed = purge_expired_entries()
        self.assertEqual(removed, 1)
        self.assertEqual(list_quarantine()["count"], 0)

    @patch("app.quarantine.prepare_smtp_session")
    @patch("app.quarantine.smtplib.SMTP")
    def test_release_sends_and_removes(self, mock_smtp_cls, _prepare) -> None:
        incoming = self.quarantine_dir / "incoming" / "spam" / "release-me"
        incoming.parent.mkdir(parents=True, exist_ok=True)
        incoming.write_bytes(_build_message(recipient="release@example.com"))

        meta = ingest_amavis_file(incoming, reason="spam")
        assert meta is not None
        smtp = mock_smtp_cls.return_value.__enter__.return_value

        result = release_quarantine(meta["id"])

        self.assertEqual(result["status"], "released")
        self.assertEqual(result["to"], ["release@example.com"])
        smtp.sendmail.assert_called_once()
        self.assertEqual(list_quarantine()["count"], 0)


class SpamWhitelistGenerationTest(unittest.TestCase):
    def test_amavis_overrides_include_sender_maps(self) -> None:
        from app.spamassassin import build_amavis_overrides, build_amavis_wblist, build_local_cf

        settings = {
            "whitelist_from": ["trusted@partner.com", "*@safe.example"],
            "classification": {"required_score": 5.0},
        }
        overrides = build_amavis_overrides(
            settings, admin_emails=["admin@notify.example"]
        )
        self.assertIn("@virus_admin = qw( admin@notify.example );", overrides)
        self.assertIn("@score_sender_maps", overrides)
        self.assertIn("'trusted@partner.com' => [-100]", overrides)
        self.assertIn("'.safe.example' => [-100]", overrides)

        wblist = build_amavis_wblist(settings)
        self.assertIn(".@\t+ trusted@partner.com", wblist)
        self.assertIn(".@\t+ *@safe.example", wblist)

        local_cf = build_local_cf(settings)
        self.assertIn("whitelist_from trusted@partner.com", local_cf)
        self.assertIn("whitelist_from *@safe.example", local_cf)

    def test_domain_shorthand_patterns_normalized(self) -> None:
        from app.spamassassin import (
            build_amavis_overrides,
            build_local_cf,
            normalize_settings,
            normalize_whitelist_from,
        )

        self.assertEqual(normalize_whitelist_from("*amazon.com"), "*.amazon.com")
        self.assertEqual(normalize_whitelist_from("amazon.com"), "*.amazon.com")
        self.assertEqual(normalize_whitelist_from("@amazon.com"), "*@amazon.com")
        self.assertEqual(normalize_whitelist_from("*@bounces.amazon.com"), "*@bounces.amazon.com")

        settings = normalize_settings(
            {
                "whitelist_from": [
                    "*amazon.com",
                    "*@bounces.amazon.com",
                    "*@partner.example",
                ]
            }
        )
        self.assertEqual(
            settings["whitelist_from"],
            ["*.amazon.com", "*@bounces.amazon.com", "*@partner.example"],
        )

        overrides = build_amavis_overrides(settings)
        self.assertIn("'.amazon.com' => [-100]", overrides)
        self.assertIn("'.bounces.amazon.com' => [-100]", overrides)
        self.assertIn("'.partner.example' => [-100]", overrides)
        self.assertNotIn("'*amazon.com'", overrides)

        local_cf = build_local_cf(settings)
        self.assertIn("whitelist_from *.amazon.com", local_cf)
        self.assertIn("whitelist_from *@bounces.amazon.com", local_cf)


class AmavisStaticConfigTest(unittest.TestCase):
    def test_user_config_disables_unchecked_subject_tag(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        for rel in (
            "infra/amavis/amavisd.conf",
            "config/amavis/conf.d/50-user",
        ):
            text = (repo_root / rel).read_text(encoding="utf-8")
            self.assertIn(
                "$undecipherable_subject_tag = undef;",
                text,
                rel,
            )
            self.assertIn("100.64.0.0/10", text, rel)
