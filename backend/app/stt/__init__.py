"""STT provider factory — the single place that maps settings to a provider."""

from __future__ import annotations

from ..config import Settings
from .base import STTProvider, TranscriptionResult
from .faster_whisper_stt import FasterWhisperSTT

__all__ = ["STTProvider", "TranscriptionResult", "build_stt_provider"]


def build_stt_provider(settings: Settings) -> STTProvider:
    if settings.stt_provider != "faster-whisper":
        raise ValueError(f"unknown STT provider: {settings.stt_provider!r}")
    return FasterWhisperSTT(
        model_id=settings.stt_model,
        device=settings.stt_device,
        compute_type=settings.stt_compute_type,
        low_confidence_threshold=settings.low_confidence_threshold,
        beam_size=settings.stt_beam_size,
        vad_filter=settings.stt_vad_filter,
        vad_min_silence_ms=settings.stt_vad_min_silence_ms,
        vad_speech_pad_ms=settings.stt_vad_speech_pad_ms,
    )
