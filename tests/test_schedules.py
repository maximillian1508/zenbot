from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from zen_agent_bot.schedule import (
    DEFAULT_TZ,
    format_schedules_markdown,
    next_run_utc,
    slug_id,
    validate_cron,
    validate_timezone,
)
from zen_agent_bot.store import ConfigStore


class CronValidateTests(unittest.TestCase):
    def test_ok(self) -> None:
        self.assertEqual(validate_cron(" 0  9 * * * "), "0 9 * * *")

    def test_rejects_bad(self) -> None:
        with self.assertRaises(ValueError):
            validate_cron("0 9 * *")
        with self.assertRaises(ValueError):
            validate_cron("not a cron")

    def test_timezone(self) -> None:
        self.assertEqual(validate_timezone("Asia/Singapore"), "Asia/Singapore")
        with self.assertRaises(ValueError):
            validate_timezone("Not/AZone")

    def test_slug(self) -> None:
        self.assertEqual(slug_id("Morning Health!"), "morning-health")


class NextRunTests(unittest.TestCase):
    def test_next_is_in_the_future(self) -> None:
        after = datetime(2026, 8, 13, 8, 0, tzinfo=ZoneInfo(DEFAULT_TZ))
        nxt = next_run_utc("0 9 * * *", DEFAULT_TZ, after=after)
        self.assertEqual(nxt.astimezone(ZoneInfo(DEFAULT_TZ)).hour, 9)
        self.assertGreater(nxt, after.astimezone(timezone.utc))


class StoreScheduleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = ConfigStore(Path(self.tmp.name) / "gateway.db")

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def test_crud_and_due(self) -> None:
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        self.db.upsert_schedule(
            {
                "id": "health",
                "name": "Health",
                "agent_id": "manager",
                "cron_expr": "0 9 * * *",
                "timezone": DEFAULT_TZ,
                "prompt": "curl health",
                "enabled": True,
                "next_run_at": past,
            }
        )
        due = self.db.due_schedules(datetime.now(timezone.utc).isoformat())
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["id"], "health")
        self.db.mark_schedule_running(
            "health",
            next_run_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            thread_id="1",
            session_key="manager:discord:1",
            thread_url="https://discord.com/channels/1/1",
        )
        self.assertEqual(self.db.get_schedule("health")["last_status"], "running")
        self.assertEqual(self.db.due_schedules(datetime.now(timezone.utc).isoformat()), [])
        self.db.mark_schedule_done("health", ok=True)
        self.assertEqual(self.db.get_schedule("health")["last_status"], "ok")
        self.db.set_schedule_enabled("health", False)
        self.assertFalse(self.db.get_schedule("health")["enabled"])
        self.db.delete_schedule("health")
        self.assertIsNone(self.db.get_schedule("health"))

    def test_reset_stuck(self) -> None:
        self.db.upsert_schedule(
            {
                "id": "stuck",
                "name": "Stuck",
                "agent_id": "manager",
                "cron_expr": "0 9 * * *",
                "timezone": DEFAULT_TZ,
                "prompt": "x",
                "enabled": True,
                "next_run_at": datetime.now(timezone.utc).isoformat(),
                "last_status": "running",
            }
        )
        n = self.db.reset_stuck_schedules()
        self.assertEqual(n, 1)
        self.assertEqual(self.db.get_schedule("stuck")["last_status"], "interrupted")

    def test_list_markdown(self) -> None:
        text = format_schedules_markdown([])
        self.assertIn("No schedules", text)
        text = format_schedules_markdown(
            [
                {
                    "id": "health",
                    "name": "Health",
                    "cron_expr": "0 9 * * *",
                    "timezone": DEFAULT_TZ,
                    "agent_id": "manager",
                    "enabled": True,
                    "last_status": "ok",
                    "next_run_at": "2026-08-14T01:00:00+00:00",
                }
            ]
        )
        self.assertIn("`health`", text)
        self.assertIn("0 9 * * *", text)


if __name__ == "__main__":
    unittest.main()
