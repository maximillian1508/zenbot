from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..agents.registry import AgentRegistry
from ..backends.base import AgentBackend, is_stream_line_too_large
from ..config.load import GatewayConfig
from ..sessions import SessionStore, ThreadSession
from ..skills.loader import build_prompt
from ..util.proc import terminate_process
from ..util.rebuild import request_rebuild as write_rebuild_flag, rebuild_pending
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
class _RunHandle:
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    proc: asyncio.subprocess.Process | None = None
    cancel_reason: str = "stopped"

    def register_proc(self, proc: asyncio.subprocess.Process) -> None:
        self.proc = proc

    async def cancel(self, reason: str = "stopped by /cancel") -> None:
        self.cancel_reason = reason
        self.cancel_event.set()
        if self.proc is not None:
            await terminate_process(self.proc)


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
    queue: asyncio.Queue[_QueuedJob | None]
    worker: asyncio.Task[None] | None = None
    busy: bool = False
    run_handle: _RunHandle | None = None
    current_agent_id: str | None = None
    current_prompt: str | None = None
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
        if self._shutting_down:
            await edit_status("⚠️ Gateway is shutting down — try again after restart.")
            return JobResult(text="", exit_code=0, session_id=None)

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

    async def cancel_session(self, session_key: str) -> bool:
        state = self._sessions.get(session_key)
        if state is None or not state.busy or state.run_handle is None:
            return False
        await state.run_handle.cancel("stopped by /cancel")
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
                await state.queue.put(None)

        log.info("Gateway shutdown complete")

    async def _session_worker(self, session_key: str) -> None:
        state = self._sessions[session_key]
        while True:
            job = await state.queue.get()
            if job is None:
                state.queue.task_done()
                break
            if self._shutting_down:
                state.queue.task_done()
                try:
                    await job.edit_status("⚠️ Skipped — gateway shutting down.")
                except Exception:
                    log.exception("Failed to update status for %s", session_key)
                continue
            state.busy = True
            state.current_agent_id = job.agent_id
            state.current_prompt = job.user_prompt
            state.started_at = time.time()
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
                try:
                    await job.edit_status("❌ Internal error — check logs.")
                except Exception:
                    log.exception("Failed to update status for %s", session_key)
            finally:
                state.busy = False
                state.run_handle = None
                state.current_agent_id = None
                state.current_prompt = None
                state.started_at = None
                state.queue.task_done()

    async def _execute_job(self, job: _QueuedJob) -> JobResult:
        state = self._sessions[job.session_key]
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

        run_handle = _RunHandle()
        state.run_handle = run_handle

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
                    cancel_event=run_handle.cancel_event,
                    register_proc=run_handle.register_proc,
                )
            except Exception as exc:
                log.exception("Agent run failed for %s", job.agent_id)
                existing = ""
                if progress and progress.latest.strip():
                    existing = progress.latest
                await job.edit_status(
                    _status_with_footer(
                        existing=existing,
                        emoji="❌",
                        title="Interrupted",
                        display_name=profile.display_name,
                        reason=_friendly_failure_reason(exc),
                    )
                )
                return JobResult(
                    text=str(exc),
                    exit_code=1,
                    session_id=sess.session_id,
                    error=str(exc),
                )
            finally:
                if progress:
                    await progress.flush()

        if result.error == "cancelled":
            # Prefer last streamed Discord/Telegram status over backend stub text.
            existing = ""
            if progress and progress.latest.strip():
                existing = progress.latest
            else:
                existing = (result.text or "").strip()
            await job.edit_status(
                _cancelled_status_message(
                    existing=existing,
                    display_name=profile.display_name,
                    reason=run_handle.cancel_reason,
                )
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
            await job.edit_status(
                _status_with_footer(
                    existing=existing,
                    emoji="⚠️",
                    title="Interrupted",
                    display_name=profile.display_name,
                    reason=interrupt_reason,
                )
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
            await job.edit_status(combined)
        else:
            await job.edit_status(header)
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

        cmd = "agent"
        for name, backend in self.backends.items():
            cfg = getattr(backend, "config", None)
            if name == "cursor-cli" or getattr(cfg, "command", None):
                if getattr(cfg, "command", None):
                    cmd = str(cfg.command)
                break

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
            qsize = state.queue.qsize()
            if state.busy:
                started = state.started_at or time.time()
                running.append(
                    {
                        "session_key": key,
                        "agent_id": state.current_agent_id or "?",
                        "started_at": started,
                        "elapsed_sec": round(time.time() - started, 1),
                        "prompt_preview": title_from_prompt(state.current_prompt or ""),
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
