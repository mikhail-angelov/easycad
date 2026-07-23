"""Multi-tenant in-memory session registry (SPEC13).

Replaces SPEC11's single global `SessionStore` with many stores keyed by an
opaque session id (the `easycad_session` cookie). No CAD working state touches
disk. Sessions have a sliding idle TTL: `last_access` refreshes on every request
and a background sweeper evicts sessions idle longer than the TTL, so active
users stay warm and only idle ones are reclaimed. A capacity cap evicts the
least-recently-used session to bound memory.
"""

import os
import threading
import time

from .store import SessionStore


class Session:
    def __init__(self, session_id: str) -> None:
        self.id = session_id
        self.store = SessionStore()
        # Anonymous per-session settings {provider?, model?, key?}. For logged-in
        # users, settings come from the DB instead (see resolve in main).
        self.settings: dict = {}
        self.user_id: int | None = None
        self.last_access = time.time()

    def touch(self) -> None:
        self.last_access = time.time()


class SessionRegistry:
    def __init__(self, ttl_seconds: float, max_sessions: int) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()
        self.ttl = ttl_seconds
        self.max_sessions = max_sessions

    def get_or_create(self, session_id: str) -> Session:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                self._evict_over_capacity_locked()
                session = Session(session_id)
                self._sessions[session_id] = session
            session.touch()
            return session

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            return self._sessions.get(session_id)

    def drop(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def sweep(self) -> int:
        """Evict sessions idle longer than the TTL. Returns the count removed."""
        cutoff = time.time() - self.ttl
        with self._lock:
            stale = [sid for sid, s in self._sessions.items() if s.last_access < cutoff]
            for sid in stale:
                del self._sessions[sid]
            return len(stale)

    def _evict_over_capacity_locked(self) -> None:
        while len(self._sessions) >= self.max_sessions:
            lru = min(self._sessions.values(), key=lambda s: s.last_access)
            del self._sessions[lru.id]

    def count(self) -> int:
        with self._lock:
            return len(self._sessions)

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()


def build_registry() -> SessionRegistry:
    ttl = float(os.getenv("EASYCAD_SESSION_TTL", "7200"))
    max_sessions = int(os.getenv("EASYCAD_MAX_SESSIONS", "500"))
    return SessionRegistry(ttl, max_sessions)
