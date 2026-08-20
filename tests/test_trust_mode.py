from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from zen_agent_bot.store import ConfigStore
from zen_agent_bot.trust_mode import (
    TRUST_APPROVE,
    TRUST_FORCE,
    format_trust_status,
    parse_trust_arg,
    resolve_trust,
)


class ParseTrustArgTests(unittest.TestCase):
    def test_show_tokens(self) -> None:
        self.assertEqual(parse_trust_arg(None), ("show", None))
        self.assertEqual(parse_trust_arg("list"), ("show", None))

    def test_set_tokens(self) -> None:
        self.assertEqual(parse_trust_arg("force"), ("set", TRUST_FORCE))
        self.assertEqual(parse_trust_arg("approve"), ("set", TRUST_APPROVE))

    def test_clear_tokens(self) -> None:
        self.assertEqual(parse_trust_arg("clear"), ("clear", None))
        self.assertEqual(parse_trust_arg("default"), ("clear", None))


class ResolveTrustTests(unittest.TestCase):
    def test_thread_override_wins(self) -> None:
        resolved = resolve_trust("approve", backend="cursor-sdk")
        self.assertEqual(resolved.mode, TRUST_APPROVE)
        self.assertEqual(resolved.source, "thread")

    def test_non_sdk_forces_mode(self) -> None:
        resolved = resolve_trust("approve", backend="cursor-cli")
        self.assertEqual(resolved.mode, TRUST_APPROVE)
        self.assertEqual(resolved.source, "thread")
        text = format_trust_status(resolved, backend="cursor-cli")
        self.assertIn("only applies to cursor-sdk", text)


class TrustModeStoreTests(unittest.TestCase):
    def test_persist_session_trust_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = ConfigStore(Path(tmp) / "gateway.db")
            db.set_session_trust_mode("k", "approve")
            row = db.get_session("k")
            self.assertEqual(row["trust_mode"], "approve")
            db.set_session_trust_mode("k", None)
            row2 = db.get_session("k")
            self.assertIsNone(row2["trust_mode"])
            db.close()


if __name__ == "__main__":
    unittest.main()
