from __future__ import annotations

import unittest
from pathlib import Path

from zen_agent_bot.agents.profile import AgentProfile, DiscordBinding
from zen_agent_bot.transports.discord import (
    RESERVED_SLASH,
    allowlist_mentions,
    channel_profile_map,
    group_profiles_by_token,
    resolve_agent_name,
    resolve_agent_profile,
)


def _profile(
    pid: str,
    token: str,
    channel: int,
    *,
    manager: bool = False,
    display_name: str | None = None,
) -> AgentProfile:
    return AgentProfile(
        id=pid,
        display_name=display_name or pid.title(),
        workspace=Path("/tmp"),
        default_backend="cursor-cli",
        skills=(),
        system_prompt_file=None,
        is_manager=manager,
        discord=DiscordBinding(token=token, agent_channel_id=channel),
        telegram=None,
    )


class DiscordRouteTests(unittest.TestCase):
    def test_group_same_token(self) -> None:
        manager = _profile("manager", "tok-a", 1, manager=True)
        music = _profile("music", "tok-a", 2)
        groups = group_profiles_by_token([manager, music])
        self.assertEqual(len(groups), 1)
        self.assertEqual([p.id for p in groups[0][1]], ["manager", "music"])

    def test_group_distinct_tokens(self) -> None:
        a = _profile("manager", "tok-a", 1, manager=True)
        b = _profile("music", "tok-b", 2)
        groups = group_profiles_by_token([a, b])
        self.assertEqual(len(groups), 2)

    def test_resolve_home_and_thread(self) -> None:
        manager = _profile("manager", "tok", 10, manager=True)
        music = _profile("music", "tok", 20)
        by_ch = channel_profile_map([manager, music])
        self.assertEqual(
            resolve_agent_profile(by_ch, channel_id=10, parent_id=None).id,  # type: ignore[union-attr]
            "manager",
        )
        self.assertEqual(
            resolve_agent_profile(by_ch, channel_id=99, parent_id=20).id,  # type: ignore[union-attr]
            "music",
        )
        self.assertIsNone(
            resolve_agent_profile(by_ch, channel_id=99, parent_id=None)
        )

    def test_reserved_slash_names(self) -> None:
        self.assertIn("run", RESERVED_SLASH)
        self.assertNotIn("music", RESERVED_SLASH)
        self.assertNotIn("general", RESERVED_SLASH)

    def test_resolve_agent_name_accepts_labels(self) -> None:
        manager = _profile(
            "manager", "tok", 1, manager=True, display_name="Zen Manager"
        )
        general = _profile("general", "tok", 2, display_name="Zen General")
        fleet = [manager, general]
        self.assertEqual(resolve_agent_name("manager", fleet).id, "manager")  # type: ignore[union-attr]
        self.assertEqual(resolve_agent_name("Zen Manager", fleet).id, "manager")  # type: ignore[union-attr]
        self.assertEqual(
            resolve_agent_name("Zen Manager (manager)", fleet).id,  # type: ignore[union-attr]
            "manager",
        )
        self.assertEqual(
            resolve_agent_name("manager · Zen Manager", fleet).id,  # type: ignore[union-attr]
            "manager",
        )
        self.assertIsNone(resolve_agent_name("nope", fleet))

    def test_allowlist_mentions(self) -> None:
        self.assertIsNone(allowlist_mentions([]))
        self.assertEqual(allowlist_mentions([443644232234696714]), "<@443644232234696714>")
        self.assertEqual(
            allowlist_mentions([1, 2]),
            "<@1> <@2>",
        )


if __name__ == "__main__":
    unittest.main()
