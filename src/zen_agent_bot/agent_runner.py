from __future__ import annotations

from pathlib import Path

from .backends.base import AgentRunResult
from .backends.cursor_cli import CursorCliBackend, CursorCliConfig


async def run_agent(
    *,
    prompt: str,
    workspace: Path,
    session_id: str | None,
    agent_bin: str = "agent",
    model: str | None = None,
    force: bool = True,
    timeout_sec: int = 3600,
) -> AgentRunResult:
    backend = CursorCliBackend(
        CursorCliConfig(
            command=agent_bin,
            force=force,
            model=model,
            timeout_sec=timeout_sec,
        )
    )
    return await backend.run(
        prompt=prompt,
        workspace=workspace,
        session_id=session_id,
    )
