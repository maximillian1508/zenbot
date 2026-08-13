from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zen_agent_bot.store import ConfigStore, validate_secret_name


class SecretNameTests(unittest.TestCase):
    def test_valid(self) -> None:
        self.assertEqual(validate_secret_name("OPENROUTER_API_KEY"), "OPENROUTER_API_KEY")

    def test_rejects_lowercase(self) -> None:
        with self.assertRaises(ValueError):
            validate_secret_name("openrouter_api_key")

    def test_rejects_blank(self) -> None:
        with self.assertRaises(ValueError):
            validate_secret_name("")


class SecretStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = ConfigStore(Path(self.tmp.name) / "gateway.db")

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def test_admin_wins_over_env(self) -> None:
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "env-key"}, clear=False):
            self.assertEqual(self.db.resolve_secret("OPENROUTER_API_KEY"), "env-key")
            self.assertEqual(self.db.secret_source("OPENROUTER_API_KEY"), "env")
            self.db.set_secret("OPENROUTER_API_KEY", "admin-key")
            self.assertEqual(self.db.resolve_secret("OPENROUTER_API_KEY"), "admin-key")
            self.assertEqual(self.db.secret_source("OPENROUTER_API_KEY"), "admin")

    def test_clear_falls_back_to_env(self) -> None:
        with patch.dict(os.environ, {"DISCORD_TOKEN_MANAGER": "env-token"}, clear=False):
            self.db.set_secret("DISCORD_TOKEN_MANAGER", "admin-token")
            self.db.delete_secret("DISCORD_TOKEN_MANAGER")
            self.assertEqual(self.db.resolve_secret("DISCORD_TOKEN_MANAGER"), "env-token")
            self.assertEqual(self.db.secret_source("DISCORD_TOKEN_MANAGER"), "env")

    def test_missing(self) -> None:
        self.assertEqual(self.db.resolve_secret("NO_SUCH_SECRET"), "")
        self.assertIsNone(self.db.secret_source("NO_SUCH_SECRET"))

    def test_rejects_empty_value(self) -> None:
        with self.assertRaises(ValueError):
            self.db.set_secret("OPENROUTER_API_KEY", "   ")
