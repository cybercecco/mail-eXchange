import unittest

from fastapi import HTTPException

from app.relay_ips import (
    MAX_RELAY_SOURCE_IPS,
    normalize_relay_source_ips,
    parse_relay_source_ips_text,
    relay_client_access_filename,
    relay_restriction_class_name,
    relay_source_ips_from_db,
    relay_source_ips_to_db,
)


class RelayIpsParseTest(unittest.TestCase):
    def test_parse_multiline_and_commas(self) -> None:
        text = "192.168.1.1\n10.0.0.0/8, 2001:db8::1/64"
        self.assertEqual(
            parse_relay_source_ips_text(text),
            ["192.168.1.1", "10.0.0.0/8", "2001:db8::1/64"],
        )

    def test_normalize_canonicalizes_host_to_cidr(self) -> None:
        result = normalize_relay_source_ips(["192.168.1.10", "192.168.1.10/32"])
        self.assertEqual(result, ["192.168.1.10/32"])

    def test_normalize_accepts_ipv6(self) -> None:
        result = normalize_relay_source_ips(["2001:db8::1"])
        self.assertEqual(result, ["2001:db8::1/128"])

    def test_normalize_rejects_invalid(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            normalize_relay_source_ips(["not-an-ip"])
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("Invalid relay source IP", ctx.exception.detail)

    def test_normalize_rejects_too_many(self) -> None:
        ips = [f"10.0.{i // 256}.{i % 256}" for i in range(MAX_RELAY_SOURCE_IPS + 1)]
        with self.assertRaises(HTTPException) as ctx:
            normalize_relay_source_ips(ips)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("At most", ctx.exception.detail)

    def test_db_roundtrip(self) -> None:
        ips = ["10.0.0.0/8", "203.0.113.5/32"]
        stored = relay_source_ips_to_db(ips)
        self.assertEqual(relay_source_ips_from_db(stored), ips)
        self.assertIsNone(relay_source_ips_to_db([]))

    def test_client_access_filename(self) -> None:
        self.assertEqual(
            relay_client_access_filename("Example.COM"),
            "relay_client_access_example_com.cidr",
        )

    def test_restriction_class_name(self) -> None:
        self.assertEqual(
            relay_restriction_class_name("vetrobalsamo.com"),
            "relay_vetrobalsamo_com",
        )


if __name__ == "__main__":
    unittest.main()
