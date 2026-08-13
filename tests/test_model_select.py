from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from zen_agent_bot.backends.cursor_cli import CursorCliBackend, CursorCliConfig
from zen_agent_bot.model_select import (
    OPENROUTER_FALLBACK,
    apply_openrouter_online,
    format_cursor_catalog,
    openrouter_online_enabled,
    parse_agent_models_output,
    parse_model_arg,
    resolve_model,
)
from zen_agent_bot.store import ConfigStore


class ParseModelArgTests(unittest.TestCase):
    def test_show_on_empty(self) -> None:
        self.assertEqual(parse_model_arg(None), ("show", None))
        self.assertEqual(parse_model_arg(""), ("show", None))
        self.assertEqual(parse_model_arg("  "), ("show", None))

    def test_clear_tokens(self) -> None:
        for token in ("clear", "default", "none", "RESET"):
            self.assertEqual(parse_model_arg(token), ("clear", None))

    def test_set_id(self) -> None:
        self.assertEqual(parse_model_arg("composer-2.5"), ("set", "composer-2.5"))
        self.assertEqual(
            parse_model_arg("  anthropic/claude-sonnet-4 "),
            ("set", "anthropic/claude-sonnet-4"),
        )

    def test_rejects_too_long(self) -> None:
        with self.assertRaises(ValueError):
            parse_model_arg("x" * 200)

    def test_list_is_show(self) -> None:
        self.assertEqual(parse_model_arg("list"), ("show", None))
        self.assertEqual(parse_model_arg("ls"), ("show", None))


SAMPLE_AGENT_MODELS = """
Available models

auto - Auto (current, default)
composer-2.5 - Composer 2.5
composer-2.5-fast - Composer 2.5 Fast
gpt-5.2 - GPT-5.2

Tip: use --model <id> (or /model <id> in interactive mode) to switch.
"""


class ParseAgentModelsTests(unittest.TestCase):
    def test_parses_id_and_label(self) -> None:
        rows = parse_agent_models_output(SAMPLE_AGENT_MODELS)
        self.assertEqual(
            rows,
            [
                ("auto", "Auto (current, default)"),
                ("composer-2.5", "Composer 2.5"),
                ("composer-2.5-fast", "Composer 2.5 Fast"),
                ("gpt-5.2", "GPT-5.2"),
            ],
        )

    def test_catalog_marks_current(self) -> None:
        rows = parse_agent_models_output(SAMPLE_AGENT_MODELS)
        text = format_cursor_catalog(rows, current="composer-2.5")
        self.assertIn("`composer-2.5`", text)
        self.assertIn("←", text)
        self.assertIn("4 on this account", text)


class StoreSessionOverrideTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = ConfigStore(Path(self.tmp.name) / "gateway.db")

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def test_set_session_preserves_model(self) -> None:
        self.db.set_session_model("t1", "composer-2.5")
        self.db.set_session("t1", "sess-abc", "hello")
        row = self.db.get_session("t1")
        self.assertEqual(row["session_id"], "sess-abc")
        self.assertEqual(row["model"], "composer-2.5")

    def test_reset_resume_keeps_model(self) -> None:
        self.db.set_session("t1", "sess-abc", "hello")
        self.db.set_session_model("t1", "sonnet")
        self.db.reset_session_resume("t1")
        row = self.db.get_session("t1")
        self.assertIsNone(row["session_id"])
        self.assertIsNone(row["title"])
        self.assertEqual(row["model"], "sonnet")

    def test_clear_model(self) -> None:
        self.db.set_session_model("t1", "composer-2.5")
        self.db.set_session_model("t1", None)
        self.assertIsNone(self.db.get_session("t1")["model"])

    def test_existing_db_migrates_columns(self) -> None:
        path = Path(self.tmp.name) / "old.db"
        import sqlite3

        conn = sqlite3.connect(str(path))
        conn.execute(
            "CREATE TABLE sessions (session_key TEXT PRIMARY KEY, "
            "session_id TEXT, title TEXT, updated_at TEXT)"
        )
        conn.execute(
            "INSERT INTO sessions VALUES ('k','sid','hi','now')"
        )
        conn.commit()
        conn.close()
        db = ConfigStore(path)
        row = db.get_session("k")
        self.assertEqual(row["session_id"], "sid")
        self.assertIsNone(row["model"])
        db.set_session_model("k", "composer-2.5")
        self.assertEqual(db.get_session("k")["model"], "composer-2.5")
        db.close()


class ResolveModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = ConfigStore(Path(self.tmp.name) / "gateway.db")
        self._env_backup = {
            key: os.environ.pop(key, None)
            for key in (
                "AGENT_MODEL",
                "CLAUDE_MODEL",
                "OPENROUTER_MODEL",
                "OPENROUTER_ONLINE",
            )
        }

    def tearDown(self) -> None:
        for key, val in self._env_backup.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        self.db.close()
        self.tmp.cleanup()

    def test_order_thread_beats_admin(self) -> None:
        self.db.set_setting("backend.cursor-cli.model", "admin-model")
        self.db.set_session_model("t1", "thread-model")
        resolved = resolve_model(self.db, "t1", "cursor-cli")
        self.assertEqual(resolved.model, "thread-model")
        self.assertEqual(resolved.source, "thread")

    def test_env_beats_admin(self) -> None:
        self.db.set_setting("backend.cursor-cli.model", "admin-model")
        os.environ["AGENT_MODEL"] = "env-model"
        resolved = resolve_model(self.db, "missing", "cursor-cli")
        self.assertEqual(resolved.model, "env-model")
        self.assertEqual(resolved.source, "env")

    def test_admin_when_no_thread(self) -> None:
        self.db.set_setting("backend.cursor-cli.model", "admin-model")
        resolved = resolve_model(self.db, "missing", "cursor-cli")
        self.assertEqual(resolved.model, "admin-model")
        self.assertEqual(resolved.source, "admin")

    def test_openrouter_fallback(self) -> None:
        resolved = resolve_model(self.db, "missing", "openrouter")
        self.assertEqual(resolved.model, OPENROUTER_FALLBACK)
        self.assertEqual(resolved.source, "default")

    def test_openrouter_online_appends(self) -> None:
        self.db.set_setting("backend.openrouter.model", "~deepseek/deepseek-v4-flash-latest")
        self.db.set_setting("backend.openrouter.online", "true")
        resolved = resolve_model(self.db, "missing", "openrouter")
        self.assertEqual(
            resolved.model, "~deepseek/deepseek-v4-flash-latest:online"
        )
        self.assertEqual(resolved.source, "admin")

    def test_openrouter_online_idempotent_on_thread(self) -> None:
        self.db.set_setting("backend.openrouter.online", "true")
        self.db.set_session_model("t1", "openai/gpt-4o:online")
        resolved = resolve_model(self.db, "t1", "openrouter")
        self.assertEqual(resolved.model, "openai/gpt-4o:online")
        self.assertEqual(resolved.source, "thread")

    def test_openrouter_online_off_leaves_plain(self) -> None:
        self.db.set_setting("backend.openrouter.model", "openai/gpt-4o")
        resolved = resolve_model(self.db, "missing", "openrouter")
        self.assertEqual(resolved.model, "openai/gpt-4o")

    def test_openrouter_online_env_wins(self) -> None:
        self.db.set_setting("backend.openrouter.model", "openai/gpt-4o")
        self.db.set_setting("backend.openrouter.online", "false")
        os.environ["OPENROUTER_ONLINE"] = "true"
        self.assertTrue(openrouter_online_enabled(self.db))
        resolved = resolve_model(self.db, "missing", "openrouter")
        self.assertEqual(resolved.model, "openai/gpt-4o:online")

    def test_cursor_cli_default_is_none(self) -> None:
        resolved = resolve_model(self.db, "missing", "cursor-cli")
        self.assertIsNone(resolved.model)
        self.assertEqual(resolved.source, "default")


class ApplyOnlineTests(unittest.TestCase):
    def test_append_and_skip(self) -> None:
        self.assertEqual(
            apply_openrouter_online("openai/gpt-4o", online=True),
            "openai/gpt-4o:online",
        )
        self.assertEqual(
            apply_openrouter_online("openai/gpt-4o:online", online=True),
            "openai/gpt-4o:online",
        )
        self.assertEqual(
            apply_openrouter_online("openai/gpt-4o:nitro:online", online=True),
            "openai/gpt-4o:nitro:online",
        )
        self.assertEqual(
            apply_openrouter_online("openai/gpt-4o", online=False),
            "openai/gpt-4o",
        )
        self.assertIsNone(apply_openrouter_online(None, online=True))


class CursorCmdTests(unittest.TestCase):
    def test_omits_model_when_none(self) -> None:
        backend = CursorCliBackend(CursorCliConfig())
        cmd = backend._base_cmd("agent", Path("/tmp"), None, None)
        self.assertNotIn("--model", cmd)

    def test_includes_model_override(self) -> None:
        backend = CursorCliBackend(CursorCliConfig(model="baked"))
        cmd = backend._base_cmd("agent", Path("/tmp"), None, "composer-2.5")
        idx = cmd.index("--model")
        self.assertEqual(cmd[idx + 1], "composer-2.5")


if __name__ == "__main__":
    unittest.main()
