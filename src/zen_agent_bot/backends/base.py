from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Protocol

ProgressCallback = Callable[[str], Awaitable[None]]
RegisterProc = Callable[[asyncio.subprocess.Process], None]

# Cursor/Claude stream-json can emit huge single-line events (tool payloads).
# asyncio StreamReader defaults to 64 KiB and raises LimitOverrunError.
STREAM_STDOUT_LIMIT = 8 * 1024 * 1024


def is_stream_line_too_large(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return (
        isinstance(exc, asyncio.LimitOverrunError)
        or "chunk is longer than limit" in msg
        or "separator is found" in msg
    )


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
        cancel_event: asyncio.Event | None = None,
        register_proc: RegisterProc | None = None,
        model: str | None = None,
    ) -> AgentRunResult: ...
