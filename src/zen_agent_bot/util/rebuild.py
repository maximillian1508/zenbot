from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


REBUILD_FLAG_NAME = "REQUEST_REBUILD"


def rebuild_flag_path(data_dir: Path) -> Path:
    return data_dir / REBUILD_FLAG_NAME


def request_rebuild(data_dir: Path, *, reason: str = "") -> Path:
    """Write the host-watched rebuild flag. Returns the flag path."""
    data_dir.mkdir(parents=True, exist_ok=True)
    path = rebuild_flag_path(data_dir)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = f"requested_at={stamp}\n"
    if reason.strip():
        body += f"reason={reason.strip()[:500]}\n"
    path.write_text(body, encoding="utf-8")
    return path


def rebuild_pending(data_dir: Path) -> bool:
    return rebuild_flag_path(data_dir).is_file()
