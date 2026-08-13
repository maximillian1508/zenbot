from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from zen_agent_bot.notify import (
    append_status_line,
    format_close_reply,
    format_duration,
    format_job_done_ping,
    should_ping_done,
)
from zen_agent_bot.store import ConfigStore


class DurationTests(unittest.TestCase):
    def test_seconds(self) -> None:
        self.assertEqual(format_duration(9), "9s")

    def test_minutes(self) -> None:
        self.assertEqual(format_duration(60), "1m")
        self.assertEqual(format_duration(75), "1m 15s")

    def test_hours(self) -> None:
        self.assertEqual(format_duration(3661), "1h 1m")


class DonePingTests(unittest.TestCase):
    def test_success_mentions(self) -> None:
        text = format_job_done_ping(
            mention="<@42>", exit_code=0, error=None, elapsed_sec=12
        )
        self.assertEqual(text, "✅ Done <@42> · 12s")

    def test_cancelled(self) -> None:
        text = format_job_done_ping(
            mention="<@42>", exit_code=130, error="cancelled", elapsed_sec=60
        )
        self.assertEqual(text, "🛑 Cancelled <@42> · 1m")

    def test_no_mention(self) -> None:
        text = format_job_done_ping(
            mention=None, exit_code=0, error=None, elapsed_sec=5
        )
        self.assertEqual(text, "✅ Done · 5s")

    def test_skip_send_now(self) -> None:
        self.assertFalse(
            should_ping_done(
                error="cancelled",
                cancel_reason="stopped by Send now (queued follow-up)",
                elapsed_sec=90,
            )
        )
        self.assertFalse(
            should_ping_done(
                error="cancelled",
                cancel_reason="stopped by /close",
                elapsed_sec=90,
            )
        )
        self.assertTrue(
            should_ping_done(
                error="cancelled",
                cancel_reason="stopped by /cancel",
                elapsed_sec=90,
            )
        )

    def test_skip_short_success(self) -> None:
        self.assertFalse(
            should_ping_done(error=None, cancel_reason="", elapsed_sec=25)
        )
        self.assertTrue(
            should_ping_done(error=None, cancel_reason="", elapsed_sec=60)
        )

    def test_always_ping_errors(self) -> None:
        self.assertTrue(
            should_ping_done(error="boom", cancel_reason="", elapsed_sec=5)
        )

    def test_disabled(self) -> None:
        self.assertFalse(
            should_ping_done(
                error=None, cancel_reason="", elapsed_sec=120, enabled=False
            )
        )

    def test_append_keeps_body(self) -> None:
        out = append_status_line(
            "✅ **Zen Manager**\n\nhello", "✅ Done <@1> · 1m 29s"
        )
        self.assertIn("hello", out)
        self.assertTrue(out.endswith("✅ Done <@1> · 1m 29s"))

    def test_append_truncates_long_body(self) -> None:
        body = "x" * 2000
        ping = "✅ Done · 1m"
        out = append_status_line(body, ping, limit=100)
        self.assertLessEqual(len(out), 100)
        self.assertTrue(out.endswith(ping))


class CloseReplyTests(unittest.TestCase):
    def test_idle(self) -> None:
        text = format_close_reply(cancelled=False, dropped=0)
        self.assertIn("Session closed.", text)
        self.assertIn("/model", text)

    def test_busy(self) -> None:
        text = format_close_reply(cancelled=True, dropped=2)
        self.assertIn("Stopped the running job.", text)
        self.assertIn("Dropped 2 queued messages.", text)


class PruneSessionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = ConfigStore(Path(self.tmp.name) / "gateway.db")

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def test_prune_empty_keeps_resume(self) -> None:
        self.db.set_session("live", "sess-1", "keep")
        self.db.set_session_model("empty", "composer-2.5")
        self.assertEqual(self.db.prune_empty_sessions(), 1)
        self.assertEqual(self.db.get_session("live")["session_id"], "sess-1")
        self.assertIsNone(self.db.get_session("empty")["model"])


if __name__ == "__main__":
    unittest.main()
