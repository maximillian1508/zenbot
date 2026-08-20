from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .agents.profile import AgentProfile, DiscordBinding, TelegramBinding
from .agents.registry import AgentRegistry
from .chat_history import CHAT_TURN_MAX, CHAT_TURN_MAX_CHARS, clip_turn, window_turns

log = logging.getLogger(__name__)

SECRET_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


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
    model TEXT,
    backend TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS secrets (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS chat_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_key TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_turns_session ON chat_turns(session_key, id);
CREATE TABLE IF NOT EXISTS schedules (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    cron_expr TEXT NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'Asia/Singapore',
    prompt TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    next_run_at TEXT,
    last_run_at TEXT,
    last_status TEXT,
    last_error TEXT,
    last_thread_id TEXT,
    last_session_key TEXT,
    last_thread_url TEXT,
    run_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS route_bindings (
    id TEXT PRIMARY KEY,
    transport TEXT NOT NULL DEFAULT 'discord',
    channel_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    workspace TEXT,
    backend TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(transport, channel_id)
);
"""


def validate_secret_name(name: str) -> str:
    cleaned = name.strip()
    if not SECRET_NAME_RE.match(cleaned):
        raise ValueError(
            "Secret name must be an env-style identifier (A-Z, 0-9, _; start with a letter)"
        )
    return cleaned


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
            self._migrate_session_columns()
            self._conn.commit()

    def _migrate_session_columns(self) -> None:
        cols = {
            str(row[1])
            for row in self._conn.execute("PRAGMA table_info(sessions)").fetchall()
        }
        if "model" not in cols:
            self._conn.execute("ALTER TABLE sessions ADD COLUMN model TEXT")
        if "backend" not in cols:
            self._conn.execute("ALTER TABLE sessions ADD COLUMN backend TEXT")

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

    def resolve_secret(self, name: str | None) -> str:
        """Admin SQLite value wins; otherwise process env. Never log the value."""
        if not name or not str(name).strip():
            return ""
        key = str(name).strip()
        row = self._conn.execute(
            "SELECT value FROM secrets WHERE name = ?", (key,)
        ).fetchone()
        if row is not None:
            return str(row["value"]).strip()
        return os.environ.get(key, "").strip()

    def secret_source(self, name: str) -> str | None:
        """Where the live value comes from: 'admin', 'env', or None."""
        key = name.strip()
        if not key:
            return None
        row = self._conn.execute(
            "SELECT value FROM secrets WHERE name = ?", (key,)
        ).fetchone()
        if row is not None and str(row["value"]).strip():
            return "admin"
        if os.environ.get(key, "").strip():
            return "env"
        return None

    def list_secret_names(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT name FROM secrets ORDER BY name"
        ).fetchall()
        return [str(r["name"]) for r in rows]

    def set_secret(self, name: str, value: str) -> None:
        key = validate_secret_name(name)
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Secret value cannot be empty")
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO secrets(name, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, cleaned, now),
            )
            self._conn.commit()

    def delete_secret(self, name: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM secrets WHERE name = ?", (name.strip(),))
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
            token = self.resolve_secret(row["discord_token_env"])
            if not token:
                log.warning(
                    "Discord token %s missing for agent %s — set it in admin Secrets or .env",
                    row["discord_token_env"],
                    row["id"],
                )
            else:
                guild = row.get("discord_guild_id")
                discord = DiscordBinding(
                    token=token,
                    agent_channel_id=int(row["discord_channel_id"]),
                    guild_id=int(guild) if guild else None,
                )
        telegram = None
        if row.get("telegram_enabled") and row.get("telegram_token_env"):
            token = self.resolve_secret(row["telegram_token_env"])
            if not token:
                log.warning(
                    "Telegram token %s missing for agent %s — set it in admin Secrets or .env",
                    row["telegram_token_env"],
                    row["id"],
                )
            else:
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
            "SELECT session_key, session_id, title, model, backend, updated_at "
            "FROM sessions ORDER BY updated_at DESC, session_key"
        ).fetchall()
        return {
            str(r["session_key"]): {
                "session_id": r["session_id"],
                "title": r["title"],
                "model": r["model"],
                "backend": r["backend"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        }

    def get_session(self, key: str) -> dict[str, str | None]:
        row = self._conn.execute(
            "SELECT session_id, title, model, backend FROM sessions WHERE session_key = ?",
            (key,),
        ).fetchone()
        if not row:
            return {
                "session_id": None,
                "title": None,
                "model": None,
                "backend": None,
            }
        return {
            "session_id": row["session_id"],
            "title": row["title"],
            "model": row["model"],
            "backend": row["backend"],
        }

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

    def set_session_model(self, key: str, model: str | None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO sessions(session_key, model, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(session_key) DO UPDATE SET
                    model = excluded.model,
                    updated_at = excluded.updated_at
                """,
                (key, model, now),
            )
            self._conn.commit()

    def set_session_backend(self, key: str, backend: str | None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO sessions(session_key, backend, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(session_key) DO UPDATE SET
                    backend = excluded.backend,
                    updated_at = excluded.updated_at
                """,
                (key, backend, now),
            )
            self._conn.commit()

    def reset_session_resume(self, key: str) -> None:
        """Clear --resume mapping; keep /model and /backend overrides."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                """
                UPDATE sessions
                SET session_id = NULL, title = NULL, updated_at = ?
                WHERE session_key = ?
                """,
                (now, key),
            )
            self._conn.execute("DELETE FROM chat_turns WHERE session_key = ?", (key,))
            self._conn.commit()

    def clear_session(self, key: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM chat_turns WHERE session_key = ?", (key,))
            self._conn.execute("DELETE FROM sessions WHERE session_key = ?", (key,))
            self._conn.commit()

    def append_chat_turn(self, session_key: str, role: str, content: str) -> None:
        body = clip_turn(content)
        if role not in ("user", "assistant") or not body:
            return
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO chat_turns(session_key, role, content, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (session_key, role, body, now),
            )
            self._prune_chat_turns_locked(session_key)
            self._conn.commit()

    def list_chat_turns(self, session_key: str) -> list[dict[str, str]]:
        rows = self._conn.execute(
            """
            SELECT role, content FROM chat_turns
            WHERE session_key = ?
            ORDER BY id ASC
            """,
            (session_key,),
        ).fetchall()
        return window_turns(
            [{"role": str(r["role"]), "content": str(r["content"])} for r in rows]
        )

    def clear_chat_turns(self, session_key: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM chat_turns WHERE session_key = ?", (session_key,))
            self._conn.commit()

    def _prune_chat_turns_locked(self, session_key: str) -> None:
        rows = self._conn.execute(
            """
            SELECT id, content FROM chat_turns
            WHERE session_key = ?
            ORDER BY id ASC
            """,
            (session_key,),
        ).fetchall()
        drop: list[int] = []
        keep = list(rows)
        extra = len(keep) - CHAT_TURN_MAX
        if extra > 0:
            drop.extend(int(r["id"]) for r in keep[:extra])
            keep = keep[extra:]
        total = sum(len(str(r["content"] or "")) for r in keep)
        while len(keep) > 1 and total > CHAT_TURN_MAX_CHARS:
            dropped = keep.pop(0)
            drop.append(int(dropped["id"]))
            total -= len(str(dropped["content"] or ""))
        if drop:
            placeholders = ",".join("?" * len(drop))
            self._conn.execute(
                f"DELETE FROM chat_turns WHERE id IN ({placeholders})",
                drop,
            )

    def _schedule_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["enabled"] = bool(data.get("enabled"))
        data["run_count"] = int(data.get("run_count") or 0)
        return data

    def list_schedules(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM schedules ORDER BY enabled DESC, name COLLATE NOCASE, id"
        ).fetchall()
        return [self._schedule_dict(r) for r in rows]

    def get_schedule(self, schedule_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM schedules WHERE id = ?", (schedule_id,)
        ).fetchone()
        return self._schedule_dict(row) if row else None

    def upsert_schedule(self, row: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        created = str(row.get("created_at") or now)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO schedules(
                    id, name, agent_id, cron_expr, timezone, prompt, enabled,
                    created_at, updated_at, next_run_at, last_run_at, last_status,
                    last_error, last_thread_id, last_session_key, last_thread_url,
                    run_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    agent_id = excluded.agent_id,
                    cron_expr = excluded.cron_expr,
                    timezone = excluded.timezone,
                    prompt = excluded.prompt,
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at,
                    next_run_at = excluded.next_run_at
                """,
                (
                    row["id"],
                    row["name"],
                    row["agent_id"],
                    row["cron_expr"],
                    row.get("timezone") or "Asia/Singapore",
                    row["prompt"],
                    1 if row.get("enabled", True) else 0,
                    created,
                    now,
                    row.get("next_run_at"),
                    row.get("last_run_at"),
                    row.get("last_status"),
                    row.get("last_error"),
                    row.get("last_thread_id"),
                    row.get("last_session_key"),
                    row.get("last_thread_url"),
                    int(row.get("run_count") or 0),
                ),
            )
            self._conn.commit()

    def delete_schedule(self, schedule_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
            self._conn.commit()

    def set_schedule_enabled(self, schedule_id: str, enabled: bool) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                "UPDATE schedules SET enabled = ?, updated_at = ? WHERE id = ?",
                (1 if enabled else 0, now, schedule_id),
            )
            self._conn.commit()

    def due_schedules(self, now_iso: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT * FROM schedules
            WHERE enabled = 1
              AND (last_status IS NULL OR last_status != 'running')
              AND next_run_at IS NOT NULL
              AND next_run_at <= ?
            ORDER BY next_run_at
            """,
            (now_iso,),
        ).fetchall()
        return [self._schedule_dict(r) for r in rows]

    def mark_schedule_running(
        self,
        schedule_id: str,
        *,
        next_run_at: str,
        thread_id: str | None,
        session_key: str | None,
        thread_url: str | None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                """
                UPDATE schedules SET
                    last_status = 'running',
                    last_run_at = ?,
                    last_error = NULL,
                    last_thread_id = ?,
                    last_session_key = ?,
                    last_thread_url = ?,
                    next_run_at = ?,
                    run_count = run_count + 1,
                    updated_at = ?
                WHERE id = ?
                """,
                (now, thread_id, session_key, thread_url, next_run_at, now, schedule_id),
            )
            self._conn.commit()

    def mark_schedule_done(
        self,
        schedule_id: str,
        *,
        ok: bool,
        error: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                """
                UPDATE schedules SET
                    last_status = ?,
                    last_error = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                ("ok" if ok else "error", (error or "")[:1500] or None, now, schedule_id),
            )
            self._conn.commit()

    def reset_stuck_schedules(self) -> int:
        """Clear running flags left by a crash/restart."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            cur = self._conn.execute(
                """
                UPDATE schedules SET last_status = 'interrupted', updated_at = ?
                WHERE last_status = 'running'
                """,
                (now,),
            )
            self._conn.commit()
            return int(cur.rowcount)

    def clear_schedule_thread(self, schedule_id: str) -> None:
        """Forget the dedicated Discord thread so the next run opens a new one."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                """
                UPDATE schedules SET
                    last_thread_id = NULL,
                    last_session_key = NULL,
                    last_thread_url = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (now, schedule_id),
            )
            self._conn.commit()

    def set_schedule_next_run(self, schedule_id: str, next_run_at: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                "UPDATE schedules SET next_run_at = ?, updated_at = ? WHERE id = ?",
                (next_run_at, now, schedule_id),
            )
            self._conn.commit()

    def _binding_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["enabled"] = bool(data.get("enabled"))
        return data

    def list_route_bindings(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM route_bindings ORDER BY enabled DESC, transport, channel_id"
        ).fetchall()
        return [self._binding_dict(r) for r in rows]

    def get_route_binding(self, binding_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM route_bindings WHERE id = ?", (binding_id,)
        ).fetchone()
        return self._binding_dict(row) if row else None

    def binding_for_channel(
        self, transport: str, channel_id: str | int
    ) -> dict[str, Any] | None:
        row = self._conn.execute(
            """
            SELECT * FROM route_bindings
            WHERE transport = ? AND channel_id = ? AND enabled = 1
            """,
            (transport, str(channel_id)),
        ).fetchone()
        return self._binding_dict(row) if row else None

    def upsert_route_binding(self, row: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        created = str(row.get("created_at") or now)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO route_bindings(
                    id, transport, channel_id, agent_id, workspace, backend,
                    enabled, note, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    transport = excluded.transport,
                    channel_id = excluded.channel_id,
                    agent_id = excluded.agent_id,
                    workspace = excluded.workspace,
                    backend = excluded.backend,
                    enabled = excluded.enabled,
                    note = excluded.note,
                    updated_at = excluded.updated_at
                """,
                (
                    row["id"],
                    row.get("transport") or "discord",
                    str(row["channel_id"]),
                    row["agent_id"],
                    row.get("workspace") or None,
                    row.get("backend") or None,
                    1 if row.get("enabled", True) else 0,
                    row.get("note") or None,
                    created,
                    now,
                ),
            )
            self._conn.commit()

    def delete_route_binding(self, binding_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM route_bindings WHERE id = ?", (binding_id,)
            )
            self._conn.commit()

    def set_route_binding_enabled(self, binding_id: str, enabled: bool) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                "UPDATE route_bindings SET enabled = ?, updated_at = ? WHERE id = ?",
                (1 if enabled else 0, now, binding_id),
            )
            self._conn.commit()

    def home_channel_ids(self) -> set[str]:
        rows = self._conn.execute(
            """
            SELECT discord_channel_id FROM agents
            WHERE discord_enabled = 1 AND discord_channel_id IS NOT NULL
            """
        ).fetchall()
        return {str(r["discord_channel_id"]) for r in rows if r["discord_channel_id"]}

    def prune_empty_sessions(self) -> int:
        """Delete rows with no resume id (overrides-only leftovers)."""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM sessions WHERE session_id IS NULL OR TRIM(session_id) = ''"
            )
            self._conn.commit()
            return int(cur.rowcount or 0)

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
        if "online" in or_cfg:
            self.set_setting(
                "backend.openrouter.online",
                "true" if or_cfg["online"] else "false",
            )
        claude = backends.get("claude-cli") or {}
        if claude.get("command"):
            self.set_setting("backend.claude-cli.command", str(claude["command"]))
        if "force" in claude:
            self.set_setting(
                "backend.claude-cli.force", "true" if claude["force"] else "false"
            )
        if claude.get("model"):
            self.set_setting("backend.claude-cli.model", str(claude["model"]))
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
