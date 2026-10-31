"""STT admission control (Phase 2 — bounded queues, backpressure).

Why: the STT executor is deliberately small (1 CPU worker — inference
serialization is what keeps latency predictable), so work can pile up faster
than it is served. `ThreadPoolExecutor`'s internal queue is unbounded, so
without an admission gate a burst of flushes (or many concurrent sessions)
could queue dozens of 30 s utterances: unbounded memory growth and ever more
stale results — exactly what master prompt §9 forbids.

The gate is a plain counter manipulated only from the event loop (single-
threaded), so it needs no locks:

- `try_acquire()` admits one utterance for decoding (per flush);
- `release()` is called exactly once when its task finishes (any outcome);
- when the gate is full the flush is answered with a non-fatal `server_busy`
  error instead of being queued — the client keeps the connection and can
  retry; this IS the backpressure.

Per-session concurrency is additionally bounded in the WebSocket layer
(`stt_max_pending_per_session`), so one misbehaving client cannot monopolize
the global queue.
"""

from __future__ import annotations


class SttQueue:
    """Bounded, process-global admission gate for STT decode tasks."""

    def __init__(self, max_queued: int) -> None:
        self._max = max(1, int(max_queued))
        self._queued = 0

    def try_acquire(self) -> bool:
        """Admit one utterance; False when the queue is full (backpressure)."""
        if self._queued >= self._max:
            return False
        self._queued += 1
        return True

    def release(self) -> None:
        """Mark one admitted utterance as finished (idempotent at zero)."""
        self._queued = max(0, self._queued - 1)

    @property
    def queued(self) -> int:
        return self._queued

    def stats(self) -> dict[str, int]:
        return {"queued": self._queued, "max": self._max}
