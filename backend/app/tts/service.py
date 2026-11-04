"""TTSService — bounded async speech synthesis execution (Phase 4, ADR-0013).

One instance per app. Mirrors TranslationService's execution discipline
(master prompt §15):

- the ONLY place provider calls happen, always:
    * behind a per-SERVICE sliding-window rate limiter
      (`tts_rate_limit_per_minute`, 30/min default) → `rate_limited`;
    * behind a global concurrency semaphore (`tts_max_concurrent`, 1 —
      synthesis is CPU-bound and the provider is loaded lazily per language);
    * under a hard `asyncio.wait_for` timeout (`tts_timeout_s`, 10 s) →
      `timeout` (the worker thread finishes in the background; the executor
      is single-threaded so bounded overrun cannot stack up);
    * mapping every provider exception to a non-fatal outcome — the caller
      (WebSocket layer) turns outcomes into utterance-scoped errors while
      the translated text stays untouched on screen.
- unsupported languages and over-long texts are decided BEFORE the provider
  call (no wasted synthesis) → `unsupported` / `text_too_long`.
- warmup()/aclose() — lifecycle for the dedicated single-thread executor
  (STT decodes and MT never queue behind synthesis).

Audio is produced as raw PCM16 mono; the WebSocket layer frames it as one
JSON metadata event plus one binary frame (docs/audio-protocol.md).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from .base import TTSProvider, TTSProviderError
from .fake import primary_subtag

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TTSOutcome:
    """Result of one synthesis attempt (never an exception)."""

    status: str  # "ok" | "failed" | "timeout" | "rate_limited" | "unsupported" | "text_too_long"
    pcm: bytes = b""
    sample_rate: int = 0
    audio_duration_ms: int = 0
    provider: str = ""
    duration_ms: int = 0
    error_message: str = ""


class _RateLimiter:
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


class TTSService:
    def __init__(
        self,
        *,
        provider: TTSProvider | None,
        timeout_s: float = 10.0,
        rate_limit_per_minute: int = 30,
        max_concurrent: int = 1,
        max_text_chars: int = 800,
        worker_threads: int = 1,
    ) -> None:
        self.provider = provider
        self.enabled = provider is not None
        self._timeout_s = float(timeout_s)
        self.rate_limit_per_minute = max(0, int(rate_limit_per_minute))
        self.max_concurrent = max(1, int(max_concurrent))
        self.max_text_chars = max(0, int(max_text_chars))
        self._limiter = _RateLimiter(self.rate_limit_per_minute)
        self._semaphore = asyncio.Semaphore(self.max_concurrent)
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, int(worker_threads)), thread_name_prefix="tts"
        )

    # -- execution -----------------------------------------------------------

    async def synthesize(self, *, text: str, language: str) -> TTSOutcome:
        provider = self.provider
        if provider is None:
            return TTSOutcome(status="failed", error_message="tts is disabled")
        if not text.strip():
            return TTSOutcome(
                status="failed", error_message="empty text", provider=provider.name
            )
        if len(text) > self.max_text_chars:
            logger.info(
                "tts.text_too_long chars=%d limit=%d", len(text), self.max_text_chars
            )
            return TTSOutcome(status="text_too_long", provider=provider.name)
        if not provider.supports(language):
            logger.info("tts.unsupported language=%s", language)
            return TTSOutcome(status="unsupported", provider=provider.name)
        if not self._limiter.allow(time.monotonic()):
            logger.info("tts.rate_limited limit reached")
            return TTSOutcome(status="rate_limited", provider=provider.name)

        started = time.perf_counter()
        try:
            async with self._semaphore:
                loop = asyncio.get_running_loop()
                audio = await asyncio.wait_for(
                    loop.run_in_executor(
                        self._executor, provider.synthesize, text, language
                    ),
                    timeout=self._timeout_s,
                )
        except asyncio.TimeoutError:
            duration = int((time.perf_counter() - started) * 1000)
            logger.info("tts.timeout after_ms=%d", duration)
            return TTSOutcome(
                status="timeout",
                duration_ms=duration,
                provider=provider.name,
                error_message=f"synthesis exceeded {self._timeout_s:.0f}s",
            )
        except TTSProviderError as exc:
            duration = int((time.perf_counter() - started) * 1000)
            logger.info("tts.failed after_ms=%d error=%s", duration, exc)
            return TTSOutcome(
                status="failed",
                duration_ms=duration,
                provider=provider.name,
                error_message=str(exc) or type(exc).__name__,
            )
        except Exception:  # noqa: BLE001 — a provider bug must not kill the socket
            logger.exception("tts provider crashed")
            return TTSOutcome(
                status="failed",
                provider=provider.name,
                error_message="unexpected provider failure",
            )

        duration = int((time.perf_counter() - started) * 1000)
        logger.info(
            "tts.ok provider=%s language=%s chars=%d synthesis_ms=%d audio_ms=%d",
            provider.name,
            language,
            len(text),
            duration,
            audio.duration_ms,
        )
        return TTSOutcome(
            status="ok",
            pcm=audio.pcm,
            sample_rate=audio.sample_rate,
            audio_duration_ms=audio.duration_ms,
            provider=provider.name,
            duration_ms=duration,
        )

    # -- lifecycle -----------------------------------------------------------

    def warmup(self) -> None:
        """Pre-load provider resources (blocking — call from a worker thread).
        The piper provider is lazy by design, so this is a no-op there."""
        if self.provider is not None:
            self.provider.ensure_loaded()

    def aclose(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def describe(self) -> dict[str, object]:
        provider_info: dict[str, object] = (
            dict(self.provider.describe())
            if self.provider is not None
            else {"provider": None}
        )
        return {
            "enabled": self.enabled,
            **provider_info,
            "languages": provider_info.get("languages", []),
            "timeout_s": self._timeout_s,
            "rate_limit_per_minute": self.rate_limit_per_minute,
            "max_concurrent": self.max_concurrent,
            "max_text_chars": self.max_text_chars,
        }


__all__ = ["TTSService", "TTSOutcome", "primary_subtag"]
