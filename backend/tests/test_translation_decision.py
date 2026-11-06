"""Unit tests for the Phase 3 decision engine (pure logic — no I/O).

Covers the master prompt §3/§4 rules and the ADR-0012 v2 detection
resolution (standalone detect_language + ur/hi cluster + confidence gate —
the KI-002 design input). Real measured behavior: ur speech → whisper label
`hi` p≈0.775; en/es labels are reliable.
"""

from __future__ import annotations

import pytest

from app.translation.decision import (
    ACTION_PASSTHROUGH,
    ACTION_TRANSLATE,
    DecisionEngine,
    DetectionResult,
    DETECTION_CLUSTER,
    DETECTION_FALLBACK,
    DETECTION_PINNED,
    DETECTION_WHISPER,
    REASON_IDENTICAL,
    REASON_LOW_CONFIDENCE,
    REASON_NATIVE,
    REASON_PREFERRED,
    REASON_THIRD,
    REASON_TOO_LONG,
    REASON_UNDETECTED,
    REASON_UNSUPPORTED,
    script_profile,
)

URDU_TEXT = "یہ ایک آزمائشی جملہ ہے"  # Arabic script
HINDI_TEXT = "यह एक वाक्य है"  # Devanagari script


def supports_all(src: str, tgt: str) -> bool:
    return True


def supports_never(src: str, tgt: str) -> bool:  # pragma: no cover - spy below
    raise AssertionError("provider_supports must not be consulted for pass-through")


def test_script_profile_ratios() -> None:
    profile = script_profile(URDU_TEXT)
    assert profile["arabic"] > 0.9
    assert script_profile(HINDI_TEXT)["devanagari"] > 0.9
    assert script_profile("hello world")["latin"] > 0.9
    assert script_profile("...123") == {"arabic": 0.0, "devanagari": 0.0, "latin": 0.0}


# ------------------------------------------------- resolve_stt_language ----


def test_resolve_native_label_stays_native() -> None:
    engine = DecisionEngine()
    detection = engine.resolve_stt_language("ur", 0.9, native_language="ur")
    assert detection == DetectionResult("ur", 0.9, DETECTION_WHISPER)


def test_resolve_ki002_hindi_label_collapses_to_urdu_cluster() -> None:
    """The MEASURED whisper behavior on the ur fixture: hi p=0.775 (ur second
    at 0.20) — the ur/hi cluster resolves it to the configured native."""
    engine = DecisionEngine()
    detection = engine.resolve_stt_language("hi", 0.775, native_language="ur")
    assert detection == DetectionResult("ur", 0.775, DETECTION_CLUSTER)


def test_resolve_without_label_falls_back_to_native() -> None:
    engine = DecisionEngine()
    detection = engine.resolve_stt_language(None, 0.0, native_language="ur-PK")
    assert detection == DetectionResult("ur", 0.0, DETECTION_FALLBACK)


def test_resolve_confident_third_language_is_kept() -> None:
    engine = DecisionEngine()
    detection = engine.resolve_stt_language("es", 0.989, native_language="ur")
    assert detection == DetectionResult("es", 0.989, DETECTION_WHISPER)


def test_resolve_uncertain_third_language_falls_back_to_native() -> None:
    """Below the threshold the label is untrusted — decode as native (safe
    KI-006 default); the decision engine answers low_confidence."""
    engine = DecisionEngine()
    detection = engine.resolve_stt_language("fr", 0.40, native_language="ur")
    assert detection == DetectionResult("ur", 0.40, DETECTION_FALLBACK)


def test_resolve_preferred_label_is_kept_for_rule2() -> None:
    engine = DecisionEngine()
    detection = engine.resolve_stt_language("en", 0.997, native_language="ur")
    assert detection == DetectionResult("en", 0.997, DETECTION_WHISPER)


def test_resolve_regional_native_subtag_is_reduced() -> None:
    engine = DecisionEngine()
    detection = engine.resolve_stt_language("ur", 0.9, native_language="ur-PK")
    assert detection.language == "ur"


def test_resolve_arabic_label_for_urdu_native_stays_third_language() -> None:
    """The measured standalone detector does NOT produce this (it says hi),
    but a confident `ar` label with native=ur must stay a third language —
    the master prompt EXPLICITLY requires Arabic pass-through (Rule 3)."""
    engine = DecisionEngine()
    detection = engine.resolve_stt_language("ar", 0.97, native_language="ur")
    assert detection == DetectionResult("ar", 0.97, DETECTION_WHISPER)


# ------------------------------------------------------------------ decide --


def _detection(
    language: str, probability: float = 0.95, source: str = DETECTION_WHISPER
) -> DetectionResult:
    return DetectionResult(language, probability, source)


def test_decide_rule1_native_speech_translates_to_preferred() -> None:
    engine = DecisionEngine()
    decision = engine.decide(
        _detection("ur"),
        text=URDU_TEXT,
        native_language="ur",
        preferred_language="en",
        provider_supports=supports_all,
    )
    assert decision.action == ACTION_TRANSLATE
    assert decision.reason == REASON_NATIVE
    assert decision.source_language == "ur"
    assert decision.target_language == "en"


