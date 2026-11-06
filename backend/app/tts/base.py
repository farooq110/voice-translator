"""TTS provider abstraction — the replaceable interface (Phase 4, ADR-0013).

Mirrors the translation provider pattern (app/translation/base.py): the
WebSocket layer and the TTSService depend only on this module, never on a
concrete provider (master prompt §10 — "keep provider implementations
modular").

Shipped implementations:
- `fake.FakeTTSProvider` — deterministic sine-tone stub for tests and demos
- `piper_local.PiperTTSProvider` — offline Piper neural voices (default)

All provider failures are `TTSProviderError` subclasses: they must never kill
a WebSocket connection — the translated text is already on screen; the audio
is an enhancement whose absence is reported as a non-fatal, utterance-scoped
error.

Non-negotiable (master prompt §17): original audio pass-through must bypass
TTS — the WebSocket layer only ever calls the service for `translate`
decisions, never for pass-through.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class TTSProviderError(Exception):
    """Base class for TTS provider failures (never fatal)."""


class UnsupportedLanguageError(TTSProviderError):
    """The provider has no voice for this language."""


class SynthesisRequestError(TTSProviderError):
    """The provider rejected the request (bad input)."""


class SynthesisUnavailableError(TTSProviderError):
    """The provider is unhealthy or malformed (voice files, runtime)."""


@dataclass(frozen=True)
class SynthesizedAudio:
    """One synthesis result: raw PCM16 little-endian mono samples."""

    pcm: bytes
    sample_rate: int

    @property
    def duration_ms(self) -> int:
        if self.sample_rate <= 0:
            return 0
        return int(len(self.pcm) / 2 / self.sample_rate * 1000)


class TTSProvider(ABC):
    """A speech synthesis engine for a set of languages."""

    name: str = "abstract"

    def ensure_loaded(self) -> None:
        """Warm the provider (may block; run in a worker). No-op by default."""

    @abstractmethod
    def supports(self, language: str) -> bool:
        """Whether this provider can speak `language` (primary subtag, "ur-PK" → "ur").

        The WebSocket layer consults this BEFORE synthesizing so a missing
        voice becomes a visible non-fatal condition instead of a runtime
        error (honest degradation — mirrors translation's unsupported_pair).
        """

    @abstractmethod
    def synthesize(self, text: str, language: str) -> SynthesizedAudio:
        """Synthesize `text` spoken in `language`. Blocking — run in the TTS
        executor.

        Implementations must raise only `TTSProviderError` subclasses. The
        text is never rewritten, truncated or re-cased by the provider —
        Unicode/RTL content is the voice engine's responsibility.
        """

    def describe(self) -> dict[str, object]:
        return {"provider": self.name}
