import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import db as db_module
from app.regenerate import write_caddyfile


class WriteCaddyfileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        data_dir = Path(self.tmp.name)
        self.generated_dir = data_dir / "generated"
        self.generated_dir.mkdir(parents=True)
        self.db_path = data_dir / "mailrouter.db"
        self.patches = [
            patch.object(db_module, "DATA_DIR", data_dir),
            patch.object(db_module, "DB_PATH", self.db_path),
            patch.object(db_module, "GENERATED_DIR", self.generated_dir),
            patch("app.regenerate.GENERATED_DIR", self.generated_dir),
            patch.dict("os.environ", {"CLOUDFLARE_API_TOKEN": ""}, clear=False),
        ]
        for item in self.patches:
            item.start()
        db_module.init_db()

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def _save_settings(self, payload: dict) -> None:
        import json

        with db_module.db() as conn:
            conn.execute(
                """
                INSERT INTO system_settings (id, json_payload) VALUES (1, ?)
                ON CONFLICT(id) DO UPDATE SET json_payload = excluded.json_payload
                """,
                (json.dumps(payload),),
            )
            conn.commit()

    def test_without_cloudflare_token_uses_http_and_tls_internal(self) -> None:
        self._save_settings(
            {
                "public_url": "smtp.vetrobalsamo.com",
                "acme_email": "admin@example.com",
                "docker_dns": ["1.1.1.1"],
            }
        )
        write_caddyfile()
        content = (self.generated_dir / "Caddyfile").read_text(encoding="utf-8")
        self.assertIn(":80 {", content)
        self.assertIn("https://smtp.vetrobalsamo.com {", content)
        self.assertIn("tls internal", content)
        self.assertNotIn("dns cloudflare", content)

    def test_with_cloudflare_token_env_uses_dns_acme(self) -> None:
        with patch.dict("os.environ", {"CLOUDFLARE_API_TOKEN": "cf-test-token"}, clear=False):
            self._save_settings(
                {
                    "public_url": "smtp.vetrobalsamo.com",
                    "acme_email": "admin@example.com",
                    "docker_dns": ["1.1.1.1"],
                }
            )
            write_caddyfile()
        content = (self.generated_dir / "Caddyfile").read_text(encoding="utf-8")
        self.assertIn("dns cloudflare {$CLOUDFLARE_API_TOKEN}", content)
        self.assertNotIn("tls internal", content)
        self.assertNotIn(":80 {", content)
        self.assertFalse((self.generated_dir / "caddy.env").exists())
