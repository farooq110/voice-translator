"""Session manager behavior (bounded, correct cleanup)."""

from __future__ import annotations

import pytest

from app.sessions.manager import SessionLimitError, SessionManager


def test_create_get_remove() -> None:
    mgr = SessionManager(max_sessions=2)
    s = mgr.create()
    assert mgr.get(s.id) is s
    mgr.remove(s.id)
    assert mgr.get(s.id) is None
    assert mgr.active_count() == 0


def test_session_limit_enforced() -> None:
    mgr = SessionManager(max_sessions=2)
    mgr.create()
    mgr.create()
    with pytest.raises(SessionLimitError):
        mgr.create()


def test_take_buffer_clears() -> None:
    mgr = SessionManager()
    s = mgr.create()
    assert s.append_audio(b"\x00\x00" * 10) == 20
    assert s.buffered_bytes == 20
    assert s.take_buffer() == b"\x00\x00" * 10
    assert s.buffered_bytes == 0
    assert s.take_buffer() == b""


def test_remove_marks_closed() -> None:
    mgr = SessionManager()
    s = mgr.create()
    assert s.closed is False
    mgr.remove(s.id)
    assert s.closed is True


def test_remove_unknown_is_noop() -> None:
    mgr = SessionManager()
    mgr.remove("does-not-exist")  # must not raise
