from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..agents.registry import AgentRegistry
from ..approvals import ApprovalBridge
from ..backends.base import AgentBackend, is_stream_line_too_large
from ..config.load import GatewayConfig
from ..backend_select import (
    ResolvedBackend,
    format_backend_catalog,
    format_backend_status,
    parse_backend_arg,
    resolve_backend,
)
from ..model_select import (
    format_cursor_catalog,
    format_model_status,
    model_in_catalog,
    parse_agent_models_output,
    parse_model_arg,
    resolve_model,
)
from ..notify import append_status_line, format_job_done_ping, should_ping_done
from ..schedule import format_schedules_markdown
from ..sessions import SessionStore, ThreadSession
from ..skills.loader import build_prompt
from ..trust_mode import format_trust_status, parse_trust_arg, resolve_trust
from ..util.proc import terminate_process
from ..util.rebuild import request_rebuild as write_rebuild_flag, rebuild_pending
from ..util.throttle import ThrottledProgress
from .queue import drop_by_id, promote_by_id, queued_count

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
    queued_job_id: str | None = None


SendText = Callable[[str], Awaitable[None]]
EditText = Callable[..., Awaitable[None]]
_STOP = object()


@dataclass
class _QueuedJob:
    job_id: str
    agent_id: str
    session_key: str
    user_prompt: str
    send: SendText
    edit_status: EditText
    ready: bool = True
    notify_mention: str | None = None
    schedule_id: str | None = None
    on_done: Callable[["JobResult"], Awaitable[None]] | None = None
    done_view: object | None = None
    running_view: object | None = None
    workspace_override: Path | None = None


@dataclass
class _RunHandle:
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    proc: asyncio.subprocess.Process | None = None
    cancel_reason: str = "stopped"
    progress: ThrottledProgress | None = None
    edit_status: EditText | None = None
    display_name: str = "Agent"

    def register_proc(self, proc: asyncio.subprocess.Process) -> None:
        self.proc = proc

    async def cancel(self, reason: str = "stopped by /cancel") -> None:
        self.cancel_reason = reason
        self.cancel_event.set()
        if self.progress is not None:
            self.progress.stop()
        if self.edit_status is not None:
            try:
                existing = self.progress.latest if self.progress else ""
                await self.edit_status(
                    _status_with_footer(
                        existing=existing,
                        emoji="🛑",
                        title="Cancelling",
                        display_name=self.display_name,
                        reason=reason,
                    ),
                    view=None,
                )
            except Exception:
                log.exception("Failed to mark job as cancelling")
        proc = self.proc
        if proc is not None:
            asyncio.create_task(
                terminate_process(proc), name="terminate-cancelled-job"
            )


def _status_with_footer(
    *,
    existing: str,
    emoji: str,
    title: str,
    display_name: str,
    reason: str,
    limit: int = 2000,
) -> str:
    """Keep streamed/partial text and append an outcome note (do not wipe)."""
    footer = f"\n\n———\n{emoji} **{title}** · {display_name}\n_{reason}_"
    body = (existing or "").strip()
    placeholders = {
        "Cancelled.",
        "Agent run timed out.",
        "Claude Code run timed out.",
        "(interrupted)",
        "⏳ Agent running…",
        "⏳ **Agent running…**",
        "⏳ **Claude Code…**",
    }
    if not body or body in placeholders:
        bare = f"{emoji} **{title}** · {display_name}\n_{reason}_"
        return bare[:limit]

    room = limit - len(footer)
    if room < 80:
        return f"{emoji} **{title}** · {display_name}\n_{reason}_"[:limit]
    if len(body) > room:
        body = body[: room - 20].rstrip() + "\n\n_(truncated)_"
    return body + footer


def _cancelled_status_message(
    *,
    existing: str,
    display_name: str,
    reason: str,
    limit: int = 2000,
) -> str:
    return _status_with_footer(
        existing=existing,
        emoji="🛑",
        title="Cancelled",
        display_name=display_name,
        reason=reason,
        limit=limit,
    )


def _friendly_failure_reason(exc: BaseException) -> str:
    if is_stream_line_too_large(exc):
        return (
            "stream line too large for the gateway reader "
            "(huge Cursor NDJSON event). Retry this follow-up."
        )
    msg = str(exc).strip() or type(exc).__name__
    return msg[:280]


