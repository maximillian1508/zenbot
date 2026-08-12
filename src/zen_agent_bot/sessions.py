from __future__ import annotations

from dataclasses import dataclass

from .store import ConfigStore


@dataclass
class ThreadSession:
    session_id: str | None = None
    title: str | None = None


class SessionStore:
    def __init__(self, db: ConfigStore) -> None:
        self.db = db

    def get(self, thread_key: str) -> ThreadSession:
        row = self.db.get_session(thread_key)
        return ThreadSession(session_id=row.get("session_id"), title=row.get("title"))

    def set(self, thread_key: str, session: ThreadSession) -> None:
        self.db.set_session(thread_key, session.session_id, session.title)

    def clear(self, thread_key: str) -> None:
        self.db.clear_session(thread_key)

    def all(self) -> dict[str, dict[str, str | None]]:
        return self.db.list_sessions()
