from __future__ import annotations

import unittest

from zen_agent_bot.util.throttle import ThrottledProgress


class ThrottledProgressStopTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_blocks_push_and_flush(self) -> None:
        seen: list[str] = []

        async def cb(text: str) -> None:
            seen.append(text)

        progress = ThrottledProgress(cb, min_interval=0)
        await progress.push("running")
        self.assertEqual(seen, ["running"])
        progress.stop()
        await progress.push("still running")
        await progress.flush()
        self.assertEqual(seen, ["running"])
        self.assertEqual(progress.latest, "running")
