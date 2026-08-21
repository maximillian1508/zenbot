from __future__ import annotations

import unittest

from zen_agent_bot.approvals import ApprovalBridge
from zen_agent_bot.gateway.router import Gateway, _QueuedJob, _RunHandle, _SessionState


def _gateway() -> Gateway:
    gw = Gateway.__new__(Gateway)
    gw.approvals = ApprovalBridge()
    return gw


class SendNowRaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancels_only_handle_captured_under_lock(self) -> None:
        gw = _gateway()
        gw._sessions = {}
        state = _SessionState()
        old = _RunHandle()
        successor = _RunHandle()
        state.busy = True
        state.run_handle = old

        async def edit(text: str, **_kwargs: object) -> None:
            # Previous job finished; worker already started the queued job.
            state.run_handle = successor

        async def send(_text: str) -> None:
            return None

        job = _QueuedJob(
            job_id="queued",
            agent_id="manager",
            session_key="k",
            user_prompt="follow-up",
            send=send,
            edit_status=edit,
            ready=True,
        )
        state.pending.append(job)
        gw._sessions["k"] = state

        result = await gw.send_now("k", "queued")
        self.assertEqual(result, "cancelled")
        self.assertTrue(old.cancel_event.is_set())
        self.assertFalse(successor.cancel_event.is_set())
        self.assertIs(state.run_handle, successor)

    async def test_idle_thread_promotes_without_cancel(self) -> None:
        gw = _gateway()
        gw._sessions = {}
        state = _SessionState()
        leftover = _RunHandle()
        state.busy = False
        state.run_handle = leftover
        edited: list[str] = []

        async def edit(text: str, **_kwargs: object) -> None:
            edited.append(text)

        async def send(_text: str) -> None:
            return None

        job = _QueuedJob(
            job_id="queued",
            agent_id="manager",
            session_key="k",
            user_prompt="follow-up",
            send=send,
            edit_status=edit,
            ready=True,
        )
        state.pending.append(job)
        gw._sessions["k"] = state

        result = await gw.send_now("k", "queued")
        self.assertEqual(result, "promoted")
        self.assertFalse(leftover.cancel_event.is_set())
        self.assertTrue(edited)
        self.assertIn("starting", edited[0].lower())

    async def test_cancel_session_sets_event(self) -> None:
        gw = _gateway()
        gw._sessions = {}
        state = _SessionState()
        handle = _RunHandle()
        state.busy = True
        state.run_handle = handle
        gw._sessions["k"] = state

        ok = await gw.cancel_session("k", reason="stopped by Cancel")
        self.assertTrue(ok)
        self.assertTrue(handle.cancel_event.is_set())
        self.assertEqual(handle.cancel_reason, "stopped by Cancel")
        self.assertFalse(await gw.cancel_session("idle"))

    async def test_cancel_session_edits_status_immediately(self) -> None:
        gw = _gateway()
        gw._sessions = {}
        state = _SessionState()
        edited: list[tuple[str, object]] = []

        async def edit(text: str, **kwargs: object) -> None:
            edited.append((text, kwargs.get("view", "missing")))

        async def progress_cb(_text: str) -> None:
            return None

        from zen_agent_bot.util.throttle import ThrottledProgress

        handle = _RunHandle()
        handle.display_name = "Zen Manager"
        handle.edit_status = edit
        handle.progress = ThrottledProgress(progress_cb, min_interval=0)
        await handle.progress.push("⏳ **Agent running…**\n\nworking")
        state.busy = True
        state.run_handle = handle
        gw._sessions["k"] = state

        ok = await gw.cancel_session("k", reason="stopped by Cancel")
        self.assertTrue(ok)
        self.assertEqual(len(edited), 1)
        self.assertIn("Cancelling", edited[0][0])
        self.assertIn("stopped by Cancel", edited[0][0])
        self.assertIsNone(edited[0][1])
        await handle.progress.push("should not land")
        self.assertEqual(len(edited), 1)

    async def test_missing_job(self) -> None:
        gw = _gateway()
        gw._sessions = {"k": _SessionState()}
        self.assertEqual(await gw.send_now("k", "nope"), "missing")
        self.assertEqual(await gw.send_now("gone", "queued"), "missing")


if __name__ == "__main__":
    unittest.main()
