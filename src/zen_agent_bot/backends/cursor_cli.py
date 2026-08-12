from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from .base import AgentRunResult, ProgressCallback


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

    def _base_cmd(self, binary: str, workspace: Path, session_id: str | None) -> list[str]:
        cmd: list[str] = [
            binary,
            "-p",
            "--workspace",
            str(workspace),
        ]
        if self.config.force:
            cmd.append("--force")
        if self.config.model:
            cmd.extend(["--model", self.config.model])
        if session_id:
            cmd.extend(["--resume", session_id])
        return cmd

    async def run(
        self,
        *,
        prompt: str,
        workspace: Path,
        session_id: str | None,
        on_progress: ProgressCallback | None = None,
    ) -> AgentRunResult:
        binary = self._resolve_bin()
        if on_progress is not None:
            return await self._run_streaming(
                binary=binary,
                prompt=prompt,
                workspace=workspace,
                session_id=session_id,
                on_progress=on_progress,
            )
        return await self._run_json(
            binary=binary,
            prompt=prompt,
            workspace=workspace,
            session_id=session_id,
        )

    async def _run_json(
        self,
        *,
        binary: str,
        prompt: str,
        workspace: Path,
        session_id: str | None,
    ) -> AgentRunResult:
        cmd = self._base_cmd(binary, workspace, session_id)
        cmd.extend(["--output-format", "json", prompt])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(workspace),
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.config.timeout_sec,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return AgentRunResult(
                text="Agent run timed out.",
                session_id=session_id,
                exit_code=124,
                error="timeout",
            )

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
    ) -> AgentRunResult:
        cmd = self._base_cmd(binary, workspace, session_id)
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
        )
        assert proc.stdout is not None
        assert proc.stderr is not None

        assistant_text = ""
        phase = "running"
        new_session = session_id
        exit_code = 0
        result_text = ""
        exit_code = 0

        async def drain_stderr() -> str:
            data = await proc.stderr.read()
            return data.decode("utf-8", errors="replace").strip()

        stderr_task = asyncio.create_task(drain_stderr())

        try:
            while True:
                try:
                    line = await asyncio.wait_for(
                        proc.stdout.readline(),
                        timeout=self.config.timeout_sec,
                    )
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
                    stderr_task.cancel()
                    return AgentRunResult(
                        text="Agent run timed out.",
                        session_id=new_session,
                        exit_code=124,
                        error="timeout",
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
                    chunk = _assistant_text(event)
                    if chunk:
                        if event.get("timestamp_ms"):
                            assistant_text += chunk
                        else:
                            assistant_text = chunk
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
                proc.kill()
                await proc.wait()

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


def _assistant_text(event: dict) -> str:
    message = event.get("message") or {}
    parts = message.get("content") or []
    chunks: list[str] = []
    for part in parts:
        if part.get("type") == "text":
            chunks.append(str(part.get("text") or ""))
    return "".join(chunks)
