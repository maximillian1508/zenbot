from __future__ import annotations

import asyncio
import json
import shutil
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from ..util.proc import terminate_process
from .base import (
    STREAM_STDOUT_LIMIT,
    AgentRunResult,
    ProgressCallback,
    RegisterProc,
    is_stream_line_too_large,
)


@dataclass(frozen=True)
class CursorCliConfig:
    command: str = "agent"
    force: bool = True
    model: str | None = None
    timeout_sec: int = 3600


def _format_progress(*, phase: str, text: str, max_len: int = 1800) -> str:
    icons = {"thinking": "💭", "writing": "✍️", "running": "⏳", "tool": "🔧"}
    header = f"{icons.get(phase, '⏳')} **Agent running…**"
    if not text:
        return header
    preview = text[-max_len:] if len(text) > max_len else text
    return f"{header}\n\n{preview}"


class CursorCliBackend:
    def __init__(self, config: CursorCliConfig) -> None:
        self.config = config

    def _resolve_bin(self) -> str:
        path = shutil.which(self.config.command)
        if not path:
            raise FileNotFoundError(f"Agent binary not found: {self.config.command}")
        return path

    def _base_cmd(
        self,
        binary: str,
        workspace: Path,
        session_id: str | None,
        model: str | None,
    ) -> list[str]:
        cmd: list[str] = [
            binary,
            "-p",
            "--workspace",
            str(workspace),
        ]
        if self.config.force:
            cmd.append("--force")
        if model:
            cmd.extend(["--model", model])
        if session_id:
            cmd.extend(["--resume", session_id])
        return cmd

    def _cancelled(
        self,
        *,
        session_id: str | None,
        cancel_event: asyncio.Event | None,
    ) -> AgentRunResult | None:
        if cancel_event and cancel_event.is_set():
            return AgentRunResult(
                text="Cancelled.",
                session_id=session_id,
                exit_code=130,
                error="cancelled",
            )
        return None

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
    ) -> AgentRunResult:
        binary = self._resolve_bin()
        if on_progress is not None:
            return await self._run_streaming(
                binary=binary,
                prompt=prompt,
                workspace=workspace,
                session_id=session_id,
                on_progress=on_progress,
                cancel_event=cancel_event,
                register_proc=register_proc,
                model=model,
            )
        return await self._run_json(
            binary=binary,
            prompt=prompt,
            workspace=workspace,
            session_id=session_id,
            cancel_event=cancel_event,
            register_proc=register_proc,
            model=model,
        )

    async def _run_json(
        self,
        *,
        binary: str,
        prompt: str,
        workspace: Path,
        session_id: str | None,
        cancel_event: asyncio.Event | None = None,
        register_proc: RegisterProc | None = None,
        model: str | None = None,
    ) -> AgentRunResult:
        cmd = self._base_cmd(binary, workspace, session_id, model)
        cmd.extend(["--output-format", "json", prompt])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(workspace),
            limit=STREAM_STDOUT_LIMIT,
        )
        if register_proc:
            register_proc(proc)
        try:
            comm_task = asyncio.create_task(proc.communicate())
            if cancel_event:
                cancel_wait = asyncio.create_task(cancel_event.wait())
                done, pending = await asyncio.wait(
                    {comm_task, cancel_wait},
                    return_when=asyncio.FIRST_COMPLETED,
                    timeout=self.config.timeout_sec,
                )
                if cancel_wait in done:
                    comm_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await comm_task
                    await terminate_process(proc)
                    return AgentRunResult(
                        text="Cancelled.",
                        session_id=session_id,
                        exit_code=130,
                        error="cancelled",
                    )
                if comm_task not in done:
                    comm_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await comm_task
                    await terminate_process(proc)
                    return AgentRunResult(
                        text="Agent run timed out.",
                        session_id=session_id,
                        exit_code=124,
                        error="timeout",
                    )
                for task in pending:
                    task.cancel()
                stdout, stderr = comm_task.result()
            else:
                stdout, stderr = await asyncio.wait_for(
                    comm_task,
                    timeout=self.config.timeout_sec,
                )
        except asyncio.TimeoutError:
            await terminate_process(proc)
            return AgentRunResult(
                text="Agent run timed out.",
                session_id=session_id,
                exit_code=124,
                error="timeout",
            )

        cancelled = self._cancelled(session_id=session_id, cancel_event=cancel_event)
        if cancelled:
            return cancelled

        return self._parse_json_output(
            stdout=stdout.decode("utf-8", errors="replace").strip(),
            stderr=stderr.decode("utf-8", errors="replace").strip(),
            session_id=session_id,
            exit_code=proc.returncode or 0,
        )

    async def _run_streaming(
        self,
        *,
        binary: str,
        prompt: str,
        workspace: Path,
        session_id: str | None,
        on_progress: ProgressCallback,
        cancel_event: asyncio.Event | None = None,
        register_proc: RegisterProc | None = None,
        model: str | None = None,
    ) -> AgentRunResult:
        cmd = self._base_cmd(binary, workspace, session_id, model)
        cmd.extend(
            [
                "--output-format",
                "stream-json",
                "--stream-partial-output",
                prompt,
            ]
        )

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(workspace),
            limit=STREAM_STDOUT_LIMIT,
        )
        if register_proc:
            register_proc(proc)
        assert proc.stdout is not None
        assert proc.stderr is not None

        assistant_text = ""
        phase = "running"
        new_session = session_id
        exit_code = 0
        result_text = ""

        async def drain_stderr() -> str:
            data = await proc.stderr.read()
            return data.decode("utf-8", errors="replace").strip()

        stderr_task = asyncio.create_task(drain_stderr())

        try:
            while True:
                if cancel_event and cancel_event.is_set():
                    await terminate_process(proc)
                    return AgentRunResult(
                        text="Cancelled.",
                        session_id=new_session,
                        exit_code=130,
                        error="cancelled",
                    )

                read_task = asyncio.create_task(proc.stdout.readline())
                wait_set: set[asyncio.Task] = {read_task}
                if cancel_event:
                    wait_set.add(asyncio.create_task(cancel_event.wait()))
                done, pending = await asyncio.wait(
                    wait_set,
                    return_when=asyncio.FIRST_COMPLETED,
                    timeout=self.config.timeout_sec,
                )
                for task in pending:
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task

                if cancel_event and cancel_event.is_set():
                    read_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await read_task
                    await terminate_process(proc)
                    return AgentRunResult(
                        text="Cancelled.",
                        session_id=new_session,
                        exit_code=130,
                        error="cancelled",
                    )

                if read_task not in done:
                    read_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await read_task
                    await terminate_process(proc)
                    return AgentRunResult(
                        text="Agent run timed out.",
                        session_id=new_session,
                        exit_code=124,
                        error="timeout",
                    )

                try:
                    line = read_task.result()
                except (asyncio.LimitOverrunError, ValueError) as exc:
                    if not is_stream_line_too_large(exc):
                        raise
                    await terminate_process(proc)
                    return AgentRunResult(
                        text=assistant_text or result_text or "(interrupted)",
                        session_id=new_session,
                        exit_code=1,
                        error="stream_line_too_large",
                    )
                if not line:
                    break

                raw = line.decode("utf-8", errors="replace").strip()
                if not raw:
                    continue

                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                etype = event.get("type")
                if etype == "system" and event.get("subtype") == "init":
                    new_session = event.get("session_id") or new_session
                elif etype == "thinking":
                    phase = "thinking"
                    await on_progress(_format_progress(phase=phase, text=assistant_text))
                elif etype == "assistant":
                    merged = _apply_assistant_event(assistant_text, event)
                    if merged is not None:
                        assistant_text = merged
                        phase = "writing"
                        await on_progress(_format_progress(phase=phase, text=assistant_text))
                elif etype in ("tool_call", "tool", "function_call"):
                    phase = "tool"
                    await on_progress(_format_progress(phase=phase, text=assistant_text))
                elif etype == "result":
                    new_session = event.get("session_id") or new_session
                    result_text = str(event.get("result") or "").strip()
                    if event.get("is_error"):
                        exit_code = 1
                    if not result_text and event.get("error"):
                        result_text = str(event.get("error"))

            await proc.wait()
            exit_code = exit_code or (proc.returncode or 0)
        finally:
            if proc.returncode is None:
                await terminate_process(proc)

        cancelled = self._cancelled(session_id=new_session, cancel_event=cancel_event)
        if cancelled:
            return cancelled

        err = ""
        if not stderr_task.done():
            err = await stderr_task
        else:
            err = stderr_task.result()

        text = result_text or assistant_text
        if not text and err:
            text = err[:4000]
        if not text:
            text = "(no output)"

        return AgentRunResult(
            text=text,
            session_id=new_session,
            exit_code=exit_code,
            error=err[:2000] if exit_code != 0 and err else None,
        )

    def _parse_json_output(
        self,
        *,
        stdout: str,
        stderr: str,
        session_id: str | None,
        exit_code: int,
    ) -> AgentRunResult:
        text = ""
        new_session = session_id
        if stdout:
            try:
                payload = json.loads(stdout)
                text = str(payload.get("result") or "").strip()
                new_session = payload.get("session_id") or new_session
                if payload.get("is_error"):
                    exit_code = exit_code or 1
                    if not text:
                        text = str(payload.get("error") or "Agent returned an error.")
            except json.JSONDecodeError:
                text = stdout

        if not text and stderr:
            text = stderr[:4000]
        if not text:
            text = "(no output)"

        return AgentRunResult(
            text=text,
            session_id=new_session,
            exit_code=exit_code,
            error=stderr[:2000] if exit_code != 0 and stderr else None,
        )


