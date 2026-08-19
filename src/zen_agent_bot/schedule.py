"""Cron expression helpers and schedule list formatting."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from croniter import croniter

DEFAULT_TZ = "Asia/Singapore"
MAX_NAME = 80
MAX_PROMPT = 8000
MAX_CRON = 64


def validate_cron(expr: str) -> str:
    text = " ".join(expr.strip().split())
    if not text:
        raise ValueError("Cron expression is empty")
    if len(text) > MAX_CRON:
        raise ValueError("Cron expression is too long")
    if len(text.split()) != 5:
        raise ValueError("Use a 5-field cron: min hour day month weekday (e.g. 0 9 * * *)")
    try:
        croniter(text)
    except (ValueError, KeyError) as exc:
        raise ValueError(f"Invalid cron: {exc}") from exc
    return text


def validate_timezone(name: str) -> str:
    text = (name or DEFAULT_TZ).strip() or DEFAULT_TZ
    try:
        ZoneInfo(text)
    except Exception as exc:
        raise ValueError(f"Unknown timezone {text!r}") from exc
    return text


def slug_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32]
    return slug or uuid.uuid4().hex[:8]


def next_run_utc(
    cron_expr: str,
    tz_name: str,
    *,
    after: datetime | None = None,
) -> datetime:
    tz = ZoneInfo(tz_name)
    now = after or datetime.now(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        now = now.astimezone(tz)
    nxt = croniter(cron_expr, now).get_next(datetime)
    if nxt.tzinfo is None:
        nxt = nxt.replace(tzinfo=tz)
    return nxt.astimezone(timezone.utc)


def next_run_iso(cron_expr: str, tz_name: str, *, after: datetime | None = None) -> str:
    return next_run_utc(cron_expr, tz_name, after=after).isoformat()


def format_schedules_markdown(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No schedules. Add one in admin **Schedules**."
    lines = [f"**Schedules** ({len(rows)}):"]
    for row in rows:
        flag = "on" if row.get("enabled") else "off"
        status = row.get("last_status") or "—"
        nxt = (row.get("next_run_at") or "—")[:16].replace("T", " ")
        lines.append(
            f"• `{row['id']}` **{row['name']}** · `{row['cron_expr']}` "
            f"{row.get('timezone') or DEFAULT_TZ} · {row['agent_id']} · "
            f"{flag} · {status} · next {nxt}"
        )
    lines.append(
        "_Admin → Schedules to add/edit. Each schedule reuses one Discord thread "
        "(new thread only if the last one is gone). `/new` in that thread resets resume._"
    )
    return "\n".join(lines)
