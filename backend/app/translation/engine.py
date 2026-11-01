"""TranslationService — decision engine + bounded async translation execution.

One instance per app. Responsibilities (master prompt §10/§15):

- **decide_for()** — pure decision: normalize the whisper detection
  (script/cluster/threshold, KI-002) and apply the §3 rules, consulting
  `provider.supports` only for would-be translations. Disabled service →
  `None` (protocol keeps Phase 1/2 shape).
- **translate()** — the ONLY place provider calls happen, always:
    * behind a per-session... no — a per-SERVICE sliding-window rate limiter
      (`translation_rate_limit_per_minute`, 30/min default) → `rate_limited`;
    * behind a global concurrency semaphore (`translation_max_concurrent`, 2);
    * under a hard `asyncio.wait_for` timeout (`translation_timeout_s`, 8 s)
      → `timeout` (the worker thread finishes in the background; the
      executor is single-threaded so bounded overrun cannot stack up);
    * mapping every provider exception to a non-fatal outcome — the caller
      (WebSocket layer) turns outcomes into utterance-scoped errors while
      the original transcript is kept untouched.
- **warmup()/aclose()** — lifecycle for the provider models and the
  dedicated single-thread executor (STT decodes never queue behind MT).

Text is never rewritten, truncated or normalized by the service — Unicode
and RTL content pass through exactly as the provider returned them.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from .base import TranslationProvider, TranslationProviderError
from .decision import DetectionResult, DecisionEngine, TranslationDecision

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TranslationOutcome:
    """Result of one translation attempt (never an exception)."""

    status: str  # "ok" | "failed" | "timeout" | "rate_limited"
    text: str = ""
    provider: str = ""
    duration_ms: int = 0
    error_message: str = ""


class TranslationRateLimiter:
    """Sliding-window limiter (60 s). `max_per_minute <= 0` disables all calls."""

    def __init__(self, max_per_minute: int) -> None:
        self._max = max(0, int(max_per_minute))
        self._events: deque[float] = deque()

    def allow(self, now: float) -> bool:
        if self._max <= 0:
            return False
        window_start = now - 60.0
        while self._events and self._events[0] <= window_start:
            self._events.popleft()
        if len(self._events) >= self._max:
            return False
        self._events.append(now)
        return True


class TranslationService:
    def __init__(
        self,
        *,
        provider: TranslationProvider | None,
        decision_engine: DecisionEngine,
        detect_mode: str = "auto",
        timeout_s: float = 8.0,
        rate_limit_per_minute: int = 30,
        max_concurrent: int = 2,
        worker_threads: int = 1,
    ) -> None:
        self.provider = provider
        self.enabled = provider is not None
        self._engine = decision_engine
        self.detect_mode = detect_mode if detect_mode in ("auto", "pinned") else "auto"
        self._timeout_s = float(timeout_s)
        self.rate_limit_per_minute = max(0, int(rate_limit_per_minute))
        self.max_concurrent = max(1, int(max_concurrent))
        self._limiter = TranslationRateLimiter(self.rate_limit_per_minute)
        self._semaphore = asyncio.Semaphore(self.max_concurrent)
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, int(worker_threads)), thread_name_prefix="translation"
        )

    # -- decision ------------------------------------------------------------

    @property
    def confidence_threshold(self) -> float:
        return self._engine.confidence_threshold

    def resolve_detection(
        self,
        raw_label: str | None,
        raw_probability: float,
        *,
        native_language: str,
    ) -> DetectionResult:
        """Auto mode: resolve the standalone detect_language result into the
        language the single decode is pinned to (cluster/threshold rules,
        ADR-0012 v2). Disabled service still resolves — the STT pinning must
        work even when translation is off (auto mode requires it)."""
        return self._engine.resolve_stt_language(
            raw_label, raw_probability, native_language=native_language
        )

    def decide_from(
        self,
        detection: DetectionResult,
        *,
        text: str,
        native_language: str,
        preferred_language: str,
    ) -> TranslationDecision | None:
        """Decision for one utterance from its resolved detection — None when
        translation is disabled."""
        if not self.enabled or self.provider is None:
            return None
        return self._engine.decide(
            detection,
            text=text,
            native_language=native_language,
            preferred_language=preferred_language,
            provider_supports=self.provider.supports,
        )

    # -- execution -----------------------------------------------------------

    async def translate(self, *, text: str, source_language: str, target_language: str) -> TranslationOutcome:
        provider = self.provider
        if provider is None:
            return TranslationOutcome(status="failed", error_message="translation is disabled")
        if not self._limiter.allow(time.monotonic()):
            logger.info("translation.rate_limited limit reached")
            return TranslationOutcome(status="rate_limited")

        started = time.perf_counter()
        try:
            async with self._semaphore:
                loop = asyncio.get_running_loop()
                translated = await asyncio.wait_for(
                    loop.run_in_executor(
                        self._executor, provider.translate, text, source_language, target_language
                    ),
                    timeout=self._timeout_s,
                )
        except asyncio.TimeoutError:
            duration = int((time.perf_counter() - started) * 1000)
            logger.info("translation.timeout after_ms=%d", duration)
            return TranslationOutcome(
                status="timeout",
                duration_ms=duration,
                error_message=f"translation exceeded {self._timeout_s:.0f}s",
            )
        except TranslationProviderError as exc:
            duration = int((time.perf_counter() - started) * 1000)
            logger.info("translation.failed after_ms=%d error=%s", duration, exc)
            return TranslationOutcome(
                status="failed",
                duration_ms=duration,
                error_message=str(exc) or type(exc).__name__,
            )
        except Exception:  # noqa: BLE001 — a provider bug must not kill the socket
            logger.exception("translation provider crashed")
            return TranslationOutcome(
                status="failed", error_message="unexpected provider failure"
            )

        duration = int((time.perf_counter() - started) * 1000)
        logger.info(
            "translation.ok provider=%s src=%s tgt=%s chars=%d after_ms=%d",
            provider.name, source_language, target_language, len(translated), duration,
        )
        return TranslationOutcome(
            status="ok", text=translated, provider=provider.name, duration_ms=duration
        )

    # -- lifecycle -----------------------------------------------------------

    def warmup(self) -> None:
        """Pre-load provider models (blocking — call from a worker thread)."""
        if self.provider is not None:
            self.provider.ensure_loaded()

    def aclose(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def describe(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "provider": self.provider.name if self.provider is not None else None,
            "detect_mode": self.detect_mode,
            "timeout_s": self._timeout_s,
            "rate_limit_per_minute": self.rate_limit_per_minute,
            "max_concurrent": self.max_concurrent,
        }
