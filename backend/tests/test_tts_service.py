"""TTSService unit tests — bounded execution (Phase 4, ADR-0013).

Mirrors the TranslationService tests: every provider failure maps to a
non-fatal outcome, rate limiting / concurrency / timeout are enforced, and
unsupported languages never reach the provider. Sync tests wrap the async
service calls in asyncio.run (no pytest-asyncio dependency).
"""

from __future__ import annotations

import asyncio
import time

import numpy as np
import pytest

from app.tts.base import SynthesizedAudio
from app.tts.fake import FakeTTSProvider
from app.tts.service import TTSService


def _rms(pcm: bytes) -> float:
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    return float(np.sqrt(np.mean(samples**2))) if samples.size else 0.0


def test_synthesize_ok_returns_real_audio() -> None:
    service = TTSService(provider=FakeTTSProvider(), timeout_s=5)
    outcome = asyncio.run(service.synthesize(text="Hello there", language="en"))
    assert outcome.status == "ok"
    assert outcome.provider == "fake"
    assert outcome.sample_rate > 0
    assert _rms(outcome.pcm) > 0.05  # audible, not silence
    assert outcome.audio_duration_ms > 0
    assert outcome.duration_ms >= 0
    service.aclose()


def test_unsupported_language_never_reaches_provider() -> None:
    provider = FakeTTSProvider(languages=("en",))
    service = TTSService(provider=provider, timeout_s=5)
    outcome = asyncio.run(service.synthesize(text="Bonjour", language="fr"))
    assert outcome.status == "unsupported"
    assert provider.calls == 0
    service.aclose()


def test_text_too_long_is_rejected_before_provider() -> None:
    provider = FakeTTSProvider()
    service = TTSService(provider=provider, timeout_s=5, max_text_chars=10)
    outcome = asyncio.run(service.synthesize(text="x" * 11, language="en"))
    assert outcome.status == "text_too_long"
    assert provider.calls == 0
    service.aclose()


def test_rate_limited() -> None:
    service = TTSService(
        provider=FakeTTSProvider(), timeout_s=5, rate_limit_per_minute=1
    )
    first = asyncio.run(service.synthesize(text="one", language="en"))
    second = asyncio.run(service.synthesize(text="two", language="en"))
    assert first.status == "ok"
    assert second.status == "rate_limited"
    service.aclose()


def test_provider_failure_maps_to_failed() -> None:
    service = TTSService(provider=FakeTTSProvider(fail=True), timeout_s=5)
    outcome = asyncio.run(service.synthesize(text="hello", language="en"))
    assert outcome.status == "failed"
    assert "fake synthesis failure" in outcome.error_message
    service.aclose()


def test_timeout_maps_to_timeout() -> None:
    class SlowProvider(FakeTTSProvider):
        def synthesize(self, text: str, language: str) -> SynthesizedAudio:
            time.sleep(0.2)
            return super().synthesize(text, language)

    service = TTSService(provider=SlowProvider(), timeout_s=0.05)
    outcome = asyncio.run(service.synthesize(text="hello", language="en"))
    assert outcome.status == "timeout"
    assert "exceeded" in outcome.error_message
    service.aclose()


def test_disabled_service_fails_without_provider() -> None:
    service = TTSService(provider=None, timeout_s=5)
    outcome = asyncio.run(service.synthesize(text="hello", language="en"))
    assert outcome.status == "failed"
    service.aclose()


def test_concurrency_is_bounded() -> None:
    state = {"active": 0, "peak": 0}

    class TrackingProvider(FakeTTSProvider):
        def synthesize(self, text: str, language: str) -> SynthesizedAudio:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
            time.sleep(0.05)
            try:
                return super().synthesize(text, language)
            finally:
                state["active"] -= 1

    service = TTSService(provider=TrackingProvider(), timeout_s=5, max_concurrent=1)

    async def four_at_once():
        return await asyncio.gather(
            *(
                service.synthesize(text=f"utterance {i}", language="en")
                for i in range(4)
            )
        )

    outcomes = asyncio.run(four_at_once())
    assert all(o.status == "ok" for o in outcomes)
    assert state["peak"] == 1  # never more than one synthesis at a time
    service.aclose()


def test_describe_surfaces_provider_and_bounds() -> None:
    service = TTSService(
        provider=FakeTTSProvider(languages=("en", "ur")),
        timeout_s=3,
        rate_limit_per_minute=17,
        max_concurrent=1,
        max_text_chars=500,
    )
    info = service.describe()
    assert info["enabled"] is True
    assert info["provider"] == "fake"
    assert info["languages"] == ["en", "ur"]
    assert info["timeout_s"] == 3
    assert info["rate_limit_per_minute"] == 17
    assert info["max_concurrent"] == 1
    assert info["max_text_chars"] == 500
    service.aclose()


def test_synthesis_request_error_is_failure_not_crash() -> None:
    provider = FakeTTSProvider()
    service = TTSService(provider=provider, timeout_s=5)
    outcome = asyncio.run(service.synthesize(text="   ", language="en"))
    assert outcome.status == "failed"
    service.aclose()


def test_fake_provider_is_deterministic_and_audible() -> None:
    """Same (language, text) → identical bytes; different text → different
    audio; output is always non-silent with the expected duration."""
    provider = FakeTTSProvider()
    a = provider.synthesize("the same words", "en")
    b = provider.synthesize("the same words", "en")
    c = provider.synthesize("other words entirely", "en")
    assert a.pcm == b.pcm
    assert a.pcm != c.pcm
    assert _rms(a.pcm) > 0.1
    # 14 chars × 60 ms/char = 840 ms
    assert 800 <= a.duration_ms <= 900
    assert a.sample_rate == 22050
    ur = provider.synthesize("یہ ایک سادہ جملہ ہے", "ur")
    assert _rms(ur.pcm) > 0.1  # RTL content synthesizes too
