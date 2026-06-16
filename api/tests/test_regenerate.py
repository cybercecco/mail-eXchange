import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import db as db_module
from app import mail_config as mail_config_module
from app.regenerate import regenerate_files


class RegenerateCatchAllTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        data_dir = Path(self.tmp.name)
        self.generated_dir = data_dir / "generated"
        mail_cfg = data_dir / "mail-config"
        self.postfix_generated = mail_cfg / "postfix" / "generated"
        self.db_path = data_dir / "mailrouter.db"
        self.patches = [
            patch.object(db_module, "DATA_DIR", data_dir),
            patch.object(db_module, "DB_PATH", self.db_path),
            patch.object(db_module, "GENERATED_DIR", self.generated_dir),
            patch.object(mail_config_module, "MAIL_CONFIG_DIR", mail_cfg),
            patch.object(mail_config_module, "POSTFIX_GENERATED_DIR", self.postfix_generated),
            patch.object(
                mail_config_module,
                "SPAMASSASSIN_LOCAL_CF",
                mail_cfg / "spamassassin" / "local.cf",
            ),
            patch.object(
                mail_config_module,
                "AMAVIS_SPAM_OVERRIDES",
                mail_cfg / "amavis" / "spam-overrides.conf",
            ),
            patch("app.regenerate.GENERATED_DIR", self.generated_dir),
            patch("app.regenerate.POSTFIX_GENERATED_DIR", self.postfix_generated),
            patch("app.regenerate.SPAMASSASSIN_LOCAL_CF", mail_cfg / "spamassassin" / "local.cf"),
            patch("app.regenerate.AMAVIS_SPAM_OVERRIDES", mail_cfg / "amavis" / "spam-overrides.conf"),
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

        transport = (self.postfix_generated / "transport_maps").read_text(encoding="utf-8")
        self.assertIn("multi.example smtp:[first.backend]:2525", transport)
        self.assertNotIn("multi.example smtp:[second.backend]:26", transport)
        self.assertTrue(
            any("2 destinations configured" in message for message in logs.output)
        )

    def test_relay_all_domain_accepts_any_local_part(self) -> None:
        with db_module.db() as conn:
            self._insert_domain(
                conn,
                "iot.relay.example",
                relay_all_inbound=True,
                destination=("relay.backend", 2525),
            )
            conn.commit()

        regenerate_files()

        mailbox_maps = (
            self.postfix_generated / "virtual_mailbox_maps"
        ).read_text(encoding="utf-8")
        alias_domains = (
            self.postfix_generated / "virtual_alias_domains"
        ).read_text(encoding="utf-8")
        transport = (self.postfix_generated / "transport_maps").read_text(encoding="utf-8")

        self.assertIn("iot.relay.example OK", alias_domains)
        self.assertIn("@iot.relay.example OK", mailbox_maps)
        self.assertIn("iot.relay.example smtp:[relay.backend]:2525", transport)
        self.assertNotIn("\n@iot.relay.example smtp:", transport)
        catchall_transport = (
            self.postfix_generated / "transport_catchall"
        ).read_text(encoding="utf-8")
        self.assertIn("/^.+@iot\\.relay\\.example$/ smtp:[relay.backend]:2525", catchall_transport)

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

        transport = (self.postfix_generated / "transport_maps").read_text(encoding="utf-8")
        mailbox_maps = (
            self.postfix_generated / "virtual_mailbox_maps"
        ).read_text(encoding="utf-8")
        alias_domains = (
            self.postfix_generated / "virtual_alias_domains"
        ).read_text(encoding="utf-8")
        catchall = (self.postfix_generated / "virtual_mailbox_catchall").read_text(encoding="utf-8")

        self.assertIn("catchall.example smtp:[relay.backend]:2525", transport)
        self.assertIn("known@catchall.example smtp:[other.backend]:25", transport)
        self.assertIn("@catchall.example OK", mailbox_maps)
        self.assertIn("catchall.example OK", alias_domains)
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

        transport = (self.postfix_generated / "transport_maps").read_text(encoding="utf-8")
        catchall = (self.postfix_generated / "virtual_mailbox_catchall").read_text(encoding="utf-8")

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

        transport = (self.postfix_generated / "transport_maps").read_text(encoding="utf-8")
        catchall = (self.postfix_generated / "virtual_mailbox_catchall").read_text(encoding="utf-8")

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

        sender_map = (self.postfix_generated / "relay_sender_access").read_text(encoding="utf-8")
        class_map = (
            self.postfix_generated / "relay_restriction_classes"
        ).read_text(encoding="utf-8")
        cidr_map = (
            self.postfix_generated / "relay_client_access_relay_example.cidr"
        ).read_text(encoding="utf-8")

        self.assertIn("@relay.example\trelay_relay_example", sender_map)
        self.assertIn("relay_client_access_relay_example.cidr", class_map)
        self.assertIn("relay_relay_example check_client_access cidr:", class_map)
        self.assertIn("smtpd_restriction_classes relay_relay_example", class_map)
        self.assertIn("203.0.113.0/24\tOK", cidr_map)
        self.assertIn("203.0.113.10/32\tOK", cidr_map)
        relay_mynetworks = (
            self.postfix_generated / "relay_mynetworks.cidr"
        ).read_text(encoding="utf-8")
        self.assertIn("203.0.113.0/24\tOK", relay_mynetworks)
        self.assertIn("203.0.113.10/32\tOK", relay_mynetworks)

    def test_relay_source_ips_removed_when_domain_updated(self) -> None:
        with db_module.db() as conn:
            self._insert_domain(
                conn,
                "gone.example",
                relay_source_ips="10.0.0.1",
            )

        regenerate_files()
        stale = self.postfix_generated / "relay_client_access_gone_example.cidr"
        self.assertTrue(stale.exists())

        with db_module.db() as conn:
            conn.execute(
                "UPDATE domains SET relay_source_ips = NULL WHERE name = 'gone.example'"
            )
            conn.commit()

        regenerate_files()
        self.assertFalse(stale.exists())

    def test_postmaster_routes_to_admin_notify_email(self) -> None:
        with db_module.db() as conn:
            self._insert_domain(
                conn,
                "client.example",
                destination=("mail.backend", 2525),
            )
            conn.execute(
                """
                INSERT INTO users (username, password_hash, role, totp_secret, mfa_enabled, notify_email)
                VALUES ('admin', 'hash', 'admin', NULL, 0, 'Ops.Notify@Example.COM')
                """
            )
            conn.commit()

        with patch("app.regenerate.POSTFIX_HOSTNAME", "mx.example.com"):
            regenerate_files()

        alias_maps = (
            self.postfix_generated / "virtual_alias_maps"
        ).read_text(encoding="utf-8")
        transport = (self.postfix_generated / "transport_maps").read_text(encoding="utf-8")
        amavis = (
            self.postfix_generated.parent.parent / "amavis" / "spam-overrides.conf"
        )
        amavis_overrides = amavis.read_text(encoding="utf-8")

        self.assertIn("postmaster@mx.example.com ops.notify@example.com", alias_maps)
        self.assertIn("postmaster@client.example ops.notify@example.com", alias_maps)
        self.assertNotIn("postmaster@client.example smtp:[mail.backend]", transport)
        self.assertIn("ops.notify@example.com smtp:", transport)
        self.assertIn("@virus_admin = qw( ops.notify@example.com );", amavis_overrides)

    def test_postmaster_rejected_without_admin_notify_email(self) -> None:
        with db_module.db() as conn:
            self._insert_domain(
                conn,
                "solo.example",
                destination=("solo.backend", 25),
            )
            conn.commit()

        with (
            patch("app.regenerate.POSTFIX_HOSTNAME", "mx.example.com"),
            self.assertLogs("app.regenerate", level="WARNING") as logs,
        ):
            regenerate_files()

        transport = (self.postfix_generated / "transport_maps").read_text(encoding="utf-8")
        self.assertIn(
            "postmaster@mx.example.com error:5.1.3 Postmaster forwarding is not configured",
            transport,
        )
        self.assertIn(
            "postmaster@solo.example error:5.1.3 Postmaster forwarding is not configured",
            transport,
        )
        self.assertTrue(
            any("no admin notify_email configured" in message for message in logs.output)
        )

    def test_regenerate_writes_postfix_main_override(self) -> None:
        with db_module.db() as conn:
            conn.execute(
                """
                UPDATE postfix_settings
                SET json_payload = ?
                WHERE id = 1
                """,
                (
                    json.dumps(
                        {
                            "message_size_limit": 20_971_520,
                            "mailbox_size_limit": 62_914_560,
                            "smtpd_timeout": 180,
                        }
                    ),
                ),
            )
            conn.commit()

        regenerate_files()

        override_path = self.postfix_generated / "main.cf.override"
        self.assertTrue(override_path.is_file())
        content = override_path.read_text(encoding="utf-8")
        self.assertIn("message_size_limit = 20971520", content)
        self.assertIn("mailbox_size_limit = 62914560", content)
        self.assertIn("smtpd_timeout = 180s", content)
