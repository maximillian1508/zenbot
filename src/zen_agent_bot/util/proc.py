from __future__ import annotations

import asyncio


async def terminate_process(proc: asyncio.subprocess.Process, *, grace_sec: float = 5.0) -> None:
    if proc.returncode is not None:
        return
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=grace_sec)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
