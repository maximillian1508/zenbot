from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable

ProgressCallback = Callable[[str], Awaitable[None]]


class ThrottledProgress:
    """Rate-limit progress callbacks (e.g. Discord message edits)."""

    def __init__(self, callback: ProgressCallback, *, min_interval: float = 1.5) -> None:
        self._callback = callback
        self._min_interval = min_interval
        self._last = 0.0
        self._latest = ""
        self._stopped = False
        self._lock = asyncio.Lock()

    @property
    def latest(self) -> str:
        """Last progress text pushed (may not have been flushed yet)."""
        return self._latest

    def stop(self) -> None:
        """Ignore further stream edits (cancel / final status owns the bubble)."""
        self._stopped = True

    async def push(self, text: str) -> None:
        async with self._lock:
            if self._stopped:
                return
            self._latest = text
            now = time.monotonic()
            if now - self._last >= self._min_interval:
                self._last = now
                await self._callback(text)

    async def flush(self) -> None:
        async with self._lock:
            if self._stopped or not self._latest:
                return
            await self._callback(self._latest)
            self._last = time.monotonic()
