"""Per-thread job queue helpers (promote / drop by id)."""

from __future__ import annotations

from collections import deque
from typing import TypeVar

T = TypeVar("T")


def promote_by_id(pending: deque[T], job_id: str) -> T | None:
    """Move the matching job to the front. Returns it, or None if missing."""
    for item in pending:
        if getattr(item, "job_id", None) == job_id:
            pending.remove(item)
            pending.appendleft(item)
            return item
    return None


def drop_by_id(pending: deque[T], job_id: str) -> T | None:
    """Remove the matching job. Returns it, or None if missing."""
    for item in pending:
        if getattr(item, "job_id", None) == job_id:
            pending.remove(item)
            return item
    return None


def queued_count(pending: deque[object], *, stop: object | None = None) -> int:
    return sum(1 for item in pending if item is not stop)
