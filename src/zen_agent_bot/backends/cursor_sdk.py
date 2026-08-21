from __future__ import annotations

import asyncio
import logging
import os
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from cursor_sdk import (
    AsyncClient,
    LocalAgentOptions,
    LocalSendOptions,
    SendOptions,
)

from ..model_select import CURSOR_SDK_FALLBACK
from .base import AgentRunResult, ProgressCallback, RegisterProc

if TYPE_CHECKING:
    from ..store import ConfigStore

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CursorSdkConfig:
    model: str | None = None
    force: bool = True
    timeout_sec: int = 3600
    api_key_env: str = "CURSOR_API_KEY"


def usable_sdk_session_id(session_id: str | None) -> str | None:
    """Keep SDK agent ids; drop OpenRouter `or-…` and empty values."""
    if not session_id:
        return None
    text = session_id.strip()
    if not text or text.startswith("or-"):
        return None
    return text


def _format_progress(text: str, max_len: int = 1800) -> str:
    header = "⏳ **Agent running…** (cursor-sdk)"
    if not text:
        return header
    preview = text[-max_len:] if len(text) > max_len else text
    return f"{header}\n\n{preview}"


def _status_str(obj: object) -> str:
    raw = getattr(obj, "status", None)
    if raw is None:
        return ""
    return str(getattr(raw, "value", raw)).lower()


def _result_text(result: object, streamed: str) -> str:
    raw = getattr(result, "result", None)
    if isinstance(raw, str) and raw.strip():
        return raw
    return streamed.strip() or "(no output)"


async def _cancel_run(run: Any) -> None:
    if run is None:
        return
    if _status_str(run) in {"finished", "error", "cancelled", "expired"}:
        return
    if hasattr(run, "supports") and not run.supports("cancel"):
        return
    try:
        await run.cancel()
    except Exception:
        log.debug("cursor-sdk run.cancel failed", exc_info=True)


def _cancelled_run_result(*, text: str, agent_id: str | None) -> SimpleNamespace:
    return SimpleNamespace(status="cancelled", result=text, agent_id=agent_id)