def test_decide_rule2_preferred_speech_translates_to_native() -> None:
    engine = DecisionEngine()
    decision = engine.decide(
        _detection("en"),
        text="hello there",
        native_language="ur",
        preferred_language="en",
        provider_supports=supports_all,
    )
    assert decision.action == ACTION_TRANSLATE
    assert decision.reason == REASON_PREFERRED
    assert decision.target_language == "ur"


def test_decide_rule3_third_language_passes_through_without_provider_call() -> None:
    engine = DecisionEngine()
    decision = engine.decide(
        _detection("es"),
        text="hola",
        native_language="ur",
        preferred_language="en",
        provider_supports=supports_never,  # must never be consulted
    )
    assert decision.action == ACTION_PASSTHROUGH
    assert decision.reason == REASON_THIRD
    assert decision.target_language is None


def test_decide_identical_configuration_is_pass_through() -> None:
    engine = DecisionEngine()
    decision = engine.decide(
        _detection("en"),
        text="hello",
        native_language="en",
        preferred_language="en",
        provider_supports=supports_never,
    )
    assert decision.action == ACTION_PASSTHROUGH
    assert decision.reason == REASON_IDENTICAL


def test_decide_undetected_language_is_pass_through() -> None:
    engine = DecisionEngine()
    decision = engine.decide(
        DetectionResult("", 0.0, "unknown"),
        text="hello",
        native_language="ur",
        preferred_language="en",
        provider_supports=supports_never,
    )
    assert decision.action == ACTION_PASSTHROUGH
    assert decision.reason == REASON_UNDETECTED
    assert decision.source_language == ""


def test_decide_overlong_text_is_pass_through() -> None:
    engine = DecisionEngine(max_text_chars=10)
    decision = engine.decide(
        _detection("ur"),
        text="x" * 11,
        native_language="ur",
        preferred_language="en",
        provider_supports=supports_never,
    )
    assert decision.action == ACTION_PASSTHROUGH
    assert decision.reason == REASON_TOO_LONG


def test_decide_unsupported_pair_is_pass_through() -> None:
    engine = DecisionEngine()
    calls: list[tuple[str, str]] = []

    def supports(src: str, tgt: str) -> bool:
        calls.append((src, tgt))
        return False

    decision = engine.decide(
        _detection("ur"),
        text=URDU_TEXT,
        native_language="ur",
        preferred_language="en",
        provider_supports=supports,
    )
    assert decision.action == ACTION_PASSTHROUGH
    assert decision.reason == REASON_UNSUPPORTED
    assert calls == [("ur", "en")]  # consulted exactly once, for the translation


def test_decide_regional_native_language_subtag_is_reduced() -> None:
    engine = DecisionEngine()
    decision = engine.decide(
        _detection("ur"),
        text=URDU_TEXT,
        native_language="ur-PK",
        preferred_language="en",
        provider_supports=supports_all,
    )
    assert decision.action == ACTION_TRANSLATE
    assert decision.reason == REASON_NATIVE


def test_decide_fallback_source_is_gated_like_whisper() -> None:
    """A fallback pin (uncertain detection) must not translate — the safety
    rule dominates: no incorrect translation when uncertain."""
    engine = DecisionEngine()
    decision = engine.decide(
        DetectionResult("ur", 0.40, DETECTION_FALLBACK),
        text="some words",
        native_language="ur",
        preferred_language="en",
        provider_supports=supports_never,
    )
    assert decision.action == ACTION_PASSTHROUGH
    assert decision.reason == REASON_LOW_CONFIDENCE


def test_decide_cluster_source_is_trusted_above_threshold() -> None:
    """The cluster resolution already implies a whisper label at/above the
    threshold — the native speech translates (Rule 1)."""
    engine = DecisionEngine()
    decision = engine.decide(
        DetectionResult("ur", 0.775, DETECTION_CLUSTER),
        text=URDU_TEXT,
        native_language="ur",
        preferred_language="en",
        provider_supports=supports_all,
    )
    assert decision.action == ACTION_TRANSLATE
    assert decision.reason == REASON_NATIVE


@pytest.mark.parametrize(("language", "probability"), [("ur", 0.59), ("en", 0.59)])
def test_decide_confidence_boundary(language: str, probability: float) -> None:
    engine = DecisionEngine(confidence_threshold=0.60)
    decision = engine.decide(
        _detection(language, probability),
        text="some words",
        native_language="ur",
        preferred_language="en",
        provider_supports=supports_never,
    )
    assert decision.reason == REASON_LOW_CONFIDENCE


def test_pinned_detection_result_is_trusted() -> None:
    """Pinned mode builds DetectionResult(native, 1.0, pinned) — Rule 1."""
    engine = DecisionEngine()
    decision = engine.decide(
        DetectionResult("ur", 1.0, DETECTION_PINNED),
        text=URDU_TEXT,
        native_language="ur",
        preferred_language="en",
        provider_supports=supports_all,
    )
    assert decision.action == ACTION_TRANSLATE
    assert decision.reason == REASON_NATIVE
