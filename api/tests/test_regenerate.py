import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import db as db_module
from app.regenerate import regenerate_files


class RegenerateCatchAllTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        data_dir = Path(self.tmp.name)
        self.generated_dir = data_dir / "generated"
        self.db_path = data_dir / "mailrouter.db"
        self.patches = [
            patch.object(db_module, "DATA_DIR", data_dir),
            patch.object(db_module, "DB_PATH", self.db_path),
            patch.object(db_module, "GENERATED_DIR", self.generated_dir),
            patch("app.regenerate.GENERATED_DIR", self.generated_dir),
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

    def _insert_domain(
        self,
        conn,
        name: str,
        *,
        relay_all_inbound: bool = False,
        relay_source_ips: str | None = None,
        enabled: bool = True,
        destination: tuple[str, int] | None = None,
    ) -> int:
        cursor = conn.execute(
            """
            INSERT INTO domains (name, enabled, dkim_selector, relay_all_inbound, relay_source_ips)
            VALUES (?, ?, 'mail', ?, ?)
            """,
            (name, int(enabled), int(relay_all_inbound), relay_source_ips),
        )
        domain_id = cursor.lastrowid
        if destination:
            host, port = destination
            conn.execute(
                """
                INSERT INTO domain_destinations (domain_id, label, host, port)
                VALUES (?, 'Primary', ?, ?)
                """,
                (domain_id, host, port),
            )
        conn.commit()
        return domain_id

    def test_catch_all_uses_sole_destination_when_multiple_exist(self) -> None:
        with db_module.db() as conn:
            domain_id = self._insert_domain(
                conn,
                "multi.example",
                relay_all_inbound=True,
                destination=("first.backend", 2525),
            )
            conn.execute(
                """
                INSERT INTO domain_destinations (domain_id, label, host, port)
                VALUES (?, 'Second', 'second.backend', 26)
                """,
                (domain_id,),
            )
            conn.commit()

        with self.assertLogs("app.regenerate", level="WARNING") as logs:
            regenerate_files()

        transport = (self.generated_dir / "transport_maps").read_text(encoding="utf-8")
        self.assertIn("@multi.example smtp:[first.backend]:2525", transport)
        self.assertNotIn("@multi.example smtp:[second.backend]:26", transport)
        self.assertTrue(
            any("2 destinations configured" in message for message in logs.output)
        )

    def test_catch_all_generates_transport_and_regexp_maps(self) -> None:
        with db_module.db() as conn:
            domain_id = self._insert_domain(
                conn,
                "catchall.example",
                relay_all_inbound=True,
                destination=("relay.backend", 2525),
            )
            conn.execute(
                """
                INSERT INTO mailboxes (email, destination_host, destination_port, enabled, domain_id)
                VALUES ('known@catchall.example', 'other.backend', 25, 1, ?)
                """,
                (domain_id,),
            )
            conn.commit()

        regenerate_files()

        transport = (self.generated_dir / "transport_maps").read_text(encoding="utf-8")
        catchall = (self.generated_dir / "virtual_mailbox_catchall").read_text(encoding="utf-8")

        self.assertIn("@catchall.example smtp:[relay.backend]:2525", transport)
        self.assertIn("known@catchall.example smtp:[other.backend]:25", transport)
        self.assertIn("/^.+@catchall\\.example$/ OK", catchall)

    def test_catch_all_skipped_without_destination(self) -> None:
        with db_module.db() as conn:
            self._insert_domain(
                conn,
                "node.st.example",
                relay_all_inbound=True,
                destination=None,
            )

        regenerate_files()

        transport = (self.generated_dir / "transport_maps").read_text(encoding="utf-8")
        catchall = (self.generated_dir / "virtual_mailbox_catchall").read_text(encoding="utf-8")

        self.assertNotIn("@node.st.example", transport)
        self.assertNotIn("node.st.example", catchall)

    def test_disabled_domain_has_no_catch_all(self) -> None:
        with db_module.db() as conn:
            self._insert_domain(
                conn,
                "off.example",
                relay_all_inbound=True,
                enabled=False,
                destination=("relay.backend", 25),
            )

        regenerate_files()

        transport = (self.generated_dir / "transport_maps").read_text(encoding="utf-8")
        catchall = (self.generated_dir / "virtual_mailbox_catchall").read_text(encoding="utf-8")

        self.assertNotIn("@off.example", transport)
        self.assertNotIn("off.example", catchall)

    def test_relay_source_ips_generates_sender_and_cidr_maps(self) -> None:
        with db_module.db() as conn:
            self._insert_domain(
                conn,
                "relay.example",
                relay_source_ips="203.0.113.0/24\n203.0.113.10/32",
            )

        regenerate_files()

        sender_map = (self.generated_dir / "relay_sender_access").read_text(encoding="utf-8")
        cidr_map = (
            self.generated_dir / "relay_client_access_relay_example.cidr"
        ).read_text(encoding="utf-8")

        self.assertIn("@relay.example\tcheck_client_access cidr:", sender_map)
        self.assertIn("relay_client_access_relay_example.cidr", sender_map)
        self.assertIn("203.0.113.0/24\tOK", cidr_map)
        self.assertIn("203.0.113.10/32\tOK", cidr_map)

    def test_relay_source_ips_removed_when_domain_updated(self) -> None:
        with db_module.db() as conn:
            self._insert_domain(
                conn,
                "gone.example",
                relay_source_ips="10.0.0.1",
            )

        regenerate_files()
        stale = self.generated_dir / "relay_client_access_gone_example.cidr"
        self.assertTrue(stale.exists())

        with db_module.db() as conn:
            conn.execute(
                "UPDATE domains SET relay_source_ips = NULL WHERE name = 'gone.example'"
            )
            conn.commit()

        regenerate_files()
        self.assertFalse(stale.exists())
