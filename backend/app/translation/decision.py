"""Language-detection resolution + translation decision rules (Phase 3).

Implements the master prompt §3/§4 rules EXACTLY, for any configured pair
(nothing is hardcoded to Urdu/English):

    IF detected == native:      translate → preferred   (Rule 1)
    ELIF detected == preferred: translate → native      (Rule 2)
    ELSE:                       pass-through, no provider call, no TTS (Rules 3+4)

Detection architecture (ADR-0012 v2, "detect-first, pin-the-decode"):

Whisper's FULL auto-decode is both unreliable and slow on Hindustani speech
(KI-002/KI-006: ur mislabeled `hi`/`ar`, mixed Arabic/Devanagari output,
3–4× decode time). The production flow therefore NEVER auto-decodes:

1. `detect_language(audio)` — the standalone encoder-only pass (measured:
   en p=0.997, es p=0.989, ur→hi p=0.775 — precisely KI-002);
2. `resolve_stt_language()` normalizes that label — ur/hi cluster collapse
   toward the configured language, confidence gate — and returns the one
   language the SINGLE decode is pinned to (native when uncertain: the
   KI-006 safe default);
3. the decode is FORCED to that language (Phase 1/2 text quality and speed);
4. `decide()` applies the §3 rules to the resolved detection.

`pinned` detection mode (deployment choice, Phase 1/2 behavior): no detect
pass at all — the hello config's native language is authoritative for every
utterance. Faster and deterministic, but Rule 2 (the other participant's
preferred-language speech) can never fire. `auto` is the Phase 3 default.

Session context tracking was considered and deliberately dropped: the
master prompt's safety rule ("do not generate an incorrect translation";
pass-through when uncertain) dominates any guess a previous-utterance
memory could produce, and the single-mic MVP has exactly one stream.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- Script ranges (sufficient for the gate; no heavy dependencies) --------
_ARABIC_RANGES: tuple[tuple[int, int], ...] = (
    (0x0600, 0x06FF),  # Arabic
    (0x0750, 0x077F),  # Arabic Supplement
    (0x08A0, 0x08FF),  # Arabic Extended-A
    (0xFB50, 0xFDFF),  # Arabic Presentation Forms-A
    (0xFE70, 0xFEFF),  # Arabic Presentation Forms-B
)
_DEVANAGARI_RANGES: tuple[tuple[int, int], ...] = ((0x0900, 0x097F),)
_LATIN_RANGES: tuple[tuple[int, int], ...] = ((0x0041, 0x005A), (0x0061, 0x007A))


def _script_ratio(text: str, ranges: tuple[tuple[int, int], ...]) -> float:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return 0.0
    hits = sum(1 for ch in letters if any(lo <= ord(ch) <= hi for lo, hi in ranges))
    return hits / len(letters)


def script_profile(text: str) -> dict[str, float]:
    """Fraction of alphabetic characters per script (arabic/devanagari/latin)."""
    return {
        "arabic": _script_ratio(text, _ARABIC_RANGES),
        "devanagari": _script_ratio(text, _DEVANAGARI_RANGES),
        "latin": _script_ratio(text, _LATIN_RANGES),
    }


def primary_subtag(code: str) -> str:
    """BCP-47 primary subtag, normalized ("ur-PK" -> "ur")."""
    return (code or "").split("-", 1)[0].strip().lower()


# Languages whose SPEECH Whisper routinely confuses (KI-002): Urdu and Hindi
# are registers of the same spoken language (Hindustani). Written script is
# what actually differs — hence the script override above.
LANGUAGE_CLUSTERS: dict[str, frozenset[str]] = {
    "ur": frozenset({"ur", "hi"}),
    "hi": frozenset({"ur", "hi"}),
}

# (whisper label, script key in script_profile, corrected label, min ratio)
# Script overrides over the transcription TEXT are deliberately NOT part of
# the v2 flow (the decode is already pinned before text exists — see the
# module docstring). script_profile() remains available for tests/probes.

DETECTION_WHISPER = "whisper"
DETECTION_CLUSTER = "cluster"
DETECTION_PINNED = "pinned"
DETECTION_FALLBACK = "fallback"
DETECTION_UNKNOWN = "unknown"

# Decision reasons (protocol-visible — see TranslationDecisionInfo).
REASON_NATIVE = "native_language"
REASON_PREFERRED = "preferred_language"
REASON_THIRD = "third_language"
REASON_IDENTICAL = "identical_config"
REASON_LOW_CONFIDENCE = "low_confidence"
REASON_UNDETECTED = "undetected_language"
REASON_UNSUPPORTED = "unsupported_pair"
REASON_TOO_LONG = "text_too_long"

ACTION_TRANSLATE = "translate"
ACTION_PASSTHROUGH = "passthrough"


@dataclass(frozen=True)
class DetectionResult:
    language: str  # normalized primary subtag; "" when nothing was detected
    probability: float  # 0..1
    source: str  # whisper | script | cluster | pinned | unknown


@dataclass(frozen=True)
class TranslationDecision:
    action: str  # "translate" | "passthrough"
    reason: str  # one of the REASON_* values
    source_language: str  # detected language ("" when undetected)
    target_language: str | None  # set only for translate
    detection: str  # DetectionResult.source (protocol-visible)


class DecisionEngine:
    """Pure decision logic — no I/O, no provider calls (Rules 3+4 by design)."""

    def __init__(
        self,
        *,
        confidence_threshold: float = 0.60,
        max_text_chars: int = 2000,
    ) -> None:
        self.confidence_threshold = float(confidence_threshold)
        self.max_text_chars = int(max_text_chars)

    # -- detection resolution (ADR-0012 v2) ----------------------------------

    def resolve_stt_language(
        self,
        raw_label: str | None,
        raw_probability: float,
        *,
        native_language: str,
    ) -> DetectionResult:
        """Resolve the standalone detect_language result into the language the
        SINGLE decode is pinned to.

        - whisper label == native → native (`whisper`);
        - cluster sibling label ("hi" when native is "ur") → native
          (`cluster`, KI-002);
        - anything else at/above the threshold → that label (`whisper`) —
          the other participant / a third language really did speak;
        - anything else below the threshold, or no label → native
          (`fallback`, probability carried for the decision gate) — the
          KI-006 safe default: decode as the configured native language and
          let the decision engine answer low_confidence pass-through.
        """
        native = primary_subtag(native_language)
        label = primary_subtag(raw_label or "") or None
        probability = max(0.0, min(1.0, float(raw_probability or 0.0)))

        if label is None:
            return DetectionResult(native, probability, DETECTION_FALLBACK)
        if label == native:
            return DetectionResult(native, probability, DETECTION_WHISPER)
        cluster = LANGUAGE_CLUSTERS.get(native)
        if cluster is not None and label in cluster and native in cluster:
            return DetectionResult(native, probability, DETECTION_CLUSTER)
        if probability >= self.confidence_threshold:
            return DetectionResult(label, probability, DETECTION_WHISPER)
        return DetectionResult(native, probability, DETECTION_FALLBACK)

    # -- decision ------------------------------------------------------------

    def decide(
        self,
        detection: DetectionResult,
        *,
        text: str,
        native_language: str,
        preferred_language: str,
        provider_supports,
    ) -> TranslationDecision:
        """Apply the master prompt §3 rules. `provider_supports(src, tgt)` is
        consulted ONLY for would-be translations (never for pass-through)."""
        native = primary_subtag(native_language)
        preferred = primary_subtag(preferred_language)
        detected = detection.language

        if native == preferred:
            return TranslationDecision(
                ACTION_PASSTHROUGH, REASON_IDENTICAL, detected, None, detection.source
            )
        if len(text) > self.max_text_chars:
            return TranslationDecision(
                ACTION_PASSTHROUGH, REASON_TOO_LONG, detected, None, detection.source
            )
        if not detected or detection.source == DETECTION_UNKNOWN:
            return TranslationDecision(
                ACTION_PASSTHROUGH, REASON_UNDETECTED, "", None, DETECTION_UNKNOWN
            )
        if (
            detection.source in (DETECTION_WHISPER, DETECTION_FALLBACK)
            and detection.probability < self.confidence_threshold
        ):
            return TranslationDecision(
                ACTION_PASSTHROUGH, REASON_LOW_CONFIDENCE, detected, None, detection.source
            )

        if detected == native:
            reason, target = REASON_NATIVE, preferred
        elif detected == preferred:
            reason, target = REASON_PREFERRED, native
        else:
            return TranslationDecision(
                ACTION_PASSTHROUGH, REASON_THIRD, detected, None, detection.source
            )

        if not provider_supports(detected, target):
            return TranslationDecision(
                ACTION_PASSTHROUGH, REASON_UNSUPPORTED, detected, None, detection.source
            )
        return TranslationDecision(
            ACTION_TRANSLATE, reason, detected, target, detection.source
        )
