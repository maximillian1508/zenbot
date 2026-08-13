from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from zen_agent_bot.backend_select import (
    parse_backend_arg,
    resolve_backend,
)
from zen_agent_bot.store import ConfigStore

KNOWN = frozenset({"cursor-cli", "claude-cli", "openrouter"})


class ParseBackendArgTests(unittest.TestCase):
    def test_show_on_empty(self) -> None:
        self.assertEqual(parse_backend_arg(None, known=KNOWN), ("show", None))
        self.assertEqual(parse_backend_arg("", known=KNOWN), ("show", None))
        self.assertEqual(parse_backend_arg("list", known=KNOWN), ("show", None))

    def test_clear_tokens(self) -> None:
        for token in ("clear", "default", "none", "RESET"):
            self.assertEqual(parse_backend_arg(token, known=KNOWN), ("clear", None))

    def test_aliases(self) -> None:
        self.assertEqual(
            parse_backend_arg("cursor", known=KNOWN), ("set", "cursor-cli")
        )
        self.assertEqual(
            parse_backend_arg("claude", known=KNOWN), ("set", "claude-cli")
        )
        self.assertEqual(parse_backend_arg("or", known=KNOWN), ("set", "openrouter"))
        self.assertEqual(
            parse_backend_arg("  openrouter ", known=KNOWN), ("set", "openrouter")
        )

    def test_rejects_unknown(self) -> None:
        with self.assertRaises(ValueError):
            parse_backend_arg("codex", known=KNOWN)


class ResolveBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = ConfigStore(Path(self.tmp.name) / "gateway.db")

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def test_profile_default(self) -> None:
        resolved = resolve_backend(self.db, "missing", "cursor-cli", known=KNOWN)
        self.assertEqual(resolved.backend, "cursor-cli")
        self.assertEqual(resolved.source, "profile")

    def test_thread_beats_profile(self) -> None:
        self.db.set_session_backend("t1", "openrouter")
        resolved = resolve_backend(self.db, "t1", "cursor-cli", known=KNOWN)
        self.assertEqual(resolved.backend, "openrouter")
        self.assertEqual(resolved.source, "thread")

    def test_stale_override_falls_back(self) -> None:
        self.db.set_session_backend("t1", "gone-backend")
        resolved = resolve_backend(self.db, "t1", "claude-cli", known=KNOWN)
        self.assertEqual(resolved.backend, "claude-cli")
        self.assertEqual(resolved.source, "profile")

    def test_reset_resume_keeps_backend(self) -> None:
        self.db.set_session("t1", "sess-abc", "hello")
        self.db.set_session_backend("t1", "openrouter")
        self.db.set_session_model("t1", "sonnet")
        self.db.reset_session_resume("t1")
        row = self.db.get_session("t1")
        self.assertIsNone(row["session_id"])
        self.assertEqual(row["backend"], "openrouter")
        self.assertEqual(row["model"], "sonnet")

    def test_set_session_preserves_backend(self) -> None:
        self.db.set_session_backend("t1", "claude-cli")
        self.db.set_session("t1", "sess-abc", "hello")
        row = self.db.get_session("t1")
        self.assertEqual(row["session_id"], "sess-abc")
        self.assertEqual(row["backend"], "claude-cli")


if __name__ == "__main__":
    unittest.main()
