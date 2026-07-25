"""
session.py

Per conversation state.

The original app kept `previous_faculty.json` and `conversation_history.json` in
the working directory, which means every concurrent user shares one follow up
context: user B asking "what about their grants" would resolve against user A's
last result. State is keyed by session id here instead.

This is an in process store with TTL eviction, which is correct for a single
backend container. Swap `SessionStore` for Redis if the backend is ever scaled
to more than one replica.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SessionState:
    previous_faculty: list[str] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)
    updated_at: float = field(default_factory=time.time)

    def touch(self) -> None:
        self.updated_at = time.time()


class SessionStore:
    def __init__(self, ttl_seconds: int) -> None:
        self._ttl = ttl_seconds
        self._data: dict[str, SessionState] = {}
        self._lock = threading.Lock()

    def get(self, session_id: str) -> SessionState:
        with self._lock:
            self._evict_expired()
            state = self._data.get(session_id)
            if state is None:
                state = SessionState()
                self._data[session_id] = state
            state.touch()
            return state

    def set_previous_faculty(self, session_id: str, faculty: list[str]) -> None:
        state = self.get(session_id)
        with self._lock:
            state.previous_faculty = list(faculty)
            state.touch()

    def append_history(self, session_id: str, question: str, results: Any) -> None:
        state = self.get(session_id)
        with self._lock:
            state.history.append({"question": question, "results": results})
            # Keep the tail only. Nothing downstream reads deep history, and an
            # unbounded list is a slow memory leak.
            if len(state.history) > 25:
                state.history = state.history[-25:]
            state.touch()

    def reset(self, session_id: str) -> None:
        with self._lock:
            self._data.pop(session_id, None)

    def _evict_expired(self) -> None:
        cutoff = time.time() - self._ttl
        expired = [key for key, value in self._data.items() if value.updated_at < cutoff]
        for key in expired:
            self._data.pop(key, None)
