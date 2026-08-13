"""Poll SQLite schedules and fire each due run as a new Discord thread."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from .schedule import next_run_iso

if TYPE_CHECKING:
    from .gateway.router import Gateway, JobResult

log = logging.getLogger(__name__)

CronLaunchFn = Callable[..., Awaitable[dict[str, str | None]]]


class CronScheduler:
    def __init__(self, gateway: Gateway) -> None:
        self.gateway = gateway
        self._tick_sec = 15.0

    async def run(self, shutdown: asyncio.Event) -> None:
        n = self.gateway.config.db.reset_stuck_schedules()
        if n:
            log.warning("Reset %d schedule(s) stuck in running after restart", n)
        log.info("Cron scheduler started")
        while not shutdown.is_set():
            try:
                await self.tick()
            except Exception:
                log.exception("Cron tick failed")
            try:
                await asyncio.wait_for(shutdown.wait(), timeout=self._tick_sec)
            except TimeoutError:
                continue
        log.info("Cron scheduler stopped")

    async def tick(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        due = self.gateway.config.db.due_schedules(now)
        for row in due:
            await self.fire(str(row["id"]), force=False)

    async def fire(self, schedule_id: str, *, force: bool = False) -> str:
        """Start one run. Returns 'started', 'skipped', or an error string."""
        db = self.gateway.config.db
        row = db.get_schedule(schedule_id)
        if row is None:
            return "missing"
        if row.get("last_status") == "running":
            return "already-running"
        if not row["enabled"] and not force:
            return "disabled"
        if self.gateway._shutting_down:
            return "shutting-down"

        launcher = self.gateway.cron_launcher(str(row["agent_id"]))
        if launcher is None:
            db.mark_schedule_done(
                schedule_id, ok=False, error="No Discord bot for this agent"
            )
            return "no-discord"

        nxt = next_run_iso(str(row["cron_expr"]), str(row["timezone"]))
        try:
            launched = await launcher(
                agent_id=str(row["agent_id"]),
                schedule_id=schedule_id,
                name=str(row["name"]),
                prompt=str(row["prompt"]),
                cron_expr=str(row["cron_expr"]),
            )
        except Exception as exc:
            log.exception("Failed to launch schedule %s", schedule_id)
            db.mark_schedule_done(schedule_id, ok=False, error=str(exc))
            db.set_schedule_next_run(schedule_id, nxt)
            return f"error:{exc}"

        db.mark_schedule_running(
            schedule_id,
            next_run_at=nxt,
            thread_id=launched.get("thread_id"),
            session_key=launched.get("session_key"),
            thread_url=launched.get("thread_url"),
        )
        return "started"

    def on_job_done(self, schedule_id: str, result: JobResult) -> None:
        ok = result.exit_code == 0 and not result.error
        err = result.error if not ok else None
        self.gateway.config.db.mark_schedule_done(schedule_id, ok=ok, error=err)


def cron_prompt(*, name: str, cron_expr: str, prompt: str) -> str:
    return (
        f"[Scheduled task `{name}` · `{cron_expr}` — new thread this run]\n\n"
        f"{prompt.strip()}"
    )
