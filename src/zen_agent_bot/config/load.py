from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..agents.registry import AgentRegistry
from ..backends import AgentBackend, build_backends
from ..store import ConfigStore


def _truthy(raw: str | None, default: bool = True) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


def resolve_data_dir() -> Path:
    env = os.environ.get("ZEN_AGENT_DATA_DIR", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    cwd = Path.cwd() / "data"
    if cwd.exists():
        return cwd.resolve()
    return (Path(__file__).resolve().parents[3] / "data").resolve()


def resolve_yaml_seed() -> Path | None:
    env_path = os.environ.get("ZEN_AGENT_CONFIG", "").strip()
    candidates: list[Path] = []
    if env_path:
        candidates.append(Path(env_path).expanduser())
    data_dir = resolve_data_dir()
    candidates.extend(
        [
            data_dir / "config.yaml",
            Path.cwd() / "data" / "config.yaml",
            Path(__file__).resolve().parents[3] / "data" / "config.yaml",
        ]
    )
    for path in candidates:
        if path.is_file():
            return path
    return None


@dataclass
class GatewayConfig:
    project_root: Path
    data_dir: Path
    db: ConfigStore
    max_concurrent_jobs: int
    agents: AgentRegistry
    backends: dict[str, AgentBackend]
    discord_guild_id: int | None
    admin_listen: str
    admin_enabled: bool
    streaming_enabled: bool

    @property
    def allowed_user_ids(self) -> frozenset[int]:
        return frozenset(self.db.allowlist())


def load_config() -> GatewayConfig:
    data_dir = resolve_data_dir()
    db_path = Path(os.environ.get("ZEN_AGENT_DB", str(data_dir / "gateway.db"))).expanduser()
    db = ConfigStore(db_path)

    seed = resolve_yaml_seed()
    if seed is not None:
        db.migrate_yaml(seed)
    db.migrate_sessions_json(data_dir / "sessions.json")

    if db.agent_count() == 0:
        raise SystemExit(
            "No agents in SQLite. Seed data/config.yaml once, or add agents in the admin UI."
        )
    if not db.allowlist():
        raise SystemExit("Allowlist is empty — add at least one user ID (Discord or Telegram)")

    project_root = Path(os.environ.get("AGENT_WORKSPACE", str(Path.home()))).expanduser().resolve()
    if (Path("/app") / "pyproject.toml").exists():
        project_root = Path("/app")
    elif (Path.cwd() / "pyproject.toml").exists():
        project_root = Path.cwd().resolve()

    command = os.environ.get("AGENT_BIN") or db.get_setting("backend.cursor-cli.command", "agent") or "agent"
    force = _truthy(os.environ.get("AGENT_FORCE") or db.get_setting("backend.cursor-cli.force"), True)
    model = os.environ.get("AGENT_MODEL") or db.get_setting("backend.cursor-cli.model") or None

    or_model = (
        os.environ.get("OPENROUTER_MODEL")
        or db.get_setting("backend.openrouter.model")
        or "anthropic/claude-sonnet-4"
    )
    or_key_env = (
        os.environ.get("OPENROUTER_API_KEY_ENV")
        or db.get_setting("backend.openrouter.api_key_env")
        or "OPENROUTER_API_KEY"
    )
    or_base = (
        db.get_setting("backend.openrouter.base_url")
        or "https://openrouter.ai/api/v1"
    )
    claude_command = (
        os.environ.get("CLAUDE_BIN")
        or db.get_setting("backend.claude-cli.command")
        or "claude"
    )
    claude_force = _truthy(
        os.environ.get("CLAUDE_FORCE") or db.get_setting("backend.claude-cli.force"),
        True,
    )
    claude_model = (
        os.environ.get("CLAUDE_MODEL") or db.get_setting("backend.claude-cli.model") or None
    )
    backends = build_backends(
        {
            "cursor-cli": {
                "kind": "cursor-cli",
                "command": command,
                "force": force,
                "model": model,
            },
            "claude-cli": {
                "kind": "claude-cli",
                "command": claude_command,
                "force": claude_force,
                "model": claude_model,
            },
            "openrouter": {
                "kind": "openrouter",
                "api_key_env": or_key_env,
                "model": or_model,
                "base_url": or_base,
            },
            "cursor-sdk": {
                "kind": "cursor-sdk",
                "force": force,
                "model": db.get_setting("backend.cursor-sdk.model") or model,
                "api_key_env": "CURSOR_API_KEY",
            },
        },
        store=db,
    )

    max_jobs = int(
        os.environ.get("MAX_CONCURRENT_JOBS")
        or db.get_setting("max_concurrent_jobs", "2")
        or "2"
    )
    guild_raw = os.environ.get("DISCORD_GUILD_ID") or db.get_setting("discord_guild_id")
    admin_listen = os.environ.get("ADMIN_LISTEN") or db.get_setting("admin_listen") or "0.0.0.0:8787"
    admin_enabled = _truthy(os.environ.get("ADMIN_ENABLED") or db.get_setting("admin_enabled"), True)
    streaming_enabled = _truthy(
        os.environ.get("STREAMING") or db.get_setting("streaming_enabled"),
        True,
    )

    return GatewayConfig(
        project_root=project_root,
        data_dir=data_dir,
        db=db,
        max_concurrent_jobs=max_jobs,
        agents=db.load_registry(),
        backends=backends,
        discord_guild_id=int(guild_raw) if guild_raw else None,
        admin_listen=admin_listen,
        admin_enabled=admin_enabled,
        streaming_enabled=streaming_enabled,
    )
