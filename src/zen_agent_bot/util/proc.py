from __future__ import annotations

import asyncio
import os
import signal


async def terminate_process(proc: asyncio.subprocess.Process, *, grace_sec: float = 5.0) -> None:
    if proc.returncode is not None:
        return
    pid = proc.pid

    def _signal(sig: int) -> None:
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.send_signal(sig)
            except (ProcessLookupError, OSError):
                return

    _signal(signal.SIGTERM)
    try:
        await asyncio.wait_for(proc.wait(), timeout=grace_sec)
    except asyncio.TimeoutError:
        _signal(signal.SIGKILL)
        try:
            await proc.wait()
        except ProcessLookupError:
            return
