from __future__ import annotations

import unittest

from zen_agent_bot.handoff import (
    clip_line,
    format_handoff_prompt,
    format_transcript_lines,
)
from zen_agent_bot.transports.discord import RESERVED_SLASH


class HandoffFormatTests(unittest.TestCase):
    def test_clip_line(self) -> None:
        self.assertEqual(clip_line("  hello   world  "), "hello world")
        self.assertTrue(clip_line("x" * 900).endswith("…"))
        self.assertLessEqual(len(clip_line("x" * 900)), 800)

    def test_transcript_drops_oldest_when_over_budget(self) -> None:
        rows = [(f"u{i}", "word " * 50) for i in range(20)]
        text = format_transcript_lines(rows, max_chars=400)
        self.assertIn("u19:", text)
        self.assertNotIn("u0:", text)

    def test_prompt_with_note(self) -> None:
        prompt = format_handoff_prompt(
            source_agent="general",
            source_title="cron · IDX",
            source_url="https://discord.com/channels/1/2",
            target_display="Zen Manager",
            note="will future cron threads be public?",
            transcript="maxi: hello\nbot: hi",
        )
        self.assertIn("`general`", prompt)
        self.assertIn("cron · IDX", prompt)
        self.assertIn("User note: will future cron threads be public?", prompt)
        self.assertIn("You are **Zen Manager**", prompt)
        self.assertIn("maxi: hello", prompt)

    def test_prompt_without_note(self) -> None:
        prompt = format_handoff_prompt(
            source_agent="music",
            source_title="",
            source_url="",
            target_display="Zen Manager",
            note="  ",
            transcript="",
        )
        self.assertIn("Continue from the latest user question", prompt)
        self.assertIn("(empty thread)", prompt)
        self.assertNotIn("User note:", prompt)

    def test_handoff_is_reserved_slash(self) -> None:
        self.assertIn("handoff", RESERVED_SLASH)


if __name__ == "__main__":
    unittest.main()
