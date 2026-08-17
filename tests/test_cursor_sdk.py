from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from zen_agent_bot.backend_select import parse_backend_arg
from zen_agent_bot.backends import build_backends
from zen_agent_bot.backends.cursor_sdk import (
    CursorSdkBackend,
    CursorSdkConfig,
    usable_sdk_session_id,
)
from zen_agent_bot.model_select import CURSOR_SDK_FALLBACK, resolve_model
from zen_agent_bot.store import ConfigStore

KNOWN = frozenset({"cursor-cli", "cursor-sdk", "claude-cli", "openrouter"})


class UsableSessionIdTests(unittest.TestCase):
    def test_drops_empty_and_openrouter(self) -> None:
        self.assertIsNone(usable_sdk_session_id(None))
        self.assertIsNone(usable_sdk_session_id(""))
        self.assertIsNone(usable_sdk_session_id("  "))
        self.assertIsNone(usable_sdk_session_id("or-deadbeef"))

    def test_keeps_sdk_ids(self) -> None:
        self.assertEqual(usable_sdk_session_id("sdk-abc"), "sdk-abc")
        self.assertEqual(usable_sdk_session_id("  bc-cloud  "), "bc-cloud")


class ParseSdkBackendTests(unittest.TestCase):
    def test_aliases(self) -> None:
        self.assertEqual(parse_backend_arg("sdk", known=KNOWN), ("set", "cursor-sdk"))
        self.assertEqual(
            parse_backend_arg("cursor-sdk", known=KNOWN), ("set", "cursor-sdk")
        )
        self.assertEqual(
            parse_backend_arg("cursor_sdk", known=KNOWN), ("set", "cursor-sdk")
        )
        self.assertEqual(
            parse_backend_arg("cursor", known=KNOWN), ("set", "cursor-cli")
        )


class ResolveSdkModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = ConfigStore(Path(self.tmp.name) / "gateway.db")
        self._agent_model = os_pop("AGENT_MODEL")

    def tearDown(self) -> None:
        os_restore("AGENT_MODEL", self._agent_model)
        self.db.close()
        self.tmp.cleanup()

    def test_fallback_composer(self) -> None:
        resolved = resolve_model(self.db, "missing", "cursor-sdk")
        self.assertEqual(resolved.model, CURSOR_SDK_FALLBACK)
        self.assertEqual(resolved.source, "default")

    def test_falls_back_to_cursor_cli_admin(self) -> None:
        self.db.set_setting("backend.cursor-cli.model", "composer-2")
        resolved = resolve_model(self.db, "missing", "cursor-sdk")
        self.assertEqual(resolved.model, "composer-2")
        self.assertEqual(resolved.source, "admin")

    def test_own_setting_beats_cli(self) -> None:
        self.db.set_setting("backend.cursor-cli.model", "composer-2")
        self.db.set_setting("backend.cursor-sdk.model", "composer-2.5")
        resolved = resolve_model(self.db, "missing", "cursor-sdk")
        self.assertEqual(resolved.model, "composer-2.5")
        self.assertEqual(resolved.source, "admin")


class BuildBackendsTests(unittest.TestCase):
    def test_always_registers_cursor_sdk(self) -> None:
        backends = build_backends({})
        self.assertIn("cursor-sdk", backends)
        self.assertIsInstance(backends["cursor-sdk"], CursorSdkBackend)


class CursorSdkRunTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)

    async def asyncTearDown(self) -> None:
        self.tmp.cleanup()

    async def test_stream_and_resume(self) -> None:
        run = FakeRun(chunks=("hello ", "world"))
        agent = FakeAgent(run, agent_id="sdk-1")
        client = FakeClient(agent)
        backend = CursorSdkBackend(CursorSdkConfig(model="composer-2.5", timeout_sec=5))
        progress: list[str] = []

        async def on_progress(text: str) -> None:
            progress.append(text)

        with patch(
            "zen_agent_bot.backends.cursor_sdk.AsyncClient.launch_bridge",
            new=AsyncMock(return_value=client),
        ):
            result = await backend.run(
                prompt="hi",
                workspace=self.workspace,
                session_id="sdk-1",
                on_progress=on_progress,
                model="composer-2.5",
            )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.text, "hello world")
        self.assertEqual(result.session_id, "sdk-1")
        self.assertEqual(client.resumed, "sdk-1")
        self.assertIsNone(client.created)
        self.assertTrue(progress)
        self.assertIn("hello world", progress[-1])

    async def test_cancel_mid_stream(self) -> None:
        run = FakeRun(chunks=("partial", " more"), wait_after_first=True)
        agent = FakeAgent(run, agent_id="sdk-2")
        client = FakeClient(agent)
        backend = CursorSdkBackend(CursorSdkConfig(timeout_sec=5))
        cancel = asyncio.Event()

        async def on_progress(text: str) -> None:
            if "partial" in text:
                cancel.set()

        with patch(
            "zen_agent_bot.backends.cursor_sdk.AsyncClient.launch_bridge",
            new=AsyncMock(return_value=client),
        ):
            result = await backend.run(
                prompt="hi",
                workspace=self.workspace,
                session_id=None,
                on_progress=on_progress,
                cancel_event=cancel,
            )

        self.assertEqual(result.exit_code, 130)
        self.assertEqual(result.error, "cancelled")
        self.assertTrue(run.cancel_called)
        self.assertEqual(client.created is not None, True)

    async def test_skips_openrouter_session(self) -> None:
        run = FakeRun(chunks=("ok",), result="ok", agent_id="sdk-new")
        agent = FakeAgent(run, agent_id="sdk-new")
        client = FakeClient(agent)
        backend = CursorSdkBackend(CursorSdkConfig(timeout_sec=5))
        with patch(
            "zen_agent_bot.backends.cursor_sdk.AsyncClient.launch_bridge",
            new=AsyncMock(return_value=client),
        ):
            result = await backend.run(
                prompt="hi",
                workspace=self.workspace,
                session_id="or-abc123",
            )
        self.assertIsNone(client.resumed)
        self.assertIsNotNone(client.created)
        self.assertEqual(result.session_id, "sdk-new")
        self.assertEqual(result.exit_code, 0)


def os_pop(name: str) -> str | None:
    import os

    return os.environ.pop(name, None)


def os_restore(name: str, value: str | None) -> None:
    import os

    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


class FakeRun:
    def __init__(
        self,
        *,
        chunks: tuple[str, ...],
        result: str | None = None,
        wait_after_first: bool = False,
        agent_id: str = "sdk-1",
    ) -> None:
        self.chunks = chunks
        self.result_text = result if result is not None else "".join(chunks)
        self.wait_after_first = wait_after_first
        self.agent_id = agent_id
        self.status = "running"
        self.cancel_called = False
        self._proceed = asyncio.Event()
        if not wait_after_first:
            self._proceed.set()

    def supports(self, operation: str) -> bool:
        return operation == "cancel"

    async def iter_text(self):
        for i, chunk in enumerate(self.chunks):
            if self.cancel_called:
                return
            yield chunk
            if i == 0 and self.wait_after_first:
                await self._proceed.wait()
        if not self.cancel_called:
            self.status = "finished"

    async def wait(self):
        status = "cancelled" if self.cancel_called else "finished"
        self.status = status
        return SimpleNamespace(
            status=status, result=self.result_text, agent_id=self.agent_id
        )

    async def cancel(self) -> None:
        self.cancel_called = True
        self.status = "cancelled"
        self._proceed.set()


class FakeAgent:
    def __init__(self, run: FakeRun, *, agent_id: str) -> None:
        self.run = run
        self.agent_id = agent_id
        self.prompt = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def send(self, message: str, options: object = None, **_: object):
        self.prompt = message
        self.send_opts = options
        return self.run


class FakeClient:
    def __init__(self, agent: FakeAgent) -> None:
        self.agent = agent
        self.resumed: str | None = None
        self.created: dict | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def create_agent(self, **kwargs: object):
        self.created = dict(kwargs)
        return self.agent

    async def resume_agent(self, agent_id: str, options: object = None):
        self.resumed = agent_id
        self.resume_options = options
        return self.agent


if __name__ == "__main__":
    unittest.main()
