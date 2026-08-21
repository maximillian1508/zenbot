from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from zen_agent_bot.notify import format_rebuild_done_ping
from zen_agent_bot.util.rebuild import (
    RebuildNotify,
    clear_rebuild_notify,
    load_rebuild_notify,
    rebuild_notify_path,
    request_rebuild,
    save_rebuild_notify,
)


class RebuildNotifyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_save_load_clear(self) -> None:
        notify = RebuildNotify(
            transport="discord",
            channel_id="123",
            user_id="456",
            mention="<@456>",
            agent_id="manager",
        )
        save_rebuild_notify(self.data_dir, notify)
        self.assertTrue(rebuild_notify_path(self.data_dir).is_file())
        loaded = load_rebuild_notify(self.data_dir)
        assert loaded is not None
        self.assertEqual(loaded.transport, "discord")
        self.assertEqual(loaded.channel_id, "123")
        clear_rebuild_notify(self.data_dir)
        self.assertIsNone(load_rebuild_notify(self.data_dir))

    def test_request_rebuild_writes_notify(self) -> None:
        notify = RebuildNotify(
            transport="telegram",
            channel_id="99",
            user_id="1",
            mention="Maxi",
            agent_id="manager",
        )
        request_rebuild(self.data_dir, reason="test", notify=notify)
        loaded = load_rebuild_notify(self.data_dir)
        assert loaded is not None
        self.assertEqual(loaded.transport, "telegram")
        self.assertEqual(loaded.mention, "Maxi")

    def test_format_ping(self) -> None:
        self.assertEqual(
            format_rebuild_done_ping(mention="<@1>"),
            "✅ Rebuild complete <@1> — gateway is back (`/health` OK).",
        )
        self.assertIn("/health", format_rebuild_done_ping(mention=None))


if __name__ == "__main__":
    unittest.main()
