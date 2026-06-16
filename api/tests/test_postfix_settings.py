import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from app import db as db_module
from app.main import get_postfix, set_postfix
from app.postfix_settings import (
    DEFAULT_MESSAGE_SIZE_LIMIT,
    PostfixSettings,
    build_main_override,
    get_settings,
    normalize_settings,
    persist_settings,
)


class PostfixSettingsModuleTest(unittest.TestCase):
    def test_normalize_applies_defaults(self) -> None:
        result = normalize_settings({})
        self.assertEqual(result["message_size_limit"], DEFAULT_MESSAGE_SIZE_LIMIT)
        self.assertEqual(result["mailbox_size_limit"], 51_200_000)
        self.assertEqual(result["smtpd_timeout"], 300)

    def test_normalize_rejects_mailbox_smaller_than_message(self) -> None:
        with self.assertRaises(ValidationError):
            normalize_settings(
                {
                    "message_size_limit": 20_971_520,
                    "mailbox_size_limit": 10_485_760,
                }
            )

    def test_build_main_override_formats_postfix_directives(self) -> None:
        content = build_main_override(
            {
                "message_size_limit": 20_971_520,
                "mailbox_size_limit": 52_428_800,
                "smtpd_timeout": 600,
            }
        )
        self.assertIn("message_size_limit = 20971520", content)
        self.assertIn("mailbox_size_limit = 52428800", content)
        self.assertIn("smtpd_timeout = 600s", content)


class PostfixSettingsDbTest(unittest.TestCase):
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

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def test_get_settings_reads_defaults_from_db(self) -> None:
        settings = get_settings()
        self.assertEqual(settings["message_size_limit"], DEFAULT_MESSAGE_SIZE_LIMIT)

    def test_persist_settings_round_trip(self) -> None:
        saved = persist_settings(
            {
                "message_size_limit": 15_728_640,
                "mailbox_size_limit": 62_914_560,
                "smtpd_timeout": 120,
            }
        )
        self.assertEqual(saved["message_size_limit"], 15_728_640)
        self.assertEqual(get_settings()["smtpd_timeout"], 120)


class PostfixSettingsApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        data_dir = Path(self.tmp.name)
        self.db_path = data_dir / "mailrouter.db"
        self.patches = [
            patch.object(db_module, "DATA_DIR", data_dir),
            patch.object(db_module, "DB_PATH", self.db_path),
            patch.object(db_module, "GENERATED_DIR", data_dir / "generated"),
            patch("app.main.regenerate_files"),
        ]
        for item in self.patches:
            item.start()
        db_module.init_db()

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def test_get_postfix_returns_normalized_settings(self) -> None:
        result = get_postfix(_user={"role": "user"})
        self.assertEqual(result["message_size_limit"], DEFAULT_MESSAGE_SIZE_LIMIT)

    def test_set_postfix_persists_and_regenerates(self) -> None:
        payload = PostfixSettings(
            message_size_limit=25_165_824,
            mailbox_size_limit=52_428_800,
            smtpd_timeout=450,
        )
        with patch("app.main.regenerate_files") as mock_regen:
            result = set_postfix(payload, _user={"role": "admin"})
        self.assertEqual(result["status"], "updated")
        self.assertEqual(result["settings"]["message_size_limit"], 25_165_824)
        mock_regen.assert_called_once()

        with db_module.db() as conn:
            row = conn.execute(
                "SELECT json_payload FROM postfix_settings WHERE id = 1"
            ).fetchone()
        stored = json.loads(row["json_payload"])
        self.assertEqual(stored["smtpd_timeout"], 450)
