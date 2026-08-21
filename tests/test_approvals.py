from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from zen_agent_bot.approvals import MAX_APPROVALS_PER_JOB, ApprovalBridge


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

    async def test_approval_limit(self) -> None:
        bridge = ApprovalBridge()
        edits: list[tuple[str, dict[str, object]]] = []

        async def edit(text: str, **kwargs: object) -> None:
            edits.append((text, kwargs))

        with tempfile.TemporaryDirectory() as tmp:
            bridge.bind_job(
                session_key="s1",
                workspace=Path(tmp),
                edit_status=edit,
                display_name="Manager",
                running_view="cancel-view",
            )
            for i in range(MAX_APPROVALS_PER_JOB):
                task = asyncio.create_task(
                    bridge.request(
                        session_key="s1",
                        kind="shell",
                        summary=f"cmd-{i}",
                        detail=f"cmd-{i}",
                        timeout_sec=0.05,
                    )
                )
                await asyncio.sleep(0.02)
                pending = bridge.list_pending()
                if pending:
                    bridge.resolve(pending[0]["id"], allow=False, reason="test")
                await task

            allowed = await bridge.request(
                session_key="s1",
                kind="shell",
                summary="over-limit",
                detail="over-limit",
                timeout_sec=0.05,
            )
            self.assertFalse(allowed)
            self.assertEqual(len(bridge.list_pending()), 0)
            limit_edits = [e for e in edits if "Approval limit" in e[0]]
            self.assertTrue(limit_edits)
            self.assertEqual(limit_edits[-1][1].get("view"), "cancel-view")
            bridge.unbind_job("s1")

    async def test_restore_running_view_after_resolve(self) -> None:
        bridge = ApprovalBridge()
        edits: list[tuple[str, dict[str, object]]] = []

        async def edit(text: str, **kwargs: object) -> None:
            edits.append((text, kwargs))

        with tempfile.TemporaryDirectory() as tmp:
            bridge.bind_job(
                session_key="s1",
                workspace=Path(tmp),
                edit_status=edit,
                display_name="Manager",
                running_view="cancel-view",
            )
            bridge.update_progress("s1", "⏳ streaming…")
            task = asyncio.create_task(
                bridge.request(
                    session_key="s1",
                    kind="shell",
                    summary="ls",
                    detail="ls",
                    timeout_sec=2,
                )
            )
            await asyncio.sleep(0.05)
            pending = bridge.list_pending()
            bridge.resolve(pending[0]["id"], allow=True, reason="test")
            await task
            restore = [e for e in edits if e[1].get("view") == "cancel-view"]
            self.assertTrue(restore)
            self.assertIn("streaming", restore[-1][0])
            bridge.unbind_job("s1")

    async def test_superseded_request_keeps_new_prompt(self) -> None:
        """Request B replacing A must not have A's cleanup stomp B's prompt."""
        bridge = ApprovalBridge()
        edits: list[tuple[str, dict[str, object]]] = []

        async def edit(text: str, **kwargs: object) -> None:
            edits.append((text, kwargs))

        with tempfile.TemporaryDirectory() as tmp:
            bridge.bind_job(
                session_key="s1",
                workspace=Path(tmp),
                edit_status=edit,
                display_name="Manager",
                running_view="cancel-view",
            )
            task_a = asyncio.create_task(
                bridge.request(
                    session_key="s1",
                    kind="shell",
                    summary="ls tmp",
                    detail="ls tmp",
                    timeout_sec=2,
                )
            )
            await asyncio.sleep(0.05)
            task_b = asyncio.create_task(
                bridge.request(
                    session_key="s1",
                    kind="shell",
                    summary="ls -la /tmp",
                    detail="ls -la /tmp",
                    timeout_sec=2,
                )
            )
            await asyncio.sleep(0.05)
            # A was auto-denied by B's registration and finished.
            self.assertFalse(await task_a)
            # B is still the live prompt: awaiting stays set, no cancel-view
            # restore may have run, and B's prompt must be the last edit.
            self.assertTrue(bridge.is_awaiting("s1"))
            restores = [e for e in edits if e[1].get("view") == "cancel-view"]
            self.assertFalse(restores)
            self.assertIn("ls -la /tmp", edits[-1][0])
            pending = bridge.list_pending()
            self.assertEqual(len(pending), 1)
            bridge.resolve(pending[0]["id"], allow=True, reason="test")
            self.assertTrue(await task_b)
            # Now that B is done, the running view is restored once.
            restores = [e for e in edits if e[1].get("view") == "cancel-view"]
            self.assertEqual(len(restores), 1)
            bridge.unbind_job("s1")

    async def test_cancel_during_approval_skips_restore(self) -> None:
        bridge = ApprovalBridge()
        edits: list[tuple[str, dict[str, object]]] = []
        cancel = asyncio.Event()

        async def edit(text: str, **kwargs: object) -> None:
            edits.append((text, kwargs))

        with tempfile.TemporaryDirectory() as tmp:
            bridge.bind_job(
                session_key="s1",
                workspace=Path(tmp),
                edit_status=edit,
                display_name="Manager",
                running_view="cancel-view",
                cancel_event=cancel,
            )
            task = asyncio.create_task(
                bridge.request(
                    session_key="s1",
                    kind="shell",
                    summary="ls /tmp",
                    detail="ls /tmp",
                    timeout_sec=5,
                )
            )
            await asyncio.sleep(0.05)
            self.assertTrue(bridge.has_pending("s1"))
            cancel.set()
            allowed = await task
            self.assertFalse(allowed)
            restore = [e for e in edits if e[1].get("view") == "cancel-view"]
            self.assertFalse(restore)
            bridge.unbind_job("s1")

    async def test_cancel_event_skips_restore_after_manual_resolve(self) -> None:
        bridge = ApprovalBridge()
        edits: list[tuple[str, dict[str, object]]] = []
        cancel = asyncio.Event()

        async def edit(text: str, **kwargs: object) -> None:
            edits.append((text, kwargs))

        with tempfile.TemporaryDirectory() as tmp:
            bridge.bind_job(
                session_key="s1",
                workspace=Path(tmp),
                edit_status=edit,
                display_name="Manager",
                running_view="cancel-view",
                cancel_event=cancel,
            )
            task = asyncio.create_task(
                bridge.request(
                    session_key="s1",
                    kind="shell",
                    summary="ls",
                    detail="ls",
                    timeout_sec=2,
                )
            )
            await asyncio.sleep(0.05)
            pending = bridge.list_pending()
            cancel.set()
            bridge.resolve(pending[0]["id"], allow=True, reason="test")
            await task
            restore = [e for e in edits if e[1].get("view") == "cancel-view"]
            self.assertFalse(restore)
            bridge.unbind_job("s1")

    async def test_progress_not_edited_while_awaiting(self) -> None:
        bridge = ApprovalBridge()
        edits: list[str] = []

        async def edit(text: str, **kwargs: object) -> None:
            edits.append(text)

        with tempfile.TemporaryDirectory() as tmp:
            bridge.bind_job(
                session_key="s1",
                workspace=Path(tmp),
                edit_status=edit,
                display_name="Manager",
            )
            task = asyncio.create_task(
                bridge.request(
                    session_key="s1",
                    kind="shell",
                    summary="ls",
                    detail="ls",
                    timeout_sec=0.2,
                )
            )
            await asyncio.sleep(0.05)
            self.assertTrue(bridge.is_awaiting("s1"))
            await task
            approval_edits = [e for e in edits if "Approval needed" in e]
            self.assertTrue(approval_edits)
            bridge.unbind_job("s1")


if __name__ == "__main__":
    unittest.main()
