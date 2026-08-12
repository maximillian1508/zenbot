from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Awaitable, Callable

from ..agents.registry import AgentRegistry
from ..backends.base import AgentBackend
from ..config.load import GatewayConfig
from ..sessions import SessionStore, ThreadSession
from ..skills.loader import build_prompt
from ..util.throttle import ThrottledProgress

log = logging.getLogger(__name__)


def title_from_prompt(prompt: str, max_len: int = 80) -> str:
    one_line = re.sub(r"\s+", " ", prompt.strip())
    if len(one_line) <= max_len:
        return one_line or "Agent task"
    return one_line[: max_len - 1] + "…"


@dataclass
class JobResult:
    text: str
    exit_code: int
    session_id: str | None
    error: str | None = None


SendText = Callable[[str], Awaitable[None]]
EditText = Callable[[str], Awaitable[None]]


@dataclass
class _QueuedJob:
    agent_id: str
    session_key: str
    user_prompt: str
    send: SendText
    edit_status: EditText


@dataclass
class _SessionState:
    queue: asyncio.Queue[_QueuedJob | None]
    worker: asyncio.Task[None] | None = None
    busy: bool = False


class Gateway:
    def __init__(self, config: GatewayConfig) -> None:
        self.config = config
        self.agents = config.agents
        self.backends = config.backends
        self.store = SessionStore(config.db)
        self._global_sem = asyncio.Semaphore(config.max_concurrent_jobs)
        self._sessions: dict[str, _SessionState] = {}
        self._sessions_guard = asyncio.Lock()

    def session_key(self, agent_id: str, transport: str, channel_id: str | int) -> str:
        return f"{agent_id}:{transport}:{channel_id}"

    def is_allowed(self, user_id: int) -> bool:
        return self.config.db.is_allowed(user_id)

    def backend_for(self, agent_id: str) -> AgentBackend:
        profile = self.agents.get(agent_id)
        backend = self.backends.get(profile.default_backend)
        if backend is None:
            raise KeyError(f"Backend {profile.default_backend!r} not configured")
        return backend

    def list_agents_markdown(self) -> str:
        lines = ["**Agent fleet**", ""]
        for profile in self.agents.all():
            role = "manager" if profile.is_manager else "specialist"
            transports: list[str] = []
            if profile.discord:
                transports.append("Discord")
            if profile.telegram:
                transports.append("Telegram")
            transport_str = ", ".join(transports) if transports else "—"
            lines.append(
                f"• **{profile.display_name}** (`{profile.id}`) — {role} · {transport_str}"
            )
        lines.append("")
        lines.append("Message the bot for each agent directly (one bot user per profile).")
        return "\n".join(lines)

    async def _ensure_session_worker(self, session_key: str) -> _SessionState:
        async with self._sessions_guard:
            state = self._sessions.get(session_key)
            if state is None:
                state = _SessionState(queue=asyncio.Queue())
                self._sessions[session_key] = state
            if state.worker is None or state.worker.done():
                state.worker = asyncio.create_task(
                    self._session_worker(session_key),
                    name=f"session-{session_key}",
                )
            return state

    async def run_job(
        self,
        *,
        agent_id: str,
        session_key: str,
        user_prompt: str,
        send: SendText,
        edit_status: EditText,
    ) -> JobResult:
        state = await self._ensure_session_worker(session_key)
        ahead = state.queue.qsize() + (1 if state.busy else 0)
        job = _QueuedJob(
            agent_id=agent_id,
            session_key=session_key,
            user_prompt=user_prompt,
            send=send,
            edit_status=edit_status,
        )
        await state.queue.put(job)

        if ahead > 0:
            plural = "s" if ahead > 1 else ""
            await edit_status(
                f"📋 **Queued** ({ahead} message{plural} ahead — "
                "runs when the current job in this thread finishes)"
            )
            return JobResult(text="", exit_code=0, session_id=None)

        return JobResult(text="", exit_code=0, session_id=None)

    async def _session_worker(self, session_key: str) -> None:
        state = self._sessions[session_key]
        while True:
            job = await state.queue.get()
            if job is None:
                state.queue.task_done()
                break
            state.busy = True
            try:
                await self._execute_job(job)
            except Exception:
                log.exception("Unhandled error running job for %s", session_key)
                try:
                    await job.edit_status("❌ Internal error — check logs.")
                except Exception:
                    log.exception("Failed to update status for %s", session_key)
            finally:
                state.busy = False
                state.queue.task_done()

    async def _execute_job(self, job: _QueuedJob) -> JobResult:
        await job.edit_status("⏳ Agent running…")

        profile = self.agents.get(job.agent_id)
        sess = self.store.get(job.session_key)
        full_prompt = build_prompt(
            agent_id=profile.id,
            display_name=profile.display_name,
            backend=profile.default_backend,
            workspace=profile.workspace,
            system_prompt=profile.system_prompt(self.config.project_root),
            skill_paths=profile.skills,
            user_message=job.user_prompt,
        )

        async with self._global_sem:
            progress = (
                ThrottledProgress(job.edit_status)
                if self.config.streaming_enabled
                else None
            )
            try:
                result = await self.backend_for(job.agent_id).run(
                    prompt=full_prompt,
                    workspace=profile.workspace,
                    session_id=sess.session_id,
                    on_progress=progress.push if progress else None,
                )
            except Exception as exc:
                log.exception("Agent run failed for %s", job.agent_id)
                await job.edit_status(f"❌ Failed to start agent: {exc}")
                return JobResult(
                    text=str(exc),
                    exit_code=1,
                    session_id=sess.session_id,
                    error=str(exc),
                )
            finally:
                if progress:
                    await progress.flush()

        prefix = "✅" if result.exit_code == 0 else "⚠️"
        header = f"{prefix} **{profile.display_name}**"
        if result.session_id:
            header += f" · session `{result.session_id[:8]}…`"
        await job.edit_status(header)

        body = result.text
        if result.error and result.exit_code != 0:
            body += f"\n\n```\n{result.error[:1500]}\n```"

        display = body[:8000] if len(body) > 8000 else body
        await job.send(display)
        if len(body) > 8000:
            await job.send(f"_(truncated — full output was {len(body)} chars)_")

        title = sess.title or title_from_prompt(job.user_prompt)
        self.store.set(
            job.session_key,
            ThreadSession(session_id=result.session_id, title=title),
        )
        return JobResult(
            text=body,
            exit_code=result.exit_code,
            session_id=result.session_id,
            error=result.error,
        )

    def clear_session(self, session_key: str) -> None:
        self.store.clear(session_key)

    def get_session(self, session_key: str) -> ThreadSession:
        return self.store.get(session_key)

    def set_session_title(self, session_key: str, title: str) -> None:
        sess = self.store.get(session_key)
        self.store.set(session_key, ThreadSession(session_id=sess.session_id, title=title))
