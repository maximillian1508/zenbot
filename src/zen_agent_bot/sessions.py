from __future__ import annotations

from dataclasses import dataclass

from .store import ConfigStore


@dataclass
class ThreadSession:
    session_id: str | None = None
    title: str | None = None
    model: str | None = None
    backend: str | None = None
    trust_mode: str | None = None


class SessionStore:
    def __init__(self, db: ConfigStore) -> None:
        self.db = db

    def get(self, thread_key: str) -> ThreadSession:
        row = self.db.get_session(thread_key)
        return ThreadSession(
            session_id=row.get("session_id"),
            title=row.get("title"),
            model=row.get("model"),
            backend=row.get("backend"),
            trust_mode=row.get("trust_mode"),
        )

    def set(self, thread_key: str, session: ThreadSession) -> None:
        self.db.set_session(thread_key, session.session_id, session.title)

    def set_model(self, thread_key: str, model: str | None) -> None:
        self.db.set_session_model(thread_key, model)

    def set_backend(self, thread_key: str, backend: str | None) -> None:
        self.db.set_session_backend(thread_key, backend)

    def set_trust_mode(self, thread_key: str, trust_mode: str | None) -> None:
        self.db.set_session_trust_mode(thread_key, trust_mode)

    def reset_resume(self, thread_key: str) -> None:
        self.db.reset_session_resume(thread_key)

    def clear(self, thread_key: str) -> None:
        self.db.clear_session(thread_key)

    def all(self) -> dict[str, dict[str, str | None]]:
        return self.db.list_sessions()
