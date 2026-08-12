from __future__ import annotations

from .profile import AgentProfile


class AgentRegistry:
    def __init__(self, profiles: dict[str, AgentProfile]) -> None:
        if not profiles:
            raise ValueError("At least one agent profile is required")
        self._profiles = profiles

    def get(self, agent_id: str) -> AgentProfile:
        profile = self._profiles.get(agent_id)
        if profile is None:
            raise KeyError(f"Unknown agent profile: {agent_id}")
        return profile

    def all(self) -> list[AgentProfile]:
        return list(self._profiles.values())

    def manager(self) -> AgentProfile | None:
        for profile in self._profiles.values():
            if profile.is_manager:
                return profile
        return None

    def discord_agents(self) -> list[AgentProfile]:
        return [p for p in self._profiles.values() if p.discord is not None]

    def telegram_agents(self) -> list[AgentProfile]:
        return [p for p in self._profiles.values() if p.telegram is not None]
