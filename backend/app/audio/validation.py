"""Binary audio chunk validation for protocol v1 (PCM16 LE mono frames)."""

from __future__ import annotations


class ChunkTooLargeError(ValueError):
    pass


class ChunkAlignmentError(ValueError):
    pass


def validate_pcm16_chunk(data: bytes, *, max_chunk_bytes: int) -> None:
    """Raise if the binary frame violates protocol v1 framing rules.

    Rules:
    - empty frames are a no-op (allowed, ignored upstream);
    - at most `max_chunk_bytes` bytes per frame (bounded memory / abuse guard);
    - even byte count — every 16-bit sample must be whole.
    """
    if len(data) == 0:
        return
    if len(data) > max_chunk_bytes:
        raise ChunkTooLargeError(
            f"chunk of {len(data)} bytes exceeds limit of {max_chunk_bytes} bytes"
        )
    if len(data) % 2 != 0:
        raise ChunkAlignmentError(
            "PCM16 frames must contain whole 16-bit samples (even byte count)"
        )
