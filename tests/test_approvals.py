from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from zen_agent_bot.approvals import ApprovalBridge


class ApprovalBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolve_allow(self) -> None:
        bridge = ApprovalBridge()
        edits: list[str] = []

        async def edit(text: str, **kwargs: object) -> None:
            edits.append(text)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            bridge.bind_job(
                session_key="s1",
                workspace=workspace,
                edit_status=edit,
                display_name="Manager",
            )
            task = asyncio.create_task(
                bridge.request(
                    session_key="s1",
                    kind="shell",
                    summary="ls",
                    detail="ls -la",
                    timeout_sec=2,
                )
            )
            await asyncio.sleep(0.05)
            pending = bridge.list_pending()
            self.assertEqual(len(pending), 1)
            ok = bridge.resolve(pending[0]["id"], allow=True, reason="test")
            self.assertTrue(ok)
            self.assertTrue(await task)
            self.assertTrue(any("Approval needed" in e for e in edits))
            bridge.unbind_job("s1")

    async def test_timeout_denies(self) -> None:
        bridge = ApprovalBridge()

        async def edit(text: str, **kwargs: object) -> None:
            return None

        with tempfile.TemporaryDirectory() as tmp:
            bridge.bind_job(
                session_key="s1",
                workspace=Path(tmp),
                edit_status=edit,
                display_name="Manager",
            )
            allowed = await bridge.request(
                session_key="s1",
                kind="shell",
                summary="rm -rf /",
                detail="rm -rf /",
                timeout_sec=0.05,
            )
            self.assertFalse(allowed)
            bridge.unbind_job("s1")

    async def test_cwd_lookup(self) -> None:
        bridge = ApprovalBridge()

        async def edit(text: str, **kwargs: object) -> None:
            return None

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            bridge.bind_job(
                session_key="s1",
                workspace=workspace,
                edit_status=edit,
                display_name="Manager",
            )
            self.assertEqual(
                bridge.resolve_session_key(session_key=None, cwd=str(workspace)),
                "s1",
            )
            bridge.unbind_job("s1")


if __name__ == "__main__":
    unittest.main()
