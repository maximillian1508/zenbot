from __future__ import annotations

from dataclasses import dataclass

TRUST_FORCE = "force"
TRUST_APPROVE = "approve"
TRUST_DEFAULT = TRUST_FORCE
TRUST_KNOWN = frozenset({TRUST_FORCE, TRUST_APPROVE})


@dataclass(frozen=True)
class ResolvedTrust:
    mode: str
    source: str


def parse_trust_arg(raw: str | None) -> tuple[str, str | None]:
    text = (raw or "").strip().lower()
    if text in ("", "show", "list", "ls"):
        return "show", None
    if text in ("clear", "default", "reset", "none"):
        return "clear", None
    if text in ("force", "auto", "always"):
        return "set", TRUST_FORCE
    if text in ("approve", "ask", "manual"):
        return "set", TRUST_APPROVE
    raise ValueError("Use `force`, `approve`, or `clear`.")


def resolve_trust(
    thread_mode: str | None,
    *,
    backend: str,
    default_mode: str = TRUST_DEFAULT,
) -> ResolvedTrust:
    mode = (thread_mode or "").strip().lower()
    if mode in TRUST_KNOWN:
        return ResolvedTrust(mode=mode, source="thread")
    if backend != "cursor-sdk":
        return ResolvedTrust(mode=TRUST_FORCE, source="backend")
    fallback = default_mode.strip().lower()
    if fallback not in TRUST_KNOWN:
        fallback = TRUST_DEFAULT
    return ResolvedTrust(mode=fallback, source="default")


def format_trust_status(resolved: ResolvedTrust, *, backend: str) -> str:
    note = ""
    if backend != "cursor-sdk":
        note = " (only applies to cursor-sdk)"
    elif resolved.mode == TRUST_APPROVE:
        note = " — shell/MCP hooks wait for Discord Accept/Deny"
    elif resolved.mode == TRUST_FORCE:
        note = " — tools auto-run (headless default)"
    return f"Trust mode `{resolved.mode}` from **{resolved.source}**{note}."
