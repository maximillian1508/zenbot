from __future__ import annotations

import asyncio
import json
import os
import uuid
from contextlib import suppress
from dataclasses import dataclass

import httpx

from .base import AgentRunResult, ProgressCallback, RegisterProc

CHAT_ONLY_SYSTEM = (
    "You are a chat assistant via OpenRouter. You have no shell, filesystem, or "
    "tool access in this backend — answer from knowledge and the prompt only. "
    "If the user needs code changes or server actions, say they should switch to "
    "a cursor-cli agent profile."
)


@dataclass(frozen=True)
class OpenRouterConfig:
    api_key_env: str = "OPENROUTER_API_KEY"
    model: str = "anthropic/claude-sonnet-4"
    base_url: str = "https://openrouter.ai/api/v1"
    timeout_sec: int = 300
    site_url: str | None = None
    site_name: str | None = "zen-agent-bot"


def _format_progress(text: str, max_len: int = 1800) -> str:
    header = "💬 **OpenRouter…**"
    if not text:
        return header
    preview = text[-max_len:] if len(text) > max_len else text
    return f"{header}\n\n{preview}"


class OpenRouterBackend:
    """OpenAI-compatible chat completions via OpenRouter (no tools / no shell)."""

    def __init__(self, config: OpenRouterConfig) -> None:
        self.config = config

    def _api_key(self) -> str:
        key = os.environ.get(self.config.api_key_env, "").strip()
        if not key:
            raise RuntimeError(
                f"OpenRouter API key missing — set {self.config.api_key_env} in .env"
            )
        return key

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._api_key()}",
            "Content-Type": "application/json",
        }
        if self.config.site_url:
            headers["HTTP-Referer"] = self.config.site_url
        if self.config.site_name:
            headers["X-Title"] = self.config.site_name
        return headers

    def _session_id(self, session_id: str | None) -> str:
        if session_id and session_id.startswith("or-"):
            return session_id
        return f"or-{uuid.uuid4().hex[:16]}"

    async def run(
        self,
        *,
        prompt: str,
        workspace: object,
        session_id: str | None,
        on_progress: ProgressCallback | None = None,
        cancel_event: asyncio.Event | None = None,
        register_proc: RegisterProc | None = None,
        model: str | None = None,
    ) -> AgentRunResult:
        _ = workspace, register_proc  # chat-only; no subprocess
        new_session = self._session_id(session_id)
        url = f"{self.config.base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": model or self.config.model,
            "messages": [
                {"role": "system", "content": CHAT_ONLY_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            "stream": on_progress is not None,
        }

        if cancel_event and cancel_event.is_set():
            return AgentRunResult(
                text="Cancelled.",
                session_id=new_session,
                exit_code=130,
                error="cancelled",
            )

        try:
            if on_progress is not None:
                return await self._run_stream(
                    url=url,
                    payload=payload,
                    session_id=new_session,
                    on_progress=on_progress,
                    cancel_event=cancel_event,
                )
            return await self._run_once(
                url=url,
                payload=payload,
                session_id=new_session,
                cancel_event=cancel_event,
            )
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:1500]
            return AgentRunResult(
                text=f"OpenRouter HTTP {exc.response.status_code}: {body}",
                session_id=new_session,
                exit_code=1,
                error=body,
            )
        except Exception as exc:
            return AgentRunResult(
                text=f"OpenRouter error: {exc}",
                session_id=new_session,
                exit_code=1,
                error=str(exc),
            )

    async def _run_once(
        self,
        *,
        url: str,
        payload: dict,
        session_id: str,
        cancel_event: asyncio.Event | None,
    ) -> AgentRunResult:
        timeout = httpx.Timeout(self.config.timeout_sec)
        async with httpx.AsyncClient(timeout=timeout) as client:
            req_task = asyncio.create_task(
                client.post(url, headers=self._headers(), json=payload)
            )
            if cancel_event:
                cancel_wait = asyncio.create_task(cancel_event.wait())
                done, pending = await asyncio.wait(
                    {req_task, cancel_wait},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task
                if cancel_wait in done:
                    return AgentRunResult(
                        text="Cancelled.",
                        session_id=session_id,
                        exit_code=130,
                        error="cancelled",
                    )
                resp = req_task.result()
            else:
                resp = await req_task

        resp.raise_for_status()
        data = resp.json()
        text = _choice_text(data) or "(no output)"
        return AgentRunResult(text=text, session_id=session_id, exit_code=0)

    async def _run_stream(
        self,
        *,
        url: str,
        payload: dict,
        session_id: str,
        on_progress: ProgressCallback,
        cancel_event: asyncio.Event | None,
    ) -> AgentRunResult:
        timeout = httpx.Timeout(self.config.timeout_sec)
        text = ""
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST", url, headers=self._headers(), json=payload
            ) as resp:
                if resp.status_code >= 400:
                    err_body = (await resp.aread()).decode("utf-8", errors="replace")[:1500]
                    return AgentRunResult(
                        text=f"OpenRouter HTTP {resp.status_code}: {err_body}",
                        session_id=session_id,
                        exit_code=1,
                        error=err_body,
                    )
                async for line in resp.aiter_lines():
                    if cancel_event and cancel_event.is_set():
                        return AgentRunResult(
                            text=text or "Cancelled.",
                            session_id=session_id,
                            exit_code=130,
                            error="cancelled",
                        )
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw or raw == "[DONE]":
                        continue
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    chunk = _delta_text(event)
                    if chunk:
                        text += chunk
                        await on_progress(_format_progress(text))

        if cancel_event and cancel_event.is_set():
            return AgentRunResult(
                text=text or "Cancelled.",
                session_id=session_id,
                exit_code=130,
                error="cancelled",
            )
        return AgentRunResult(
            text=text or "(no output)",
            session_id=session_id,
            exit_code=0,
        )


def _choice_text(data: dict) -> str:
    choices = data.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return str(message.get("content") or "").strip()


def _delta_text(event: dict) -> str:
    choices = event.get("choices") or []
    if not choices:
        return ""
    delta = choices[0].get("delta") or {}
    return str(delta.get("content") or "")