# Thought-sized flushes (not token deltas) that Cursor sometimes re-emits
# with the same timestamp_ms / no model_call_id shape as real deltas.
_DUP_FLUSH_MIN_CHARS = 24


def _is_streaming_delta(event: dict) -> bool:
    """True only for real-time --stream-partial-output deltas (not duplicate flushes)."""
    return bool(event.get("timestamp_ms")) and not event.get("model_call_id")


def _merge_assistant_delta(current: str, chunk: str) -> str:
    """Append a stream delta; drop last-thought replays and cumulative snapshots."""
    if not chunk:
        return current
    if not current:
        return chunk
    if len(chunk) >= _DUP_FLUSH_MIN_CHARS and current.endswith(chunk):
        return current
    if len(chunk) > len(current) and chunk.startswith(current):
        return chunk
    return current + chunk


def _apply_assistant_event(current: str, event: dict) -> str | None:
    """Return updated text, or None when the event is a duplicate / non-delta."""
    chunk = _assistant_text(event)
    if not chunk or not _is_streaming_delta(event):
        return None
    merged = _merge_assistant_delta(current, chunk)
    if merged == current:
        return None
    return merged


def _assistant_text(event: dict) -> str:
    message = event.get("message") or {}
    parts = message.get("content") or []
    chunks: list[str] = []
    for part in parts:
        if part.get("type") == "text":
            chunks.append(str(part.get("text") or ""))
    return "".join(chunks)
