from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DiscordBinding:
    token: str
    agent_channel_id: int
    guild_id: int | None = None


@dataclass(frozen=True)
class TelegramBinding:
    token: str
    agent_chat_id: int | None = None


@dataclass(frozen=True)
class AgentProfile:
    id: str
    display_name: str
    workspace: Path
    default_backend: str
    skills: tuple[str, ...]
    system_prompt_file: Path | None
    is_manager: bool
    discord: DiscordBinding | None
    telegram: TelegramBinding | None

    def system_prompt(self, project_root: Path) -> str:
        if not self.system_prompt_file:
            return ""
        path = self.system_prompt_file
        if not path.is_absolute():
            path = (project_root / path).resolve()
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8").strip()
