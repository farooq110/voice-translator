"""STT provider abstraction — a replaceable interface (master prompt 13E).

Phase 1 ships one implementation (faster-whisper on CPU). Translation and TTS
provider interfaces arrive in Phases 3/4 behind the same pattern.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    language: str  # ISO-639-1 code as reported by the model, or "unknown"
    language_probability: float  # 0..1 (0 when unknown)
    audio_duration_s: float


class STTProvider(ABC):
    """A speech-to-text engine with automatic source-language detection."""

    name: str = "abstract"

    def __init__(
        self,
        *,
        model_id: str,
        device: str,
        low_confidence_threshold: float = 0.60,
    ) -> None:
        self.model_id = model_id
        self.device = device
        self.low_confidence_threshold = low_confidence_threshold

    @abstractmethod
    def ensure_loaded(self) -> None:
        """Load (and if needed download) the model. May block; run in a worker."""

    @property
    @abstractmethod
    def loaded(self) -> bool:
        """Whether the model is loaded and ready for inference."""

    @abstractmethod
    def transcribe_pcm(
        self,
        pcm16: bytes,
        *,
        sample_rate: int,
        channels: int = 1,
        language_hint: str | None = None,
    ) -> TranscriptionResult:
        """Transcribe PCM16 LE audio.

        `language_hint` (ISO-639-1, primary subtag) pins the engine to the
        translator user's configured native language when provided — Phase 1
        always passes it, because the user's selection is authoritative and
        auto-detection mislabels short utterances (KI-002/KI-006). `None`
        falls back to automatic language detection (used by real-model tests
        and, later, by Phase 3 for the *other* participant's audio).
        """

    def is_low_confidence(self, probability: float) -> bool:
        return probability < self.low_confidence_threshold

    def describe(self) -> dict[str, object]:
        return {
            "provider": self.name,
            "model": self.model_id,
            "device": self.device,
            "loaded": self.loaded,
        }
