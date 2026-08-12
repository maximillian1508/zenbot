from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .agents.profile import AgentProfile, DiscordBinding, TelegramBinding
from .agents.registry import AgentRegistry


SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    workspace TEXT NOT NULL,
    default_backend TEXT NOT NULL DEFAULT 'cursor-cli',
    skills TEXT NOT NULL DEFAULT '[]',
    system_prompt_file TEXT,
    is_manager INTEGER NOT NULL DEFAULT 0,
    discord_enabled INTEGER NOT NULL DEFAULT 1,
    discord_token_env TEXT,
    discord_channel_id TEXT,
    discord_guild_id TEXT,
    telegram_enabled INTEGER NOT NULL DEFAULT 0,
    telegram_token_env TEXT,
    telegram_chat_id TEXT
);
CREATE TABLE IF NOT EXISTS allowlist (
    user_id INTEGER PRIMARY KEY,
    note TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    session_key TEXT PRIMARY KEY,
    session_id TEXT,
    title TEXT,
    updated_at TEXT
);
"""


def _env_token(token_env: str | None) -> str:
    if not token_env:
        return ""
    return os.environ.get(token_env, "").strip()


class ConfigStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        return str(row["value"])

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self._conn.commit()

    def allowlist(self) -> list[int]:
        rows = self._conn.execute("SELECT user_id FROM allowlist ORDER BY user_id").fetchall()
        return [int(r["user_id"]) for r in rows]

    def is_allowed(self, user_id: int) -> bool:
        row = self._conn.execute("SELECT 1 FROM allowlist WHERE user_id = ?", (user_id,)).fetchone()
        return row is not None

    def add_allowed(self, user_id: int, note: str | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO allowlist(user_id, note) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET note = excluded.note",
                (user_id, note),
            )
            self._conn.commit()

    def remove_allowed(self, user_id: int) -> None:
        with self._lock:
            count = self._conn.execute("SELECT COUNT(*) AS n FROM allowlist").fetchone()["n"]
            if count <= 1:
                raise ValueError("Cannot remove last user from allowlist")
            self._conn.execute("DELETE FROM allowlist WHERE user_id = ?", (user_id,))
            self._conn.commit()

    def list_agent_rows(self) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM agents ORDER BY is_manager DESC, id").fetchall()
        return [dict(r) for r in rows]

    def get_agent_row(self, agent_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        return dict(row) if row else None

    def upsert_agent(self, row: dict[str, Any]) -> None:
        skills = row.get("skills")
        if isinstance(skills, (list, tuple)):
            skills_json = json.dumps(list(skills))
        else:
            skills_json = str(skills or "[]")
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO agents (
                    id, display_name, workspace, default_backend, skills, system_prompt_file,
                    is_manager, discord_enabled, discord_token_env, discord_channel_id,
                    discord_guild_id, telegram_enabled, telegram_token_env, telegram_chat_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    display_name = excluded.display_name,
                    workspace = excluded.workspace,
                    default_backend = excluded.default_backend,
                    skills = excluded.skills,
                    system_prompt_file = excluded.system_prompt_file,
                    is_manager = excluded.is_manager,
                    discord_enabled = excluded.discord_enabled,
                    discord_token_env = excluded.discord_token_env,
                    discord_channel_id = excluded.discord_channel_id,
                    discord_guild_id = excluded.discord_guild_id,
                    telegram_enabled = excluded.telegram_enabled,
                    telegram_token_env = excluded.telegram_token_env,
                    telegram_chat_id = excluded.telegram_chat_id
                """,
                (
                    row["id"],
                    row["display_name"],
                    row["workspace"],
                    row.get("default_backend") or "cursor-cli",
                    skills_json,
                    row.get("system_prompt_file"),
                    1 if row.get("is_manager") else 0,
                    1 if row.get("discord_enabled") else 0,
                    row.get("discord_token_env"),
                    str(row["discord_channel_id"]) if row.get("discord_channel_id") not in (None, "") else None,
                    str(row["discord_guild_id"]) if row.get("discord_guild_id") not in (None, "") else None,
                    1 if row.get("telegram_enabled") else 0,
                    row.get("telegram_token_env"),
                    str(row["telegram_chat_id"]) if row.get("telegram_chat_id") not in (None, "") else None,
                ),
            )
            self._conn.commit()

    def delete_agent(self, agent_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
            self._conn.commit()

    def agent_count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) AS n FROM agents").fetchone()["n"])

    def load_registry(self) -> AgentRegistry:
        profiles: dict[str, AgentProfile] = {}
        for row in self.list_agent_rows():
            profiles[row["id"]] = self._row_to_profile(row)
        if not profiles:
            raise SystemExit("No agent profiles in SQLite — add one in the admin UI or seed from config.yaml")
        return AgentRegistry(profiles)

    def _row_to_profile(self, row: dict[str, Any]) -> AgentProfile:
        skills_raw = row.get("skills") or "[]"
        try:
            skills_list = json.loads(skills_raw)
        except json.JSONDecodeError:
            skills_list = [s.strip() for s in str(skills_raw).splitlines() if s.strip()]
        discord = None
        if row.get("discord_enabled") and row.get("discord_token_env") and row.get("discord_channel_id"):
            token = _env_token(row["discord_token_env"])
            if not token:
                raise SystemExit(f"Environment variable {row['discord_token_env']!r} is required but empty")
            guild = row.get("discord_guild_id")
            discord = DiscordBinding(
                token=token,
                agent_channel_id=int(row["discord_channel_id"]),
                guild_id=int(guild) if guild else None,
            )
        telegram = None
        if row.get("telegram_enabled") and row.get("telegram_token_env"):
            token = _env_token(row["telegram_token_env"])
            if not token:
                raise SystemExit(f"Environment variable {row['telegram_token_env']!r} is required but empty")
            chat = row.get("telegram_chat_id")
            telegram = TelegramBinding(
                token=token,
                agent_chat_id=int(chat) if chat else None,
            )
        prompt = row.get("system_prompt_file")
        return AgentProfile(
            id=row["id"],
            display_name=row["display_name"],
            workspace=Path(str(row["workspace"])).expanduser().resolve(),
            default_backend=str(row.get("default_backend") or "cursor-cli"),
            skills=tuple(str(s) for s in skills_list),
            system_prompt_file=Path(prompt) if prompt else None,
            is_manager=bool(row.get("is_manager")),
            discord=discord,
            telegram=telegram,
        )

    def list_sessions(self) -> dict[str, dict[str, str | None]]:
        rows = self._conn.execute(
            "SELECT session_key, session_id, title FROM sessions ORDER BY session_key"
        ).fetchall()
        return {
            str(r["session_key"]): {"session_id": r["session_id"], "title": r["title"]}
            for r in rows
        }

    def get_session(self, key: str) -> dict[str, str | None]:
        row = self._conn.execute(
            "SELECT session_id, title FROM sessions WHERE session_key = ?", (key,)
        ).fetchone()
        if not row:
            return {"session_id": None, "title": None}
        return {"session_id": row["session_id"], "title": row["title"]}

    def set_session(self, key: str, session_id: str | None, title: str | None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO sessions(session_key, session_id, title, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_key) DO UPDATE SET
                    session_id = excluded.session_id,
                    title = excluded.title,
                    updated_at = excluded.updated_at
                """,
                (key, session_id, title, now),
            )
            self._conn.commit()

    def clear_session(self, key: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM sessions WHERE session_key = ?", (key,))
            self._conn.commit()

    def migrate_yaml(self, path: Path) -> bool:
        if not path.is_file() or self.agent_count() > 0:
            return False
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        server = raw.get("server") or {}
        if "max_concurrent_jobs" in server:
            self.set_setting("max_concurrent_jobs", str(server["max_concurrent_jobs"]))
        if server.get("admin_listen"):
            self.set_setting("admin_listen", str(server["admin_listen"]))
        if "admin_enabled" in server:
            self.set_setting("admin_enabled", "true" if server["admin_enabled"] else "false")
        if raw.get("discord_guild_id"):
            self.set_setting("discord_guild_id", str(raw["discord_guild_id"]))
        for uid in raw.get("allowed_user_ids") or []:
            self.add_allowed(int(uid))
        for agent_id, cfg in (raw.get("agents") or {}).items():
            disc = cfg.get("discord") or {}
            tg = cfg.get("telegram") or {}
            self.upsert_agent(
                {
                    "id": agent_id,
                    "display_name": cfg.get("display_name", agent_id),
                    "workspace": cfg.get("workspace", "/home/maxi"),
                    "default_backend": cfg.get("default_backend", "cursor-cli"),
                    "skills": cfg.get("skills") or [],
                    "system_prompt_file": cfg.get("system_prompt_file"),
                    "is_manager": bool(cfg.get("is_manager")),
                    "discord_enabled": bool(disc.get("enabled", True)),
                    "discord_token_env": disc.get("token_env"),
                    "discord_channel_id": disc.get("agent_channel_id"),
                    "discord_guild_id": disc.get("guild_id"),
                    "telegram_enabled": bool(tg.get("enabled", False)),
                    "telegram_token_env": tg.get("token_env"),
                    "telegram_chat_id": tg.get("agent_chat_id"),
                }
            )
        backends = raw.get("backends") or {}
        cli = backends.get("cursor-cli") or {}
        if cli.get("command"):
            self.set_setting("backend.cursor-cli.command", str(cli["command"]))
        if "force" in cli:
            self.set_setting("backend.cursor-cli.force", "true" if cli["force"] else "false")
        if cli.get("model"):
            self.set_setting("backend.cursor-cli.model", str(cli["model"]))
        or_cfg = backends.get("openrouter") or {}
        if or_cfg.get("model"):
            self.set_setting("backend.openrouter.model", str(or_cfg["model"]))
        if or_cfg.get("api_key_env"):
            self.set_setting("backend.openrouter.api_key_env", str(or_cfg["api_key_env"]))
        if or_cfg.get("base_url"):
            self.set_setting("backend.openrouter.base_url", str(or_cfg["base_url"]))
        return True

    def migrate_sessions_json(self, path: Path) -> None:
        if not path.is_file():
            return
        existing = self._conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"]
        if existing:
            return
        raw = json.loads(path.read_text(encoding="utf-8"))
        for key, row in raw.items():
            self.set_session(str(key), row.get("session_id"), row.get("title"))
