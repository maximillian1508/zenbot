from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)

EditStatus = Callable[..., Awaitable[None]]


@dataclass
class PendingApproval:
    id: str
    session_key: str
    kind: str
    summary: str
    detail: str
    created_at: float = field(default_factory=time.time)
    future: asyncio.Future[bool] = field(default=None)  # type: ignore[assignment]

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_key": self.session_key,
            "kind": self.kind,
            "summary": self.summary,
            "detail": self.detail[:1500],
            "created_at": self.created_at,
        }


@dataclass
class ApprovalJobContext:
    session_key: str
    workspace: Path
    edit_status: EditStatus
    display_name: str
    progress_text: str = ""


class ApprovalBridge:
    """In-memory pending tool approvals for Discord Accept/Deny."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._pending: dict[str, PendingApproval] = {}
        self._by_session: dict[str, str] = {}
        self._jobs: dict[str, ApprovalJobContext] = {}
        self._cwd_index: dict[str, str] = {}
        self._token = secrets.token_urlsafe(24)

    @property
    def token(self) -> str:
        return self._token

    def bind_job(
        self,
        *,
        session_key: str,
        workspace: Path,
        edit_status: EditStatus,
        display_name: str,
    ) -> None:
        cwd = str(workspace.resolve())
        self._jobs[session_key] = ApprovalJobContext(
            session_key=session_key,
            workspace=workspace.resolve(),
            edit_status=edit_status,
            display_name=display_name,
        )
        self._cwd_index[cwd] = session_key

    def unbind_job(self, session_key: str) -> None:
        job = self._jobs.pop(session_key, None)
        if job is not None:
            self._cwd_index.pop(str(job.workspace), None)
        aid = self._by_session.pop(session_key, None)
        if aid and aid in self._pending:
            pending = self._pending.pop(aid)
            if not pending.future.done():
                pending.future.set_result(False)

    def update_progress(self, session_key: str, text: str) -> None:
        job = self._jobs.get(session_key)
        if job is not None:
            job.progress_text = text

    def resolve_session_key(self, *, session_key: str | None, cwd: str | None) -> str | None:
        if session_key and session_key in self._jobs:
            return session_key
        if cwd:
            key = self._cwd_index.get(str(Path(cwd).expanduser().resolve()))
            if key:
                return key
            # fallback: exact string match if resolve failed
            return self._cwd_index.get(cwd)
        if len(self._jobs) == 1:
            return next(iter(self._jobs))
        return None

    async def request(
        self,
        *,
        session_key: str,
        kind: str,
        summary: str,
        detail: str,
        timeout_sec: float = 300.0,
        attach_view: Callable[[str], object] | None = None,
    ) -> bool:
        job = self._jobs.get(session_key)
        if job is None:
            log.warning("Approval request with no active job for %s", session_key)
            return False

        approval_id = secrets.token_hex(8)
        loop = asyncio.get_running_loop()
        pending = PendingApproval(
            id=approval_id,
            session_key=session_key,
            kind=kind,
            summary=summary.strip() or kind,
            detail=detail.strip(),
            future=loop.create_future(),
        )
        async with self._lock:
            old = self._by_session.get(session_key)
            if old and old in self._pending:
                prev = self._pending.pop(old)
                if not prev.future.done():
                    prev.future.set_result(False)
            self._pending[approval_id] = pending
            self._by_session[session_key] = approval_id

        body = (
            f"🔐 **Approval needed** · {job.display_name}\n"
            f"**{kind}:** `{pending.summary[:180]}`\n"
            f"_Accept or Deny within {int(timeout_sec)}s_"
        )
        existing = (job.progress_text or "").strip()
        status = f"{existing}\n\n———\n{body}" if existing else body
        view = attach_view(approval_id) if attach_view else None
        try:
            await job.edit_status(status[:2000], view=view)
        except Exception:
            log.exception("Failed to show approval prompt for %s", session_key)

        try:
            return await asyncio.wait_for(asyncio.shield(pending.future), timeout=timeout_sec)
        except asyncio.TimeoutError:
            self.resolve(approval_id, allow=False, reason="timeout")
            return False
        finally:
            async with self._lock:
                self._pending.pop(approval_id, None)
                if self._by_session.get(session_key) == approval_id:
                    self._by_session.pop(session_key, None)

    def resolve(self, approval_id: str, *, allow: bool, reason: str = "") -> bool:
        pending = self._pending.get(approval_id)
        if pending is None:
            return False
        if pending.future.done():
            return False
        pending.future.set_result(allow)
        log.info(
            "Approval %s %s (%s)",
            approval_id,
            "accepted" if allow else "denied",
            reason or "user",
        )
        return True

    def get(self, approval_id: str) -> PendingApproval | None:
        return self._pending.get(approval_id)

    def list_pending(self) -> list[dict[str, Any]]:
        return [p.to_public() for p in self._pending.values()]