class CursorSdkBackend:
    """Local Cursor agent via `cursor-sdk` (bridge + stream + cancel + resume)."""

    def __init__(
        self,
        config: CursorSdkConfig,
        *,
        secrets: ConfigStore | None = None,
    ) -> None:
        self.config = config
        self._secrets = secrets

    def _api_key(self) -> str | None:
        if self._secrets is not None:
            key = self._secrets.resolve_secret(self.config.api_key_env)
        else:
            key = os.environ.get(self.config.api_key_env, "")
        text = (key or "").strip()
        return text or None

    def _model(self, model: str | None) -> str:
        return (model or self.config.model or CURSOR_SDK_FALLBACK).strip() or CURSOR_SDK_FALLBACK

    def _local(self, workspace: Path, *, auto_review: bool = False) -> LocalAgentOptions:
        return LocalAgentOptions(
            cwd=str(workspace),
            setting_sources=["all"],
            auto_review=True if auto_review else None,
        )

    async def _open_agent(
        self,
        client: Any,
        *,
        session_id: str | None,
        model: str,
        api_key: str | None,
        local: LocalAgentOptions,
    ) -> Any:
        resume_id = usable_sdk_session_id(session_id)
        create_kw: dict[str, Any] = {"model": model, "local": local}
        if api_key:
            create_kw["api_key"] = api_key
        if resume_id:
            try:
                options: dict[str, Any] = {"model": model, "local": local}
                if api_key:
                    options["api_key"] = api_key
                return await client.resume_agent(resume_id, options)
            except Exception as exc:
                log.info("cursor-sdk resume %s failed (%s); creating new agent", resume_id, exc)
        return await client.create_agent(**create_kw)

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
        history: list[dict[str, str]] | None = None,
        approval_mode: str | None = None,
    ) -> AgentRunResult:
        _ = history
        keep_id = usable_sdk_session_id(session_id)
        if cancel_event is not None and cancel_event.is_set():
            return AgentRunResult(
                text="Cancelled.",
                session_id=keep_id,
                exit_code=130,
                error="cancelled",
            )

        resolved_model = self._model(model)
        api_key = self._api_key()
        # LocalSendOptions.force means "expire a stuck prior run", not tool auto-approve.
        # Trust=approve gates shell/MCP via Discord hooks only; native auto_review
        # would race the hook and flash Accept/Deny before restoring Cancel.
        _ = approval_mode
        local = self._local(Path(workspace), auto_review=False)
        run: Any = None
        agent_id = keep_id

        try:
            async with await AsyncClient.launch_bridge(workspace=str(workspace)) as client:
                owned = getattr(client, "_owned_bridge", None)
                bridge_proc = getattr(owned, "process", None) if owned else None
                if register_proc is not None and bridge_proc is not None:
                    register_proc(bridge_proc)
                agent = await self._open_agent(
                    client,
                    session_id=session_id,
                    model=resolved_model,
                    api_key=api_key,
                    local=local,
                )
                async with agent:
                    agent_id = getattr(agent, "agent_id", None) or keep_id
                    send_opts = SendOptions(
                        model=resolved_model,
                        local=LocalSendOptions(force=True) if self.config.force else None,
                    )
                    run = await agent.send(prompt, send_opts)
                    text, result = await asyncio.wait_for(
                        self._consume(run, on_progress, cancel_event),
                        timeout=self.config.timeout_sec,
                    )
        except asyncio.TimeoutError:
            await _cancel_run(run)
            return AgentRunResult(
                text=f"cursor-sdk timed out after {self.config.timeout_sec}s",
                session_id=agent_id,
                exit_code=1,
                error="timeout",
            )
        except asyncio.CancelledError:
            await _cancel_run(run)
            raise
        except Exception as exc:
            log.exception("cursor-sdk run failed")
            return AgentRunResult(
                text=f"cursor-sdk error: {exc}",
                session_id=agent_id,
                exit_code=1,
                error=str(exc),
            )

        agent_id = getattr(result, "agent_id", None) or agent_id
        status = _status_str(result)
        if status == "cancelled" or (cancel_event is not None and cancel_event.is_set()):
            return AgentRunResult(
                text=_result_text(result, text) or "Cancelled.",
                session_id=agent_id,
                exit_code=130,
                error="cancelled",
            )
        if status == "error":
            body = _result_text(result, text)
            return AgentRunResult(
                text=body,
                session_id=agent_id,
                exit_code=1,
                error=body,
            )
        return AgentRunResult(
            text=_result_text(result, text),
            session_id=agent_id,
            exit_code=0,
        )

    async def _consume(
        self,
        run: Any,
        on_progress: ProgressCallback | None,
        cancel_event: asyncio.Event | None,
    ) -> tuple[str, Any]:
        text = ""
        agent_id = getattr(run, "agent_id", None)
        watcher: asyncio.Task[None] | None = None
        if cancel_event is not None:

            async def _watch() -> None:
                await cancel_event.wait()
                await _cancel_run(run)

            watcher = asyncio.create_task(_watch())
        try:
            if on_progress is not None:
                async for chunk in run.iter_text():
                    if cancel_event is not None and cancel_event.is_set():
                        break
                    if not chunk:
                        continue
                    text += chunk
                    await on_progress(_format_progress(text))
            if cancel_event is not None and cancel_event.is_set():
                await _cancel_run(run)
                return text, _cancelled_run_result(text=text, agent_id=agent_id)

            wait_task = asyncio.create_task(run.wait())
            try:
                if cancel_event is not None:
                    cancel_task = asyncio.create_task(cancel_event.wait())
                    done, _ = await asyncio.wait(
                        {wait_task, cancel_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if cancel_task in done and cancel_event.is_set():
                        wait_task.cancel()
                        with suppress(asyncio.CancelledError, Exception):
                            await wait_task
                        await _cancel_run(run)
                        return text, _cancelled_run_result(text=text, agent_id=agent_id)
                    if not wait_task.done():
                        cancel_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await cancel_task
                result = await wait_task
                agent_id = getattr(result, "agent_id", None) or agent_id
                return text, result
            finally:
                if not wait_task.done():
                    wait_task.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        await wait_task
        except asyncio.CancelledError:
            await _cancel_run(run)
            raise
        finally:
            if watcher is not None:
                watcher.cancel()
                with suppress(asyncio.CancelledError):
                    await watcher
