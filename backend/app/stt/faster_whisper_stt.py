"""faster-whisper (CTranslate2) CPU STT provider with language detection.

Why faster-whisper (see docs/decisions/0001-stt-engine.md):
- pip-installable, no GPU required (int8 quantization on CPU);
- Whisper's built-in language detection over 90+ languages (Urdu, English, …);
- actively maintained, good CPU latency characteristics.

The model is loaded lazily (first `ensure_loaded()` call, thread-safe) so the
event loop never blocks on downloads or model initialization.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from ..audio.pcm import pcm16_bytes_to_float32
from .base import STTProvider, TranscriptionResult

if TYPE_CHECKING:
    from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)


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

    def transcribe_pcm(
        self, pcm16: bytes, *, sample_rate: int, channels: int = 1
    ) -> TranscriptionResult:
        self.ensure_loaded()
        model = self._model
        assert model is not None, "ensure_loaded() must initialize the model"
        audio = pcm16_bytes_to_float32(pcm16, sample_rate=sample_rate, channels=channels)
        duration_s = len(audio) / 16_000 if len(audio) else 0.0

        with self._infer_lock:
            segments, info = model.transcribe(
                audio,
                language=None,  # automatic source-language detection
                task="transcribe",
                beam_size=1,  # greedy decoding — lowest CPU latency
                condition_on_previous_text=False,  # no cross-utterance context bleed
                vad_filter=False,  # VAD is Phase 2 scope
                without_timestamps=True,
                log_progress=False,
            )
            parts = [seg.text.strip() for seg in segments if seg.text and seg.text.strip()]
            language = info.language
            probability = float(info.language_probability or 0.0)

        text = " ".join(parts).strip()
        return TranscriptionResult(
            text=text,
            language=language or "unknown",
            language_probability=probability,
            audio_duration_s=duration_s,
        )
