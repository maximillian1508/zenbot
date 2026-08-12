from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# Discord non-nitro limit is 25 MiB; keep under that by default.
DEFAULT_MAX_BYTES = 25 * 1024 * 1024
DEFAULT_MAX_FILES = 10

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._+-]+")


@dataclass(frozen=True)
class SavedAttachment:
    path: Path
    original_name: str
    size: int
    content_type: str | None = None


def attachments_dir(data_dir: Path, *, transport: str, thread_key: str) -> Path:
    safe_thread = _SAFE_NAME.sub("_", thread_key)[:80] or "thread"
    return data_dir / "attachments" / transport / safe_thread


def safe_filename(name: str | None, *, fallback: str = "file") -> str:
    raw = (name or fallback).strip() or fallback
    base = Path(raw).name
    cleaned = _SAFE_NAME.sub("_", base).strip("._") or fallback
    return cleaned[:180]


def stamp_prefix() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")[:-3]


def format_attachments_for_prompt(saved: list[SavedAttachment]) -> str:
    if not saved:
        return ""
    lines = [
        "Attached files (saved on the host — use the Read tool for images "
        "(jpeg/png/gif/webp); open other files by absolute path):",
    ]
    for item in saved:
        extra = f" ({item.content_type})" if item.content_type else ""
        lines.append(f"- `{item.path}`{extra} — {item.original_name} ({item.size} bytes)")
    return "\n".join(lines)


def merge_prompt_with_attachments(text: str, saved: list[SavedAttachment]) -> str:
    block = format_attachments_for_prompt(saved)
    text = text.strip()
    if not block:
        return text
    if not text:
        return block
    return f"{text}\n\n{block}"


async def write_attachment(
    dest_dir: Path,
    *,
    filename: str,
    data: bytes,
    content_type: str | None = None,
    original_name: str | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> SavedAttachment | None:
    if len(data) > max_bytes:
        log.warning(
            "Skipping attachment %s — %s bytes exceeds limit %s",
            filename,
            len(data),
            max_bytes,
        )
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = safe_filename(filename)
    path = dest_dir / f"{stamp_prefix()}_{name}"
    # Avoid rare collisions within the same millisecond.
    if path.exists():
        path = dest_dir / f"{stamp_prefix()}_{name}"
    path.write_bytes(data)
    return SavedAttachment(
        path=path.resolve(),
        original_name=original_name or filename or name,
        size=len(data),
        content_type=content_type,
    )
