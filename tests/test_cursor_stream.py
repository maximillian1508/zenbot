from __future__ import annotations

import unittest

from zen_agent_bot.backends.cursor_cli import (
    _apply_assistant_event,
    _is_streaming_delta,
    _merge_assistant_delta,
)


def _asst(text: str, *, ts: int | None = 1, model_call_id: str | None = None) -> dict:
    event: dict = {
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }
    if ts is not None:
        event["timestamp_ms"] = ts
    if model_call_id is not None:
        event["model_call_id"] = model_call_id
    return event


class CursorStreamDedupeTests(unittest.TestCase):
    def test_is_streaming_delta_table(self) -> None:
        self.assertTrue(_is_streaming_delta({"timestamp_ms": 1}))
        self.assertFalse(_is_streaming_delta({"timestamp_ms": 1, "model_call_id": "abc"}))
        self.assertFalse(_is_streaming_delta({}))

    def test_token_deltas_concatenate(self) -> None:
        text = ""
        for piece in ("I'll check", " the screenshot", " and look up usage."):
            merged = _apply_assistant_event(text, _asst(piece))
            assert merged is not None
            text = merged
        self.assertEqual(text, "I'll check the screenshot and look up usage.")

    def test_short_repeated_tokens_still_append(self) -> None:
        text = _merge_assistant_delta("ha", "ha")
        self.assertEqual(text, "haha")

    def test_skips_model_call_id_flush(self) -> None:
        current = "I'll read the file."
        self.assertIsNone(
            _apply_assistant_event(current, _asst(current, model_call_id="call_1"))
        )

    def test_skips_final_flush_without_timestamp(self) -> None:
        current = "I'll read the file."
        self.assertIsNone(_apply_assistant_event(current, _asst(current, ts=None)))

    def test_skips_last_thought_replay_before_tool(self) -> None:
        thought = (
            "CLI is authenticated. Next I’ll hit Cursor’s usage endpoints "
            "with the local session (without printing tokens)."
        )
        prior = (
            "I'll check the screenshot and look up how to read your "
            "Cursor usage/spend from this machine. "
            "Dashboard screenshot shows 29% Cursor Models and 51% Other Models. "
            "I'll pull live usage from this machine's Cursor session so we can compare."
        )
        current = prior + thought
        self.assertIsNone(_apply_assistant_event(current, _asst(thought)))
        self.assertEqual(_merge_assistant_delta(current, thought), current)

    def test_cumulative_snapshot_replaces(self) -> None:
        current = "I'll check the screenshot"
        chunk = "I'll check the screenshot and look up usage."
        self.assertEqual(_merge_assistant_delta(current, chunk), chunk)

    def test_identical_snapshot_is_noop(self) -> None:
        text = "Same full thought already shown."
        self.assertIsNone(_apply_assistant_event(text, _asst(text)))
