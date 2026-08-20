from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from zen_agent_bot.store import ConfigStore


class RouteBindingStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = ConfigStore(Path(self.tmp.name) / "gateway.db")
        self.db.upsert_agent(
            {
                "id": "manager",
                "display_name": "Manager",
                "workspace": "/home/maxi",
                "default_backend": "cursor-cli",
                "skills": [],
                "system_prompt_file": None,
                "is_manager": True,
                "discord_enabled": True,
                "discord_channel_id": "100",
                "discord_token_env": "DISCORD_TOKEN",
                "telegram_enabled": False,
            }
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_upsert_and_lookup(self) -> None:
        self.db.upsert_route_binding(
            {
                "id": "discord-200",
                "transport": "discord",
                "channel_id": "200",
                "agent_id": "manager",
                "workspace": "/tmp/ws",
                "backend": "cursor-sdk",
                "enabled": True,
                "note": "test",
            }
        )
        row = self.db.binding_for_channel("discord", 200)
        assert row is not None
        self.assertEqual(row["agent_id"], "manager")
        self.assertEqual(row["workspace"], "/tmp/ws")
        self.assertEqual(row["backend"], "cursor-sdk")
        self.assertTrue(row["enabled"])

    def test_disabled_binding_hidden(self) -> None:
        self.db.upsert_route_binding(
            {
                "id": "discord-200",
                "channel_id": "200",
                "agent_id": "manager",
                "enabled": False,
            }
        )
        self.assertIsNone(self.db.binding_for_channel("discord", 200))
        self.db.set_route_binding_enabled("discord-200", True)
        self.assertIsNotNone(self.db.binding_for_channel("discord", 200))

    def test_home_channel_ids(self) -> None:
        self.assertEqual(self.db.home_channel_ids(), {"100"})


if __name__ == "__main__":
    unittest.main()
