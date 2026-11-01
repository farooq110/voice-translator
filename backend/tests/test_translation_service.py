"""TranslationService tests — bounded execution semantics (no network).

Covers the outcome mapping (ok / failed / timeout / rate_limited), the
sliding-window rate limiter, disabled-service behavior and describe().
"""

from __future__ import annotations

import asyncio

import pytest

from app.translation.decision import (
    DecisionEngine,
    DetectionResult,
    DETECTION_WHISPER,
)
from app.translation.engine import TranslationRateLimiter, TranslationService
from app.translation.fake import FakeTranslationProvider


def test_rate_limiter_allows_up_to_limit_then_blocks() -> None:
    limiter = TranslationRateLimiter(max_per_minute=3)
    now = 1000.0
    assert [limiter.allow(now) for _ in range(3)] == [True, True, True]
    assert limiter.allow(now + 1) is False
    # a full minute later the window is empty again
    assert limiter.allow(now + 61) is True


def test_rate_limiter_zero_disables_everything() -> None:
    limiter = TranslationRateLimiter(max_per_minute=0)
    assert limiter.allow(0.0) is False


_UNSET = object()


def _service(provider=_UNSET, **overrides) -> TranslationService:
    kwargs = {
        "provider": FakeTranslationProvider() if provider is _UNSET else provider,
        "decision_engine": DecisionEngine(),
        "detect_mode": "auto",
        "timeout_s": 2.0,
        "rate_limit_per_minute": 30,
        "max_concurrent": 2,
    }
    kwargs.update(overrides)
    return TranslationService(**kwargs)


def test_translate_ok_outcome() -> None:
    service = _service()
    outcome = asyncio.run(
        service.translate(text="سلام", source_language="ur", target_language="en")
    )
    assert outcome.status == "ok"
    assert outcome.text == "[fake:ur\u2192en] سلام"
    assert outcome.provider == "fake"
    assert outcome.duration_ms >= 0
    service.aclose()


def test_translate_provider_failure_maps_to_failed() -> None:
    service = _service(FakeTranslationProvider(fail=True))
    outcome = asyncio.run(
        service.translate(text="x", source_language="ur", target_language="en")
    )
    assert outcome.status == "failed"
    assert "fake provider configured to fail" in outcome.error_message
    service.aclose()


def test_translate_timeout_maps_to_timeout() -> None:
    service = _service(FakeTranslationProvider(delay_s=0.4), timeout_s=0.05)
    outcome = asyncio.run(
        service.translate(text="x", source_language="ur", target_language="en")
    )
    assert outcome.status == "timeout"
    service.aclose()


def test_translate_rate_limited_after_first_call() -> None:
    service = _service(rate_limit_per_minute=1)
    first = asyncio.run(
        service.translate(text="a", source_language="ur", target_language="en")
    )
    second = asyncio.run(
        service.translate(text="b", source_language="ur", target_language="en")
    )
    assert first.status == "ok"
    assert second.status == "rate_limited"
    service.aclose()


def test_disabled_service_no_decision_and_failed_translate() -> None:
    service = _service(provider=None)
    assert service.enabled is False
    assert (
        service.decide_from(
            DetectionResult("ur", 0.95, DETECTION_WHISPER),
            text="x",
            native_language="ur",
            preferred_language="en",
        )
        is None
    )
    outcome = asyncio.run(
        service.translate(text="x", source_language="ur", target_language="en")
    )
    assert outcome.status == "failed"
    service.aclose()


def test_resolve_and_decide_through_the_service() -> None:
    service = _service()
    detection = service.resolve_detection("hi", 0.775, native_language="ur")
    assert detection.language == "ur"  # KI-002 cluster resolution
    assert service.confidence_threshold == 0.60
    decision = service.decide_from(
        detection,
        text="سلام",
        native_language="ur",
        preferred_language="en",
    )
    assert decision is not None
    assert decision.action == "translate"
    assert decision.reason == "native_language"
    assert decision.target_language == "en"
    service.aclose()


def test_describe_reports_configuration() -> None:
    service = _service(rate_limit_per_minute=7, max_concurrent=3, timeout_s=1.5)
    info = service.describe()
    assert info == {
        "enabled": True,
        "provider": "fake",
        "detect_mode": "auto",
        "timeout_s": 1.5,
        "rate_limit_per_minute": 7,
        "max_concurrent": 3,
    }
    service.aclose()
