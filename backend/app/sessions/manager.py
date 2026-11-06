"""In-memory session bookkeeping for audio WebSocket connections."""

from __future__ import annotations

import threading
import time
import uuid

from ..models.protocol import HelloConfig


class SessionLimitError(RuntimeError):
    """Raised when a new session would exceed the configured maximum."""


class Session:
    """State of one audio WebSocket session (one participant).

    Buffers are bounded by the WebSocket handler (auto-flush at
    `max_utterance_seconds`), so memory cannot grow without bound.
    """

    __slots__ = ("id", "created_at", "closed", "config", "utterances", "platform", "_buffer")

    def __init__(self, session_id: str) -> None:
        self.id = session_id
        self.created_at = time.time()
        self.closed = False
        self.config: HelloConfig | None = None
        self.utterances = 0
        # Phase 6: self-reported client platform tag (optional, observational).
        self.platform: str | None = None
        self._buffer = bytearray()

    def append_audio(self, chunk: bytes) -> int:
        """Append raw PCM16 bytes; returns the new total buffered size in bytes."""
        self._buffer.extend(chunk)
        return len(self._buffer)

    def take_buffer(self) -> bytes:
        """Swap out and clear the buffer; returns the accumulated bytes."""
        data = bytes(self._buffer)
        self._buffer.clear()
        return data

    @property
    def buffered_bytes(self) -> int:
        return len(self._buffer)


class SessionManager:
    def __init__(self, max_sessions: int = 50) -> None:
        self._max = max(1, max_sessions)
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self) -> Session:
        with self._lock:
            if len(self._sessions) >= self._max:
                raise SessionLimitError(f"session limit reached ({self._max})")
            session = Session(session_id=f"s_{uuid.uuid4().hex[:12]}")
            self._sessions[session.id] = session
            return session

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            return self._sessions.get(session_id)

    def remove(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.pop(session_id, None)
            if session is not None:
                session.closed = True

    def active_count(self) -> int:
        with self._lock:
            return len(self._sessions)

    def stats(self) -> dict[str, object]:
        """Active/max counts plus a by-platform tally of connected clients
        (Phase 6 observability for the real-device matrix, KI-004)."""
        with self._lock:
            by_platform: dict[str, int] = {}
            for session in self._sessions.values():
                if session.platform:
                    by_platform[session.platform] = by_platform.get(session.platform, 0) + 1
            return {
                "active": len(self._sessions),
                "max": self._max,
                "by_platform": by_platform,
            }
