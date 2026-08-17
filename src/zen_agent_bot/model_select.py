"""Resolve agent model: thread override → env → admin setting → backend default."""

from __future__ import annotations

import os
from dataclasses import dataclass

from .store import ConfigStore

BACKEND_MODEL_ENV = {
    "cursor-cli": "AGENT_MODEL",
    "cursor-sdk": "AGENT_MODEL",
    "claude-cli": "CLAUDE_MODEL",
    "openrouter": "OPENROUTER_MODEL",
}
BACKEND_MODEL_SETTING = {
    "cursor-cli": "backend.cursor-cli.model",
    "cursor-sdk": "backend.cursor-sdk.model",
    "claude-cli": "backend.claude-cli.model",
    "openrouter": "backend.openrouter.model",
}
OPENROUTER_FALLBACK = "anthropic/claude-sonnet-4"
CURSOR_SDK_FALLBACK = "composer-2.5"
OPENROUTER_ONLINE_SETTING = "backend.openrouter.online"
OPENROUTER_ONLINE_ENV = "OPENROUTER_ONLINE"
ONLINE_SUFFIX = ":online"
CLEAR_TOKENS = frozenset({"clear", "default", "none", "reset"})
SHOW_TOKENS = frozenset({"list", "ls"})
MAX_MODEL_LEN = 128


def blank_to_none(raw: str | None) -> str | None:
    if raw is None:
        return None
    text = raw.strip()
    return text or None


def _truthy(raw: str | None, default: bool = False) -> bool:
    if raw is None:
        return default
    text = raw.strip()
    if not text:
        return default
    return text.lower() not in ("0", "false", "no", "off")


def openrouter_online_enabled(db: ConfigStore) -> bool:
    """Env OPENROUTER_ONLINE wins when set; else admin setting (default off)."""
    if OPENROUTER_ONLINE_ENV in os.environ:
        return _truthy(os.environ.get(OPENROUTER_ONLINE_ENV), False)
    return _truthy(db.get_setting(OPENROUTER_ONLINE_SETTING), False)


def has_online_suffix(model: str) -> bool:
    if ":" not in model:
        return False
    _, _, rest = model.partition(":")
    return "online" in rest.split(":")


def apply_openrouter_online(model: str | None, *, online: bool) -> str | None:
    """Append :online once when the toggle is on. Leave an existing suffix alone."""
    if model is None:
        return None
    text = model.strip()
    if not text:
        return None
    if has_online_suffix(text) or not online:
        return text
    return text + ONLINE_SUFFIX


@dataclass(frozen=True)
class ResolvedModel:
    model: str | None
    source: str  # thread | env | admin | default
    backend: str


def admin_default_model(db: ConfigStore, backend: str) -> tuple[str | None, str]:
    """Return (model, source) ignoring thread override. source is env, admin, or default."""
    env_name = BACKEND_MODEL_ENV.get(backend)
    if env_name:
        env_val = blank_to_none(os.environ.get(env_name))
        if env_val:
            return env_val, "env"
    setting_key = BACKEND_MODEL_SETTING.get(backend)
    if setting_key:
        setting_val = blank_to_none(db.get_setting(setting_key))
        if setting_val:
            return setting_val, "admin"
    if backend == "cursor-sdk":
        cli = blank_to_none(db.get_setting("backend.cursor-cli.model"))
        if cli:
            return cli, "admin"
        return CURSOR_SDK_FALLBACK, "default"
    if backend == "openrouter":
        return OPENROUTER_FALLBACK, "default"
    return None, "default"


def resolve_model(db: ConfigStore, session_key: str, backend: str) -> ResolvedModel:
    row = db.get_session(session_key)
    thread = blank_to_none(row.get("model"))
    if thread:
        model, source = thread, "thread"
    else:
        model, source = admin_default_model(db, backend)
    if backend == "openrouter":
        model = apply_openrouter_online(
            model, online=openrouter_online_enabled(db)
        )
    return ResolvedModel(model=model, source=source, backend=backend)


def parse_model_arg(raw: str | None) -> tuple[str, str | None]:
    """Return ('show', None), ('clear', None), or ('set', model_id)."""
    if raw is None:
        return "show", None
    text = raw.strip()
    if not text:
        return "show", None
    if text.lower() in CLEAR_TOKENS:
        return "clear", None
    if text.lower() in SHOW_TOKENS:
        return "show", None
    if "\n" in text or "\r" in text:
        raise ValueError("Model id must be a single line")
    if len(text) > MAX_MODEL_LEN:
        raise ValueError(f"Model id too long (max {MAX_MODEL_LEN})")
    return "set", text


def format_model_status(resolved: ResolvedModel) -> str:
    label = resolved.model or "(CLI default)"
    source_note = {
        "thread": "this thread",
        "env": "from env",
        "admin": "admin default",
        "default": f"{resolved.backend} default",
    }.get(resolved.source, resolved.source)
    lines = [
        f"**Model:** `{label}` · {source_note}",
        f"**Backend:** `{resolved.backend}`",
    ]
    if resolved.source == "thread":
        lines.append("Use `/model clear` to drop the thread override.")
    else:
        lines.append("Use `/model <id>` to override this thread (next job).")
    return "\n".join(lines)


def parse_agent_models_output(text: str) -> list[tuple[str, str]]:
    """Parse `agent models` / `agent --list-models` text into (id, label) rows."""
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.lower().startswith("available models"):
            continue
        if line.lower().startswith("tip:"):
            break
        if " - " not in line:
            continue
        model_id, label = line.split(" - ", 1)
        model_id = model_id.strip()
        label = label.strip()
        if not model_id or " " in model_id or model_id in seen:
            continue
        seen.add(model_id)
        rows.append((model_id, label))
    return rows


def format_cursor_catalog(
    models: list[tuple[str, str]],
    *,
    current: str | None = None,
    max_chars: int = 1400,
    featured: int = 16,
) -> str:
    if not models:
        return "_Could not load Cursor model list (`agent models`)._"
    header = f"**Cursor CLI models** ({len(models)} on this account):"
    shown = list(models[:featured])
    shown_ids = {mid for mid, _ in shown}
    if current and current not in shown_ids:
        label = next((lab for mid, lab in models if mid == current), current)
        shown = [(current, label)] + shown
    lines = [header]
    for mid, label in shown:
        mark = " ←" if current and mid == current else ""
        lines.append(f"• `{mid}` — {label}{mark}")
    rest = len(models) - len(shown)
    if rest > 0:
        lines.append(
            f"_…{rest} more. Autocomplete `/model` or `/model <id>`._"
        )
    text = "\n".join(lines)
    while len(text) > max_chars and len(shown) > 4:
        shown.pop()
        lines = [header]
        for mid, label in shown:
            mark = " ←" if current and mid == current else ""
            lines.append(f"• `{mid}` — {label}{mark}")
        rest = len(models) - len(shown)
        if rest > 0:
            lines.append(
                f"_…{rest} more. Autocomplete `/model` or `/model <id>`._"
            )
        text = "\n".join(lines)
    return text


def model_in_catalog(model_id: str | None, models: list[tuple[str, str]]) -> bool:
    if not model_id:
        return True
    return any(mid == model_id for mid, _ in models)
