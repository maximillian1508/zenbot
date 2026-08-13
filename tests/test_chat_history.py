from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from zen_agent_bot.chat_history import (
    CHAT_TURN_MAX,
    build_openrouter_messages,
    clip_turn,
    window_turns,
)
from zen_agent_bot.store import ConfigStore


class ChatHistoryWindowTests(unittest.TestCase):
    def test_clip_turn(self) -> None:
        self.assertEqual(clip_turn("  hi  "), "hi")
        self.assertTrue(clip_turn("x" * 5000).endswith("…"))
        self.assertLessEqual(len(clip_turn("x" * 5000)), 4000)

    def test_window_drops_oldest(self) -> None:
        turns = [{"role": "user", "content": f"u{i}"} for i in range(25)]
        out = window_turns(turns, max_turns=4)
        self.assertEqual([t["content"] for t in out], ["u21", "u22", "u23", "u24"])

    def test_window_char_budget(self) -> None:
        turns = [
            {"role": "user", "content": "aaaa"},
            {"role": "assistant", "content": "bbbb"},
            {"role": "user", "content": "cccc"},
        ]
        out = window_turns(turns, max_turns=20, max_chars=8)
        self.assertEqual([t["content"] for t in out], ["bbbb", "cccc"])

    def test_build_messages_includes_history(self) -> None:
        messages = build_openrouter_messages(
            system="sys",
            history=[
                {"role": "user", "content": "tokyo january"},
                {"role": "assistant", "content": "assumed august"},
            ],
            prompt="no i meant january 2027",
        )
        self.assertEqual(messages[0], {"role": "system", "content": "sys"})
        self.assertEqual(messages[1]["content"], "tokyo january")
        self.assertEqual(messages[-1]["content"], "no i meant january 2027")
        self.assertEqual(len(messages), 4)


class ChatTurnsStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = ConfigStore(Path(self.tmp.name) / "gateway.db")

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def test_append_list_and_new_clears(self) -> None:
        self.db.append_chat_turn("s1", "user", "hello tokyo")
        self.db.append_chat_turn("s1", "assistant", "august events")
        turns = self.db.list_chat_turns("s1")
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[0]["role"], "user")
        self.db.reset_session_resume("s1")
        self.assertEqual(self.db.list_chat_turns("s1"), [])

    def test_clear_session_drops_turns(self) -> None:
        self.db.append_chat_turn("s1", "user", "keep?")
        self.db.clear_session("s1")
        self.assertEqual(self.db.list_chat_turns("s1"), [])

    def test_prune_caps_count(self) -> None:
        for i in range(CHAT_TURN_MAX + 6):
            self.db.append_chat_turn("s1", "user", f"msg-{i}")
        turns = self.db.list_chat_turns("s1")
        self.assertEqual(len(turns), CHAT_TURN_MAX)
        self.assertEqual(turns[-1]["content"], f"msg-{CHAT_TURN_MAX + 5}")

    def test_skips_empty_and_bad_role(self) -> None:
        self.db.append_chat_turn("s1", "system", "nope")
        self.db.append_chat_turn("s1", "user", "   ")
        self.assertEqual(self.db.list_chat_turns("s1"), [])


if __name__ == "__main__":
    unittest.main()
