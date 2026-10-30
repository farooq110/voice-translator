"""faster-whisper (CTranslate2) CPU STT provider with language forcing.

Why faster-whisper (see docs/decisions/0001-stt-engine.md):
- pip-installable, no GPU required (int8 quantization on CPU);
- Whisper's 90+ language coverage (Urdu, English, …);
- actively maintained, good CPU latency characteristics.

Language handling (KI-002 / KI-006, see docs/decisions/0006):
- When a `language_hint` is passed (Phase 1: the translator user's configured
  native language), it is FORCED for decoding. Whisper's automatic detection
  confuses short Hindustani-register utterances (ur/hi/fa) and — on noisy or
  silent audio — hallucinates random languages and digit strings. The user's
  explicit selection is authoritative, deterministic and also faster: forcing
  the language skips the full detection pass over the audio.
- Segments that look like classic no-speech hallucinations (high
  `no_speech_prob` AND poor `avg_logprob` — the same rule OpenAI Whisper
  applies) are dropped instead of being surfaced as "random numbers".
- Decoding is greedy with a single temperature (no fallback-retry ladder),
  which bounds worst-case latency for noisy utterances.

The model is loaded lazily (first `ensure_loaded()` call, thread-safe) so the
event loop never blocks on downloads or model initialization.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

import numpy as np

from ..audio.pcm import pcm16_bytes_to_float32
from .base import STTProvider, TranscriptionResult

if TYPE_CHECKING:
    from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)

# Whisper's own no-speech filter: drop a segment only when BOTH hold. This
# removes the classic silence/noise hallucinations ("12, 34, 56 …") without
# discarding quiet-but-real speech.
NO_SPEECH_PROB_THRESHOLD = 0.6
AVG_LOGPROB_THRESHOLD = -1.0

# Dead-audio gate: peak amplitude below ≈ -80 dBFS is not speech — decoding
# it only invites hallucinations (measured: 2 s of pure zeros produced a
# 447-character pseudo-Urdu string and 1.6 s of CPU on this model). Such
# buffers are rejected before the model is ever invoked.
DEAD_AUDIO_PEAK_THRESHOLD = 1e-4


class FasterWhisperSTT(STTProvider):
    name = "faster-whisper"

    def __init__(
        self,
        *,
        model_id: str,
        device: str,
        compute_type: str,
        low_confidence_threshold: float = 0.60,
    ) -> None:
        super().__init__(
            model_id=model_id, device=device, low_confidence_threshold=low_confidence_threshold
        )
        self.compute_type = compute_type
        self._model: WhisperModel | None = None
        self._load_lock = threading.Lock()
        # CTranslate2 handles concurrent runs, but CPU inference is serialized
        # anyway by the executor; the lock is belt-and-braces for direct use.
        self._infer_lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def ensure_loaded(self) -> None:
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is not None:  # double-checked after blocking acquire
                return
            from faster_whisper import WhisperModel  # heavy import deferred

            logger.info(
                "loading faster-whisper model=%s device=%s compute_type=%s",
                self.model_id,
                self.device,
                self.compute_type,
            )
            self._model = WhisperModel(
                self.model_id, device=self.device, compute_type=self.compute_type
            )
            logger.info("faster-whisper model loaded")

    @staticmethod
    def normalize_hint(language_hint: str | None) -> str | None:
        """"ur-PK" → "ur" (Whisper wants the primary ISO-639 subtag)."""
        if not language_hint:
            return None
        primary = language_hint.split("-", 1)[0].strip().lower()
        return primary or None

    def transcribe_pcm(
        self,
        pcm16: bytes,
        *,
        sample_rate: int,
        channels: int = 1,
        language_hint: str | None = None,
    ) -> TranscriptionResult:
        self.ensure_loaded()
        model = self._model
        assert model is not None, "ensure_loaded() must initialize the model"
        audio = pcm16_bytes_to_float32(pcm16, sample_rate=sample_rate, channels=channels)
        duration_s = len(audio) / 16_000 if len(audio) else 0.0

        forced_language = self.normalize_hint(language_hint)

        # Dead-audio gate (before the model — see DEAD_AUDIO_PEAK_THRESHOLD).
        if audio.size == 0 or float(np.abs(audio).max()) < DEAD_AUDIO_PEAK_THRESHOLD:
            logger.info(
                "stt.dead_audio_skipped duration_s=%.2f hint=%s", duration_s, forced_language
            )
            return TranscriptionResult(
                text="",
                language=forced_language or "unknown",
                language_probability=1.0 if forced_language else 0.0,
                audio_duration_s=duration_s,
            )

        try:
            text, language, probability = self._run_transcribe(
                model, audio, forced_language=forced_language
            )
        except ValueError:
            if forced_language is None:
                raise
            # Unsupported/invalid hint — never fail the utterance; retry once
            # with automatic detection so the user still gets a transcript.
            logger.warning(
                "language hint %r rejected by the model — falling back to auto-detection",
                forced_language,
            )
            text, language, probability = self._run_transcribe(
                model, audio, forced_language=None
            )

        return TranscriptionResult(
            text=text,
            language=language or "unknown",
            language_probability=probability,
            audio_duration_s=duration_s,
        )

    def _run_transcribe(
        self,
        model: WhisperModel,
        audio,
        *,
        forced_language: str | None,
    ) -> tuple[str, str | None, float]:
        """One decode pass. Returns (text, language, language_probability)."""
        with self._infer_lock:
            segments, info = model.transcribe(
                audio,
                language=forced_language,  # None → automatic detection
                task="transcribe",
                beam_size=1,  # greedy decoding — lowest CPU latency
                temperature=0.0,  # single pass: no temperature-retry ladder
                condition_on_previous_text=False,  # no cross-utterance context bleed
                vad_filter=False,  # VAD is Phase 2 scope
                without_timestamps=True,
                log_progress=False,
            )
            parts = [
                seg.text.strip()
                for seg in segments
                if seg.text
                and seg.text.strip()
                and not (
                    seg.no_speech_prob > NO_SPEECH_PROB_THRESHOLD
                    and seg.avg_logprob < AVG_LOGPROB_THRESHOLD
                )
            ]
            language = info.language
            # An explicitly forced language is a user decision, not a guess.
            probability = 1.0 if forced_language else float(info.language_probability or 0.0)

        text = " ".join(parts).strip()
        return text, language, probability
