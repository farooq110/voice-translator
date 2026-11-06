"""Deterministic sine-tone TTS provider — for tests and demos (no model data).

The tone is seeded from (language, text), so the output is reproducible:
the same utterance always yields the same PCM. Duration scales with text
length (bounded) — enough for tests to assert real, non-silent, correctly
sized audio and for demos to prove the playback path end-to-end without
provisioning any voice models.
"""

from __future__ import annotations

import math
import zlib

import numpy as np

from .base import (
    SynthesisRequestError,
    SynthesizedAudio,
    TTSProvider,
)

_SAMPLE_RATE = 22_050
_MS_PER_CHAR = 60
_MIN_MS = 150
_MAX_MS = 5_000
_AMPLITUDE = 0.25  # full-scale; RMS ≈ 0.177 for a sine


def primary_subtag(language: str) -> str:
    """Normalize a BCP-47 tag to its primary subtag ("ur-PK" → "ur")."""
    return language.split("-", 1)[0].strip().lower()


class FakeTTSProvider(TTSProvider):
    name = "fake"

    def __init__(
        self,
        *,
        languages: tuple[str, ...] = ("en", "ur", "es"),
        sample_rate: int = _SAMPLE_RATE,
        frequency_hz: float = 440.0,
        amplitude: float = _AMPLITUDE,
        fail: bool = False,
    ) -> None:
        self.languages = frozenset(languages)
        self.sample_rate = int(sample_rate)
        self.frequency_hz = float(frequency_hz)
        self.amplitude = float(amplitude)
        self.fail = fail  # raise on every synthesis (failure-path tests)
        self.calls = 0

    def supports(self, language: str) -> bool:
        return primary_subtag(language) in self.languages

    def describe(self) -> dict[str, object]:
        return {**super().describe(), "languages": sorted(self.languages)}

    def synthesize(self, text: str, language: str) -> SynthesizedAudio:
        self.calls += 1
        if self.fail:
            raise SynthesisRequestError("fake synthesis failure")
        if not text.strip():
            raise SynthesisRequestError("empty text")
        duration_ms = min(_MAX_MS, max(_MIN_MS, len(text) * _MS_PER_CHAR))
        n = int(self.sample_rate * duration_ms / 1000)
        # Deterministic per (language, text): a phase offset derived from the
        # content keeps repeated syntheses reproducible across runs.
        seed = zlib.crc32(f"{primary_subtag(language)}:{text}".encode("utf-8"))
        phase = (seed % 1000) / 1000.0 * 2 * math.pi
        t = np.arange(n, dtype=np.float64) / self.sample_rate
        wave = self.amplitude * np.sin(2 * math.pi * self.frequency_hz * t + phase)
        pcm = (wave * 32767.0).astype("<i2").tobytes()
        return SynthesizedAudio(pcm=pcm, sample_rate=self.sample_rate)
