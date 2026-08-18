from __future__ import annotations

import unittest

from zen_agent_bot.gateway.router import Gateway, _QueuedJob, _RunHandle, _SessionState


def _gateway() -> Gateway:
    return Gateway.__new__(Gateway)


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

    async def test_missing_job(self) -> None:
        gw = _gateway()
        gw._sessions = {"k": _SessionState()}
        self.assertEqual(await gw.send_now("k", "nope"), "missing")
        self.assertEqual(await gw.send_now("gone", "queued"), "missing")


if __name__ == "__main__":
    unittest.main()
