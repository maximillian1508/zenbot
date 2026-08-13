"""Format a Discord thread transcript for /handoff into another agent."""

from __future__ import annotations


class HandoffError(RuntimeError):
    """User-facing handoff failure (missing channel, not a thread, …)."""


HANDOFF_MAX_MESSAGES = 25
HANDOFF_MAX_CHARS = 6000
HANDOFF_LINE_CHARS = 800


def clip_line(text: str, limit: int = HANDOFF_LINE_CHARS) -> str:
    body = " ".join((text or "").split())
    if len(body) <= limit:
        return body
    return body[: limit - 1] + "…"


def format_transcript_lines(
    rows: list[tuple[str, str]],
    *,
    max_chars: int = HANDOFF_MAX_CHARS,
) -> str:
    """Oldest → newest `author: text`. Drops oldest rows if over budget."""
    rendered = [f"{clip_line(name, 80)}: {clip_line(text)}" for name, text in rows if clip_line(text)]
    while rendered and sum(len(line) + 1 for line in rendered) > max_chars:
        rendered.pop(0)
    return "\n".join(rendered)


def format_handoff_prompt(
    *,
    source_agent: str,
    source_title: str,
    source_url: str,
    target_display: str,
    note: str,
    transcript: str,
) -> str:
    title = source_title.strip() or "(untitled)"
    lines = [
        f"[Handoff from `{source_agent}` thread **{title}**]",
        f"Source: {source_url}" if source_url else "Source: (no jump link)",
    ]
    extra = note.strip()
    if extra:
        lines.append(f"User note: {extra}")
    else:
        lines.append("Continue from the latest user question in the transcript.")
    lines.append("")
    lines.append("--- transcript (oldest → newest) ---")
    lines.append(transcript.strip() or "(empty thread)")
    lines.append("---")
    lines.append(
        f"You are **{target_display}**. Use the transcript as context. "
        "Do not ask the user to paste it again."
    )
    return "\n".join(lines)
