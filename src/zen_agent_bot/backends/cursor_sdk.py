from __future__ import annotations

import asyncio
import logging
import os
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cursor_sdk import (
    AsyncClient,
    LocalAgentOptions,
    LocalSendOptions,
    SendOptions,
)

from ..model_select import CURSOR_SDK_FALLBACK
from ..trust_mode import TRUST_APPROVE, TRUST_FORCE
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
    with suppress(Exception):
        await run.cancel()


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
        _ = register_proc, history
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
        # Tool gating for trust=approve uses auto_review + Discord hooks.
        want_review = (approval_mode or TRUST_FORCE).strip().lower() == TRUST_APPROVE
        local = self._local(Path(workspace), auto_review=want_review)
        run: Any = None
        agent_id = keep_id

        try:
            async with await AsyncClient.launch_bridge(workspace=str(workspace)) as client:
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
        watcher: asyncio.Task[None] | None = None
        if cancel_event is not None:

            async def _watch() -> None:
                await cancel_event.wait()
                await _cancel_run(run)

            watcher = asyncio.create_task(_watch())
        try:
            if on_progress is not None:
                async for chunk in run.iter_text():
                    if not chunk:
                        continue
                    text += chunk
                    await on_progress(_format_progress(text))
            result = await run.wait()
            return text, result
        except asyncio.CancelledError:
            await _cancel_run(run)
            raise
        finally:
            if watcher is not None:
                watcher.cancel()
                with suppress(asyncio.CancelledError):
                    await watcher
