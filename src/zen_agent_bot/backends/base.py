from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Protocol

ProgressCallback = Callable[[str], Awaitable[None]]


@dataclass
class AgentRunResult:
    text: str
    session_id: str | None
    exit_code: int
    error: str | None = None


class AgentBackend(Protocol):
    async def run(
        self,
        *,
        prompt: str,
        workspace: Path,
        session_id: str | None,
        on_progress: ProgressCallback | None = None,
    ) -> AgentRunResult: ...
