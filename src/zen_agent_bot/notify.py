"""Job-done chat ping helpers."""

from __future__ import annotations


def format_duration(seconds: float) -> str:
    sec = max(0, int(seconds))
    if sec < 60:
        return f"{sec}s"
    minutes, sec = divmod(sec, 60)
    if minutes < 60:
        return f"{minutes}m {sec:02d}s" if sec else f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def append_status_line(status: str, line: str, *, limit: int = 1900) -> str:
    """Append a one-line footer to an existing status bubble (Discord ≤2k)."""
    line = line.strip()
    if not line:
        return (status or "")[:limit]
    footer = f"\n\n{line}"
    body = (status or "").rstrip()
    if not body:
        return line[:limit]
    if len(body) + len(footer) <= limit:
        return body + footer
    room = limit - len(footer) - 12
    if room < 40:
        return line[:limit]
    return body[:room].rstrip() + "\n\n_(…)_" + footer


def format_job_done_ping(
    *,
    mention: str | None,
    exit_code: int,
    error: str | None,
    elapsed_sec: float,
) -> str:
    dur = format_duration(elapsed_sec)
    who = f" {mention}" if mention and mention.strip() else ""
    if error == "cancelled":
        return f"🛑 Cancelled{who} · {dur}"
    if exit_code == 0:
        return f"✅ Done{who} · {dur}"
    return f"⚠️ Finished with errors{who} · {dur}"


def format_close_reply(
    *,
    cancelled: bool,
    dropped: int,
    archived: bool = False,
) -> str:
    lines = ["Session closed."]
    if cancelled:
        lines.append("Stopped the running job.")
    if dropped:
        plural = "s" if dropped != 1 else ""
        lines.append(f"Dropped {dropped} queued message{plural}.")
    if archived:
        lines.append(
            "Discord thread archived. Send a message to unarchive and continue "
            "(`--resume` / OpenRouter history kept). `/new` drops resume + chat turns; "
            "admin **Clear** forgets the mapping."
        )
    else:
        lines.append(
            "Next message continues the same session (`--resume` / OpenRouter history kept). "
            "`/new` drops resume + chat turns; admin **Clear** forgets the mapping."
        )
    return "\n".join(lines)


MIN_SUCCESS_PING_SEC = 60


def should_ping_done(
    *,
    error: str | None,
    cancel_reason: str,
    elapsed_sec: float = 0,
    enabled: bool = True,
    min_success_sec: float = MIN_SUCCESS_PING_SEC,
) -> bool:
    """Ping long successes and real failures. Skip short OK runs and Send now /close."""
    if not enabled:
        return False
    if error == "cancelled":
        reason = cancel_reason.lower()
        if "send now" in reason or "/close" in reason:
            return False
        return elapsed_sec >= min_success_sec
    if error:
        return True
    return elapsed_sec >= min_success_sec