def _interrupt_reason(error: str | None) -> str | None:
    if error == "stream_line_too_large":
        return (
            "stream line too large for the gateway reader "
            "(huge Cursor NDJSON event). Retry this follow-up."
        )
    if error == "timeout":
        return "agent run timed out"
    return None


@dataclass
class _SessionState:
    pending: deque[Any] = field(default_factory=deque)
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    worker: asyncio.Task[None] | None = None
    busy: bool = False
    run_handle: _RunHandle | None = None
    current_agent_id: str | None = None
    current_prompt: str | None = None
    current_schedule_id: str | None = None
    started_at: float | None = None


@dataclass
class _ErrorRecord:
    at: float
    session_key: str
    agent_id: str
    error: str


class Gateway:
    def __init__(self, config: GatewayConfig) -> None:
        self.config = config
        self.agents = config.agents
        self.backends = config.backends
        self.store = SessionStore(config.db)
        self._global_sem = asyncio.Semaphore(config.max_concurrent_jobs)
        self._sessions: dict[str, _SessionState] = {}
        self._sessions_guard = asyncio.Lock()
        self._shutting_down = False
        self._last_errors: deque[_ErrorRecord] = deque(maxlen=20)
        self._agent_status_cache: tuple[float, str] | None = None
        self._cursor_models_cache: tuple[float, list[tuple[str, str]]] | None = None
        self._cron_launchers: dict[str, Callable[..., Awaitable[dict[str, str | None]]]] = {}
        self.scheduler: Any = None
        self.approvals = ApprovalBridge()
        self._approval_view_factory: Callable[[str, str], object] | None = None

    def set_approval_view_factory(
        self, factory: Callable[[str, str], object] | None
    ) -> None:
        """factory(session_key, approval_id) -> discord.ui.View"""
        self._approval_view_factory = factory

    async def request_tool_approval(
        self,
        *,
        session_key: str,
        kind: str,
        summary: str,
        detail: str = "",
        timeout_sec: float = 300.0,
    ) -> bool:
        def attach(approval_id: str) -> object | None:
            if self._approval_view_factory is None:
                return None
            return self._approval_view_factory(session_key, approval_id)

        return await self.approvals.request(
            session_key=session_key,
            kind=kind,
            summary=summary,
            detail=detail,
            timeout_sec=timeout_sec,
            attach_view=attach,
        )

    def session_key(self, agent_id: str, transport: str, channel_id: str | int) -> str:
        return f"{agent_id}:{transport}:{channel_id}"

    def register_cron_launcher(
        self,
        agent_id: str,
        fn: Callable[..., Awaitable[dict[str, str | None]]],
    ) -> None:
        self._cron_launchers[agent_id] = fn

    def cron_launcher(
        self, agent_id: str
    ) -> Callable[..., Awaitable[dict[str, str | None]]] | None:
        return self._cron_launchers.get(agent_id)

    def schedules_markdown(self) -> str:
        return format_schedules_markdown(self.config.db.list_schedules())

    def is_allowed(self, user_id: int) -> bool:
        return self.config.db.is_allowed(user_id)

    def known_backends(self) -> set[str]:
        return set(self.backends.keys())

    def resolved_backend(self, agent_id: str, session_key: str) -> ResolvedBackend:
        profile = self.agents.get(agent_id)
        return resolve_backend(
            self.config.db,
            session_key,
            profile.default_backend,
            known=self.known_backends(),
        )

    def backend_for(self, agent_id: str, session_key: str | None = None) -> AgentBackend:
        profile = self.agents.get(agent_id)
        name = profile.default_backend
        if session_key:
            name = self.resolved_backend(agent_id, session_key).backend
        backend = self.backends.get(name)
        if backend is None:
            raise KeyError(f"Backend {name!r} not configured")
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
        lines.append(
            "One Discord bot. `/music`, `/general`, `/manager` (or `/run`) open a "
            "thread in that agent's channel. `/handoff` picks who continues a thread; "
            "non-manager jobs get an **Ask Manager** button. Plain messages in `#agent` "
            "are manager."
        )
        return "\n".join(lines)

    async def _ensure_session_worker(self, session_key: str) -> _SessionState:
        async with self._sessions_guard:
            state = self._sessions.get(session_key)
            if state is None:
                state = _SessionState()
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
        on_queued: Callable[[str], Awaitable[None]] | None = None,
        notify_mention: str | None = None,
        schedule_id: str | None = None,
        on_done: Callable[[JobResult], Awaitable[None]] | None = None,
        done_view: object | None = None,
        running_view: object | None = None,
        workspace_override: Path | None = None,
    ) -> JobResult:
        if self._shutting_down:
            await edit_status("⚠️ Gateway is shutting down — try again after restart.")
            return JobResult(text="", exit_code=0, session_id=None)

        state = await self._ensure_session_worker(session_key)
        job = _QueuedJob(
            job_id=uuid.uuid4().hex[:12],
            agent_id=agent_id,
            session_key=session_key,
            user_prompt=user_prompt,
            send=send,
            edit_status=edit_status,
            notify_mention=notify_mention,
            schedule_id=schedule_id,
            on_done=on_done,
            done_view=done_view,
            running_view=running_view,
            workspace_override=workspace_override,
        )
        async with state.lock:
            ahead = queued_count(state.pending, stop=_STOP) + (1 if state.busy else 0)
            job.ready = ahead == 0
            state.pending.append(job)

        if ahead > 0:
            plural = "s" if ahead > 1 else ""
            await edit_status(
                f"📋 **Queued** ({ahead} message{plural} ahead — "
                "runs when the current job in this thread finishes, "
                "or tap **Send now** to stop it)"
            )
            if on_queued is not None:
                try:
                    await on_queued(job.job_id)
                except Exception:
                    log.exception("Failed to attach queue controls for %s", job.job_id)
            job.ready = True
            state.wake.set()
            return JobResult(
                text="", exit_code=0, session_id=None, queued_job_id=job.job_id
            )

        state.wake.set()
        return JobResult(text="", exit_code=0, session_id=None)

    async def cancel_session(
        self, session_key: str, *, reason: str = "stopped by /cancel"
    ) -> bool:
        state = self._sessions.get(session_key)
        if state is None or not state.busy or state.run_handle is None:
            return False
        await state.run_handle.cancel(reason)
        return True

    async def send_now(self, session_key: str, job_id: str) -> str:
        """Stop & send: promote this queued job and cancel the in-flight run.

        Capture the current run handle under the lock. After awaits, the worker
        may already have started *this* job — never cancel a newer handle.
        """
        state = self._sessions.get(session_key)
        if state is None:
            return "missing"
        async with state.lock:
            job = promote_by_id(state.pending, job_id)
            if job is None or not isinstance(job, _QueuedJob):
                return "missing"
            job.ready = True
            in_flight = state.run_handle if state.busy else None
            state.wake.set()
        try:
            await job.edit_status(
                "⏭ **Sending now** — stopping the current job…"
                if in_flight is not None
                else "⏭ **Sending now** — starting…",
                view=None,
            )
        except Exception:
            log.exception("Failed to update queued status for send-now %s", job_id)
        if in_flight is not None:
            await in_flight.cancel("stopped by Send now (queued follow-up)")
            return "cancelled"
        return "promoted"

    async def close_session(self, session_key: str) -> dict[str, int | bool]:
        """Cancel in-flight work and drop the queue. Keep SQLite --resume mapping."""
        cancelled = await self.cancel_session(session_key, reason="stopped by /close")
        to_drop: list[_QueuedJob] = []
        state = self._sessions.get(session_key)
        if state is not None:
            async with state.lock:
                leftover: deque[Any] = deque()
                while state.pending:
                    item = state.pending.popleft()
                    if item is _STOP:
                        leftover.append(item)
                    elif isinstance(item, _QueuedJob):
                        to_drop.append(item)
                state.pending.extend(leftover)
                state.wake.set()
        for job in to_drop:
            try:
                await job.edit_status("🗑 Dropped — session closed.", view=None)
            except Exception:
                log.exception("Failed to update dropped status on close %s", session_key)
        return {"cancelled": cancelled, "dropped": len(to_drop)}

    async def drop_queued(self, session_key: str, job_id: str) -> bool:
        state = self._sessions.get(session_key)
        if state is None:
            return False
        async with state.lock:
            job = drop_by_id(state.pending, job_id)
        if job is None or not isinstance(job, _QueuedJob):
            return False
        try:
            await job.edit_status("🗑 Dropped from queue.", view=None)
        except Exception:
            log.exception("Failed to update dropped status for %s", job_id)
        return True

    async def shutdown(self, grace_sec: float = 180.0) -> None:
        self._shutting_down = True
        log.info("Gateway shutdown started (grace=%ss)", grace_sec)
        deadline = time.monotonic() + grace_sec

        while time.monotonic() < deadline:
            busy = any(s.busy for s in self._sessions.values())
            if not busy:
                break
            await asyncio.sleep(0.25)

        busy_keys = [k for k, s in self._sessions.items() if s.busy]
        if busy_keys:
            log.warning("Grace expired — cancelling %d running job(s)", len(busy_keys))
            for key in busy_keys:
                state = self._sessions[key]
                if state.run_handle is not None:
                    await state.run_handle.cancel(
                        "gateway restart — shutdown grace expired "
                        f"({int(grace_sec)}s); reply kept above"
                    )

            cancel_deadline = time.monotonic() + 10.0
            while time.monotonic() < cancel_deadline:
                if not any(s.busy for s in self._sessions.values()):
                    break
                await asyncio.sleep(0.25)

        for state in self._sessions.values():
            if state.worker is not None and not state.worker.done():
                async with state.lock:
                    state.pending.append(_STOP)
                state.wake.set()

        log.info("Gateway shutdown complete")

    async def _next_job(self, state: _SessionState) -> _QueuedJob | None:
        while True:
            async with state.lock:
                while state.pending:
                    item = state.pending[0]
                    if item is _STOP:
                        state.pending.popleft()
                        return None
                    if isinstance(item, _QueuedJob):
                        if not item.ready:
                            break
                        state.pending.popleft()
                        return item
                    state.pending.popleft()
                state.wake.clear()
            await state.wake.wait()

    async def _session_worker(self, session_key: str) -> None:
        state = self._sessions[session_key]
        while True:
            job = await self._next_job(state)
            if job is None:
                break
            if self._shutting_down:
                try:
                    await job.edit_status(
                        "⚠️ Skipped — gateway shutting down.", view=None
                    )
                except Exception:
                    log.exception("Failed to update status for %s", session_key)
                continue
            state.busy = True
            state.current_agent_id = job.agent_id
            state.current_prompt = job.user_prompt
            state.current_schedule_id = job.schedule_id
            state.started_at = time.time()
            result: JobResult | None = None
            try:
                result = await self._execute_job(job)
                if result.error and result.error != "cancelled":
                    self._record_error(
                        session_key=job.session_key,
                        agent_id=job.agent_id,
                        error=result.error,
                    )
            except Exception as exc:
                log.exception("Unhandled error running job for %s", session_key)
                self._record_error(
                    session_key=job.session_key,
                    agent_id=job.agent_id,
                    error=str(exc),
                )
                result = JobResult(
                    text="", exit_code=1, session_id=None, error=str(exc)
                )
                try:
                    await self._edit_final(
                        job,
                        state,
                        result,
                        "❌ Internal error — check logs.",
                    )
                except Exception:
                    log.exception("Failed to update status for %s", session_key)
            finally:
                if job.on_done is not None and result is not None:
                    try:
                        await job.on_done(result)
                    except Exception:
                        log.exception("schedule on_done failed for %s", job.job_id)
                state.busy = False
                state.run_handle = None
                state.current_agent_id = None
                state.current_prompt = None
                state.current_schedule_id = None
                state.started_at = None

    def _done_ping_line(
        self,
        job: _QueuedJob,
        state: _SessionState,
        result: JobResult,
    ) -> str | None:
        cancel_reason = ""
        if state.run_handle is not None:
            cancel_reason = state.run_handle.cancel_reason
        started = state.started_at or time.time()
        elapsed = time.time() - started
        ping_on = (self.config.db.get_setting("job_done_ping") or "true").strip().lower() not in (
            "0",
            "false",
            "no",
            "off",
        )
        if not should_ping_done(
            error=result.error,
            cancel_reason=cancel_reason,
            elapsed_sec=elapsed,
            enabled=ping_on,
        ):
            return None
        return format_job_done_ping(
            mention=job.notify_mention,
            exit_code=result.exit_code,
            error=result.error,
            elapsed_sec=elapsed,
        )

    async def _edit_final(
        self,
        job: _QueuedJob,
        state: _SessionState,
        result: JobResult,
        status: str,
    ) -> None:
        ping = self._done_ping_line(job, state, result)
        if ping:
            status = append_status_line(status, ping)
        await job.edit_status(status, view=job.done_view)

    async def _execute_job(self, job: _QueuedJob) -> JobResult:
        state = self._sessions[job.session_key]
        run_handle = _RunHandle()
        run_handle.edit_status = job.edit_status
        state.run_handle = run_handle
        await job.edit_status("⏳ Agent running…", view=job.running_view)
        cursor_key = self.config.db.resolve_secret("CURSOR_API_KEY")
        if cursor_key:
            os.environ["CURSOR_API_KEY"] = cursor_key

        profile = self.agents.get(job.agent_id)
        run_handle.display_name = profile.display_name
        sess = self.store.get(job.session_key)
        workspace = job.workspace_override or profile.workspace
        backend_name = self.resolved_backend(job.agent_id, job.session_key).backend
        trust = resolve_trust(sess.trust_mode, backend=backend_name)
        resolved = resolve_model(self.config.db, job.session_key, backend_name)
        full_prompt = build_prompt(
            agent_id=profile.id,
            display_name=profile.display_name,
            backend=backend_name,
            workspace=workspace,
            system_prompt=profile.system_prompt(self.config.project_root),
            skill_paths=profile.skills,
            user_message=job.user_prompt,
            model=resolved.model,
            project_root=self.config.project_root,
        )

        if trust.mode == "approve" and backend_name == "cursor-sdk":
            self.approvals.bind_job(
                session_key=job.session_key,
                workspace=workspace,
                edit_status=job.edit_status,
                display_name=profile.display_name,
            )

        async with self._global_sem:
            progress = (
                ThrottledProgress(job.edit_status)
                if self.config.streaming_enabled
                else None
            )
            run_handle.progress = progress

            async def on_progress(text: str) -> None:
                self.approvals.update_progress(job.session_key, text)
                if progress is not None:
                    await progress.push(text)

            history = (
                self.config.db.list_chat_turns(job.session_key)
                if backend_name == "openrouter"
                else None
            )
            try:
                result = await self.backend_for(job.agent_id, job.session_key).run(
                    prompt=full_prompt,
                    workspace=workspace,
                    session_id=sess.session_id,
                    on_progress=on_progress if progress else None,
                    cancel_event=run_handle.cancel_event,
                    register_proc=run_handle.register_proc,
                    model=resolved.model,
                    history=history,
                    approval_mode=trust.mode,
                )
            except Exception as exc:
                log.exception("Agent run failed for %s", job.agent_id)
                existing = ""
                if progress and progress.latest.strip():
                    existing = progress.latest
                failed = JobResult(
                    text=str(exc),
                    exit_code=1,
                    session_id=sess.session_id,
                    error=str(exc),
                )
                await self._edit_final(
                    job,
                    state,
                    failed,
                    _status_with_footer(
                        existing=existing,
                        emoji="❌",
                        title="Interrupted",
                        display_name=profile.display_name,
                        reason=_friendly_failure_reason(exc),
                    ),
                )
                return failed
            finally:
                if progress:
                    await progress.flush()
                if trust.mode == "approve" and backend_name == "cursor-sdk":
                    self.approvals.unbind_job(job.session_key)

        if result.error == "cancelled":
            # Prefer last streamed Discord/Telegram status over backend stub text.
            existing = ""
            if progress and progress.latest.strip():
                existing = progress.latest
            else:
                existing = (result.text or "").strip()
            await self._edit_final(
                job,
                state,
                result,
                _cancelled_status_message(
                    existing=existing,
                    display_name=profile.display_name,
                    reason=run_handle.cancel_reason,
                ),
            )
            if result.session_id:
                title = sess.title or title_from_prompt(job.user_prompt)
                self.store.set(
                    job.session_key,
                    ThreadSession(session_id=result.session_id, title=title),
                )
            return JobResult(
                text=result.text,
                exit_code=result.exit_code,
                session_id=result.session_id,
                error=result.error,
            )

        interrupt_reason = _interrupt_reason(result.error)
        if interrupt_reason:
            existing = ""
            if progress and progress.latest.strip():
                existing = progress.latest
            elif (result.text or "").strip() not in {
                "Agent run timed out.",
                "Claude Code run timed out.",
                "(interrupted)",
            }:
                existing = (result.text or "").strip()
            await self._edit_final(
                job,
                state,
                result,
                _status_with_footer(
                    existing=existing,
                    emoji="⚠️",
                    title="Interrupted",
                    display_name=profile.display_name,
                    reason=interrupt_reason,
                ),
            )
            if result.session_id:
                title = sess.title or title_from_prompt(job.user_prompt)
                self.store.set(
                    job.session_key,
                    ThreadSession(session_id=result.session_id, title=title),
                )
            return JobResult(
                text=result.text,
                exit_code=result.exit_code,
                session_id=result.session_id,
                error=result.error,
            )

        prefix = "✅" if result.exit_code == 0 else "⚠️"
        header = f"{prefix} **{profile.display_name}**"
        if result.session_id:
            header += f" · session `{result.session_id[:8]}…`"

        body = result.text
        if result.error and result.exit_code != 0:
            body += f"\n\n```\n{result.error[:1500]}\n```"

        display = body[:8000] if len(body) > 8000 else body
        # Finish on the same status message that streamed progress (Discord ≤2k).
        # Overflow only → send(), which transports attach to that status.
        status_limit = 1900
        combined = f"{header}\n\n{display}" if display.strip() else header
        if len(combined) <= status_limit:
            await self._edit_final(job, state, result, combined)
        else:
            await self._edit_final(job, state, result, header)
            await job.send(display)
            if len(body) > 8000:
                await job.send(f"_(truncated — full output was {len(body)} chars)_")

        title = sess.title or title_from_prompt(job.user_prompt)
        self.store.set(
            job.session_key,
            ThreadSession(session_id=result.session_id, title=title),
        )
        if backend_name == "openrouter" and result.exit_code == 0 and not result.error:
            self.config.db.append_chat_turn(job.session_key, "user", job.user_prompt)
            self.config.db.append_chat_turn(
                job.session_key, "assistant", result.text or ""
            )
        return JobResult(
            text=body,
            exit_code=result.exit_code,
            session_id=result.session_id,
            error=result.error,
        )

    def clear_session(self, session_key: str) -> None:
        self.store.clear(session_key)

    def reset_session_resume(self, session_key: str) -> None:
        self.store.reset_resume(session_key)

    def get_session(self, session_key: str) -> ThreadSession:
        return self.store.get(session_key)

    def set_session_title(self, session_key: str, title: str) -> None:
        sess = self.store.get(session_key)
        self.store.set(session_key, ThreadSession(session_id=sess.session_id, title=title))

    def _cursor_bin(self) -> str:
        backend = self.backends.get("cursor-cli")
        cfg = getattr(backend, "config", None)
        return str(getattr(cfg, "command", None) or "agent")

    async def cursor_models(self, *, force: bool = False) -> list[tuple[str, str]]:
        """Cached `agent models` list: (id, label)."""
        now = time.monotonic()
        if (
            not force
            and self._cursor_models_cache is not None
            and now - self._cursor_models_cache[0] < 120
        ):
            return self._cursor_models_cache[1]

        cmd = self._cursor_bin()
        rows: list[tuple[str, str]] = []
        try:
            proc = await asyncio.create_subprocess_exec(
                cmd,
                "models",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            text = stdout.decode("utf-8", errors="replace")
            if proc.returncode in (0, None):
                rows = parse_agent_models_output(text)
        except (FileNotFoundError, asyncio.TimeoutError, OSError):
            rows = []
        self._cursor_models_cache = (now, rows)
        return rows

    async def apply_model_command(
        self,
        *,
        session_key: str,
        agent_id: str,
        raw: str | None,
        include_catalog: bool = False,
        catalog_max_chars: int = 1400,
    ) -> str:
        backend_name = self.resolved_backend(agent_id, session_key).backend
        try:
            action, value = parse_model_arg(raw)
        except ValueError as exc:
            return f"⚠️ {exc}"
        if action == "set":
            self.store.set_model(session_key, value)
        elif action == "clear":
            self.store.set_model(session_key, None)
        resolved = resolve_model(self.config.db, session_key, backend_name)
        text = format_model_status(resolved)
        if backend_name not in ("cursor-cli", "cursor-sdk"):
            return text
        models = await self.cursor_models()
        if action == "set" and value and models and not model_in_catalog(value, models):
            text += (
                f"\n⚠️ `{value}` is not in `agent models` — CLI may reject it. "
                "Use `/model` with no args to see IDs."
            )
        if include_catalog:
            text += "\n\n" + format_cursor_catalog(
                models,
                current=resolved.model,
                max_chars=catalog_max_chars,
            )
        return text

    def apply_backend_command(
        self,
        *,
        session_key: str,
        agent_id: str,
        raw: str | None,
        include_catalog: bool = False,
    ) -> str:
        known = self.known_backends()
        before = self.resolved_backend(agent_id, session_key)
        try:
            action, value = parse_backend_arg(raw, known=known)
        except ValueError as exc:
            return f"⚠️ {exc}"
        if action == "set":
            self.store.set_backend(session_key, value)
        elif action == "clear":
            self.store.set_backend(session_key, None)
        after = self.resolved_backend(agent_id, session_key)
        resume_note = ""
        if after.backend != before.backend:
            self.store.reset_resume(session_key)
            resume_note = (
                "\nResume / chat history cleared — session ids don’t transfer "
                "across backends. Next message starts a new session."
            )
        model = resolve_model(self.config.db, session_key, after.backend)
        text = (
            format_backend_status(after)
            + resume_note
            + "\n\n"
            + format_model_status(model)
        )
        if include_catalog:
            text += "\n\n" + format_backend_catalog(known, current=after.backend)
        return text

    def apply_trust_command(
        self,
        *,
        session_key: str,
        agent_id: str,
        raw: str | None,
    ) -> str:
        backend_name = self.resolved_backend(agent_id, session_key).backend
        try:
            action, value = parse_trust_arg(raw)
        except ValueError as exc:
            return f"⚠️ {exc}"
        if action == "set":
            self.store.set_trust_mode(session_key, value)
        elif action == "clear":
            self.store.set_trust_mode(session_key, None)
        sess = self.store.get(session_key)
        resolved = resolve_trust(sess.trust_mode, backend=backend_name)
        return format_trust_status(resolved, backend=backend_name)

    def request_rebuild(self, *, reason: str = "") -> Path:
        """Ask the host systemd watcher to rebuild this stack (data/REQUEST_REBUILD)."""
        return write_rebuild_flag(self.config.data_dir, reason=reason)

    def rebuild_pending(self) -> bool:
        return rebuild_pending(self.config.data_dir)

    def _record_error(self, *, session_key: str, agent_id: str, error: str) -> None:
        self._last_errors.appendleft(
            _ErrorRecord(
                at=time.time(),
                session_key=session_key,
                agent_id=agent_id,
                error=error[:500],
            )
        )

    async def cursor_agent_status(self, *, force: bool = False) -> str:
        """Best-effort `agent status` (cached ~30s)."""
        now = time.monotonic()
        if (
            not force
            and self._agent_status_cache is not None
            and now - self._agent_status_cache[0] < 30
        ):
            return self._agent_status_cache[1]

        cmd = self._cursor_bin()

        try:
            proc = await asyncio.create_subprocess_exec(
                cmd,
                "status",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=8)
            text = stdout.decode("utf-8", errors="replace").strip() or "(empty)"
            if proc.returncode not in (0, None):
                text = f"(exit {proc.returncode}) {text}"
        except FileNotFoundError:
            text = f"Binary not found: {cmd}"
        except asyncio.TimeoutError:
            text = "Timed out running `agent status`"
        except Exception as exc:
            text = f"Error: {exc}"

        self._agent_status_cache = (now, text)
        return text

    def live_status(self) -> dict[str, Any]:
        running: list[dict[str, Any]] = []
        queued: list[dict[str, Any]] = []
        for key, state in self._sessions.items():
            qsize = queued_count(state.pending, stop=_STOP)
            if state.busy:
                started = state.started_at or time.time()
                running.append(
                    {
                        "session_key": key,
                        "agent_id": state.current_agent_id or "?",
                        "started_at": started,
                        "elapsed_sec": round(time.time() - started, 1),
                        "prompt_preview": title_from_prompt(state.current_prompt or ""),
                        "schedule_id": state.current_schedule_id,
                        "queue_behind": qsize,
                        "pid": (
                            state.run_handle.proc.pid
                            if state.run_handle and state.run_handle.proc
                            else None
                        ),
                    }
                )
            elif qsize > 0:
                queued.append({"session_key": key, "queued": qsize})

        errors = [
            {
                "at": e.at,
                "session_key": e.session_key,
                "agent_id": e.agent_id,
                "error": e.error,
            }
            for e in self._last_errors
        ]
        return {
            "shutting_down": self._shutting_down,
            "rebuild_pending": self.rebuild_pending(),
            "max_concurrent_jobs": self.config.max_concurrent_jobs,
            "running_count": len(running),
            "queued_threads": len(queued),
            "running": running,
            "queued": queued,
            "last_errors": errors,
            "backends": sorted(self.backends.keys()),
            "streaming_enabled": self.config.streaming_enabled,
        }
