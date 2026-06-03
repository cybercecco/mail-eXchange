import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app import traffic_stats as traffic_module
from app.traffic_stats import (
    _collect_blocked_messages,
    _collect_incoming_messages,
    _collect_outgoing_messages,
    collect_queue_listing,
    collect_traffic_stats,
    read_pipeline_snapshot,
)


def _postfix_line(day: str, time: str, body: str) -> str:
    return f"May {day} {time} mx postfix/{body}"


def _amavis_line(date: str, time: str, body: str) -> str:
    return f"{date} {time} mx amavis[12345]: {body}"


class TrafficStatsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.logs_dir = Path(self.tmp.name) / "logs"
        self.stats_dir = Path(self.tmp.name) / "stats"
        self.logs_dir.mkdir()
        self.stats_dir.mkdir()
        self.postfix_log = self.logs_dir / "postfix.log"
        self.amavis_log = self.logs_dir / "amavis.log"
        self.queue_snapshot = self.stats_dir / "queue.json"

        self.patches = [
            patch.object(traffic_module, "LOGS_DIR", self.logs_dir),
            patch.object(traffic_module, "STATS_DIR", self.stats_dir),
            patch.object(traffic_module, "QUEUE_SNAPSHOT", self.queue_snapshot),
        ]
        for item in self.patches:
            item.start()

        self.ref = datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone.utc)
        patch_now = patch.object(traffic_module, "_now", return_value=self.ref)
        patch_now.start()
        self.patches.append(patch_now)

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def _write_queue(self, *, active: int = 0, deferred: int = 0, hold: int = 0) -> None:
        self.queue_snapshot.write_text(
            json.dumps(
                {
                    "total": active + deferred + hold,
                    "active": active,
                    "deferred": deferred,
                    "hold": hold,
                    "updated_at": self.ref.isoformat(),
                    "messages": {"active": [], "deferred": [], "hold": []},
                }
            ),
            encoding="utf-8",
        )

    def test_ingresso_dedupes_by_queue_id(self) -> None:
        self.postfix_log.write_text(
            "\n".join(
                [
                    _postfix_line("28", "11:50:00", "smtpd[1]: ABC123: client=1.2.3.4"),
                    _postfix_line("28", "11:50:01", "smtpd[1]: ABC123: client=1.2.3.4"),
                    _postfix_line("28", "11:51:00", "smtpd[1]: DEF456: client=5.6.7.8"),
                ]
            ),
            encoding="utf-8",
        )
        self._write_queue(active=1)

        stats = collect_traffic_stats(window_minutes=60)
        self.assertEqual(stats["ingresso"], 2)
        cutoff = self.ref - timedelta(minutes=60)
        self.assertEqual(len(_collect_incoming_messages(60, self.ref, cutoff)), 2)

    def test_in_uscita_dedupes_by_queue_id(self) -> None:
        self.postfix_log.write_text(
            "\n".join(
                [
                    _postfix_line(
                        "28",
                        "11:50:00",
                        "smtp[1]: ABC123: to=<a@example.com>, relay=mx.example.com[1.2.3.4]:25, status=sent (250 ok)",
                    ),
                    _postfix_line(
                        "28",
                        "11:50:01",
                        "smtp[1]: ABC123: to=<b@example.com>, relay=mx.example.com[1.2.3.4]:25, status=sent (250 ok)",
                    ),
                    _postfix_line(
                        "28",
                        "11:51:00",
                        "smtp[1]: DEF456: to=<c@example.com>, relay=127.0.0.1[127.0.0.1]:10025, status=sent (250 ok)",
                    ),
                ]
            ),
            encoding="utf-8",
        )
        self._write_queue()

        stats = collect_traffic_stats(window_minutes=60)
        self.assertEqual(stats["in_uscita"], 1)
        self.assertEqual(len(_collect_outgoing_messages(60, self.ref, self.ref - timedelta(minutes=60))), 1)

    def test_in_coda_sums_pipeline_transit(self) -> None:
        self.postfix_log.write_text("", encoding="utf-8")
        self.queue_snapshot.write_text(
            json.dumps(
                {
                    "total": 109,
                    "active": 7,
                    "deferred": 99,
                    "hold": 3,
                    "updated_at": self.ref.isoformat(),
                    "pipeline": {"postfix_to_amavis": 2, "postfix_outbound": 1, "postfix_local": 4},
                    "messages": {"active": [], "deferred": [], "hold": []},
                }
            ),
            encoding="utf-8",
        )

        stats = collect_traffic_stats(window_minutes=60)
        self.assertEqual(stats["in_coda"], 109)
        self.assertEqual(stats["pipeline"]["postfix_active"], 4)
        self.assertEqual(stats["pipeline"]["postfix_to_amavis"], 2)
        self.assertEqual(stats["queue_detail"]["deferred"], 99)

    def test_window_minutes_filters_old_events(self) -> None:
        self.postfix_log.write_text(
            "\n".join(
                [
                    _postfix_line("28", "10:00:00", "smtpd[1]: A0EE01: client=1.2.3.4"),
                    _postfix_line("28", "11:55:00", "smtpd[1]: A0EE02: client=5.6.7.8"),
                ]
            ),
            encoding="utf-8",
        )
        self._write_queue()

        stats_15 = collect_traffic_stats(window_minutes=15)
        stats_360 = collect_traffic_stats(window_minutes=360)
        self.assertEqual(stats_15["ingresso"], 1)
        self.assertEqual(stats_360["ingresso"], 2)
        self.assertEqual(stats_15["window_minutes"], 15)

    def test_amavis_pipeline_counts_inflight_stages(self) -> None:
        self.amavis_log.write_text(
            "\n".join(
                [
                    _amavis_line("2026-05-28", "11:58:00", "(10001-01) ESMTP from [1.2.3.4]:25"),
                    _amavis_line("2026-05-28", "11:58:01", "(10001-01) ClamAV-clamd: All checks passed"),
                    _amavis_line("2026-05-28", "11:58:02", "(10002-02) ESMTP from [1.2.3.4]:25"),
                    _amavis_line("2026-05-28", "11:58:03", "(10002-02) SpamControl: score=1.2"),
                    _amavis_line("2026-05-28", "11:58:04", "(10003-03) Passed CLEAN"),
                ]
            ),
            encoding="utf-8",
        )
        self._write_queue()

        pipeline = read_pipeline_snapshot()["pipeline"]
        self.assertEqual(pipeline["clamav"], 1)
        self.assertEqual(pipeline["spamassassin"], 1)
        self.assertEqual(pipeline["amavis"], 0)

    def test_bloccate_excludes_warnings_and_counts_unique_messages(self) -> None:
        self.postfix_log.write_text(
            "\n".join(
                [
                    _postfix_line("28", "11:40:00", "smtpd[1]: warning: dict_nis_init: NIS domain name not set"),
                    _postfix_line(
                        "28",
                        "11:41:00",
                        "smtpd[1]: warning: hostname lookup does not match IP address",
                    ),
                    _postfix_line(
                        "28",
                        "11:42:00",
                        "smtpd[1]: NOQUEUE: reject: RCPT from unknown[1.2.3.4]: 550 5.1.1 User unknown; from=<spam@evil.com> to=<user@example.com>",
                    ),
                    _postfix_line(
                        "28",
                        "11:43:00",
                        "smtpd[1]: NOQUEUE: reject: RCPT from unknown[1.2.3.4]: 550 5.1.1 User unknown; from=<spam@evil.com> to=<user@example.com>",
                    ),
                    _postfix_line(
                        "28",
                        "11:44:00",
                        "smtp[1]: A1B2C3: to=<user@example.com>, status=bounced (host mx.example.com said: 550 blocked)",
                    ),
                ]
            ),
            encoding="utf-8",
        )
        self.amavis_log.write_text(
            "\n".join(
                [
                    _amavis_line(
                        "2026-05-28",
                        "11:45:00",
                        "(10001-01) Blocked SPAM, [1.2.3.4]:25 <spam@evil.com> -> <user@example.com>, quarantine: spam",
                    ),
                    _amavis_line(
                        "2026-05-28",
                        "11:45:01",
                        "(10001-01) Blocked SPAM, [1.2.3.4]:25 <spam@evil.com> -> <user@example.com>, quarantine: spam",
                    ),
                ]
            ),
            encoding="utf-8",
        )
        self._write_queue()

        stats = collect_traffic_stats(window_minutes=60)
        self.assertEqual(stats["bloccate"], 3)
        cutoff = self.ref - timedelta(minutes=60)
        blocked = _collect_blocked_messages(60, self.ref, cutoff)
        self.assertEqual(len(blocked), 3)
        listing = collect_queue_listing("blocked", window_minutes=60)
        self.assertEqual(listing["count"], 3)

    def test_bloccate_counts_qid_reject_once(self) -> None:
        self.postfix_log.write_text(
            "\n".join(
                [
                    _postfix_line(
                        "28",
                        "11:46:00",
                        "cleanup[1]: C0FFEE: milter-reject: 550 5.7.1 spam detected; from=<bad@evil.com> to=<user@example.com>",
                    ),
                    _postfix_line(
                        "28",
                        "11:46:01",
                        "cleanup[1]: C0FFEE: milter-reject: 550 5.7.1 spam detected; from=<bad@evil.com> to=<user@example.com>",
                    ),
                ]
            ),
            encoding="utf-8",
        )
        self._write_queue()

        stats = collect_traffic_stats(window_minutes=60)
        self.assertEqual(stats["bloccate"], 1)


if __name__ == "__main__":
    unittest.main()
