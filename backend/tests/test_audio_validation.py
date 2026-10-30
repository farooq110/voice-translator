"""Binary chunk validation rules."""

from __future__ import annotations

import pytest

from app.audio.validation import (
    ChunkAlignmentError,
    ChunkTooLargeError,
    validate_pcm16_chunk,
)


def test_empty_chunk_allowed() -> None:
    validate_pcm16_chunk(b"", max_chunk_bytes=100)


def test_exact_limit_allowed() -> None:
    validate_pcm16_chunk(b"\x00\x00" * 50, max_chunk_bytes=100)


def test_too_large_rejected() -> None:
    with pytest.raises(ChunkTooLargeError):
        validate_pcm16_chunk(b"\x00" * 101, max_chunk_bytes=100)


def test_odd_byte_count_rejected() -> None:
    with pytest.raises(ChunkAlignmentError):
        validate_pcm16_chunk(b"\x00\x00\x01", max_chunk_bytes=100)
