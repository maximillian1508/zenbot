"""Resolve agent backend: thread override → agent profile default."""

from __future__ import annotations

from dataclasses import dataclass

from .model_select import CLEAR_TOKENS, SHOW_TOKENS, blank_to_none
from .store import ConfigStore

BACKEND_ALIASES = {
    "cursor": "cursor-cli",
    "cursor-cli": "cursor-cli",
    "agent": "cursor-cli",
    "cli": "cursor-cli",
    "sdk": "cursor-sdk",
    "cursor-sdk": "cursor-sdk",
    "cursor_sdk": "cursor-sdk",
    "claude": "claude-cli",
    "claude-cli": "claude-cli",
    "claude-code": "claude-cli",
    "openrouter": "openrouter",
    "or": "openrouter",
    "open-router": "openrouter",
}


@dataclass(frozen=True)
class ResolvedBackend:
    backend: str
    source: str  # thread | profile


def canonicalize_backend(raw: str) -> str | None:
    return BACKEND_ALIASES.get(raw.strip().lower())


def resolve_backend(
    db: ConfigStore,
    session_key: str,
    profile_default: str,
    *,
    known: set[str] | frozenset[str] | None = None,
) -> ResolvedBackend:
    default = canonicalize_backend(profile_default) or profile_default.strip() or "cursor-cli"
    row = db.get_session(session_key)
    thread = blank_to_none(row.get("backend"))
    if thread:
        canonical = canonicalize_backend(thread) or thread
        if known is None or canonical in known:
            return ResolvedBackend(backend=canonical, source="thread")
    return ResolvedBackend(backend=default, source="profile")


def parse_backend_arg(
    raw: str | None,
    *,
    known: set[str] | frozenset[str],
) -> tuple[str, str | None]:
    """Return ('show', None), ('clear', None), or ('set', canonical_id)."""
    if raw is None:
        return "show", None
    text = raw.strip()
    if not text:
        return "show", None
    lower = text.lower()
    if lower in CLEAR_TOKENS:
        return "clear", None
    if lower in SHOW_TOKENS:
        return "show", None
    if "\n" in text or "\r" in text:
        raise ValueError("Backend id must be a single line")
    canonical = canonicalize_backend(text)
    if canonical is None or canonical not in known:
        names = ", ".join(f"`{n}`" for n in sorted(known))
        raise ValueError(f"Unknown backend `{text}`. Use {names} (or clear).")
    return "set", canonical


def format_backend_status(resolved: ResolvedBackend) -> str:
    source_note = {
        "thread": "this thread",
        "profile": "agent default",
    }.get(resolved.source, resolved.source)
    lines = [
        f"**Backend:** `{resolved.backend}` · {source_note}",
    ]
    if resolved.source == "thread":
        lines.append("Use `/backend clear` to drop the thread override.")
    else:
        lines.append("Use `/backend <id>` to override this thread (next job).")
    return "\n".join(lines)


def format_backend_catalog(
    known: set[str] | frozenset[str],
    *,
    current: str | None = None,
) -> str:
    lines = ["**Backends:**"]
    for name in sorted(known):
        mark = " ←" if current and name == current else ""
        lines.append(f"• `{name}`{mark}")
    return "\n".join(lines)


