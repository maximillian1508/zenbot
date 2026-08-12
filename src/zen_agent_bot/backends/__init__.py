from __future__ import annotations

from typing import Any

from .base import AgentBackend, AgentRunResult
from .cursor_cli import CursorCliBackend, CursorCliConfig


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
    "build_backends",
]
