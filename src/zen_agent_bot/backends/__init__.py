from __future__ import annotations

from typing import Any

from .base import AgentBackend, AgentRunResult
from .cursor_cli import CursorCliBackend, CursorCliConfig
from .openrouter import OpenRouterBackend, OpenRouterConfig


def build_backends(raw: dict[str, Any]) -> dict[str, AgentBackend]:
    backends: dict[str, AgentBackend] = {}
    for name, cfg in raw.items():
        kind = str(cfg.get("kind", "cursor-cli"))
        if kind == "cursor-cli":
            backends[name] = CursorCliBackend(
                CursorCliConfig(
                    command=str(cfg.get("command", "agent")),
                    force=bool(cfg.get("force", True)),
                    model=cfg.get("model"),
                    timeout_sec=int(cfg.get("timeout_sec", 3600)),
                )
            )
        elif kind == "openrouter":
            backends[name] = OpenRouterBackend(
                OpenRouterConfig(
                    api_key_env=str(cfg.get("api_key_env", "OPENROUTER_API_KEY")),
                    model=str(cfg.get("model", "anthropic/claude-sonnet-4")),
                    base_url=str(cfg.get("base_url", "https://openrouter.ai/api/v1")),
                    timeout_sec=int(cfg.get("timeout_sec", 300)),
                    site_url=cfg.get("site_url"),
                    site_name=cfg.get("site_name", "zen-agent-bot"),
                )
            )
        else:
            raise ValueError(f"Unknown backend kind {kind!r} for {name!r}")
    if "cursor-cli" not in backends:
        backends["cursor-cli"] = CursorCliBackend(CursorCliConfig())
    return backends


__all__ = [
    "AgentBackend",
    "AgentRunResult",
    "CursorCliBackend",
    "CursorCliConfig",
    "OpenRouterBackend",
    "OpenRouterConfig",
    "build_backends",
]
