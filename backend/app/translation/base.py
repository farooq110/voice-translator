"""Translation provider abstraction — the replaceable interface (Phase 3).

Mirrors the STT provider pattern (app/stt/base.py): the decision engine and
the WebSocket layer depend only on this module, never on a concrete provider
(master prompt §10 — "keep provider implementations modular").

Shipped implementations:
- `fake.FakeTranslationProvider` — deterministic stub for tests and demos
- `local_ct2.LocalCT2Provider` — offline CTranslate2 OPUS-MT models (default)
- `libre.LibreTranslateProvider` — LibreTranslate HTTP API (self-host/key)

All provider failures are `TranslationProviderError` subclasses: they must
never kill a WebSocket connection or lose the original transcript — the
utterance simply keeps its original text (the client is told via a
non-fatal, utterance-scoped error).
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class TranslationProviderError(Exception):
    """Base class for translation provider failures (never fatal)."""


class UnsupportedLanguagePairError(TranslationProviderError):
    """The provider cannot translate this language pair."""


class TranslationRequestError(TranslationProviderError):
    """The provider rejected the request (bad input, 4xx)."""


class TranslationUnavailableError(TranslationProviderError):
    """The provider is unreachable, unhealthy (5xx/429) or malformed."""


class TranslationProvider(ABC):
    """A text translation engine for a set of language pairs."""

    name: str = "abstract"

    def ensure_loaded(self) -> None:
        """Warm the provider (may block; run in a worker). No-op by default."""

    @abstractmethod
    def supports(self, source_language: str, target_language: str) -> bool:
        """Whether this provider can translate `source_language -> target_language`.

        The decision engine consults this BEFORE calling the provider so an
        unsupported pair becomes a pass-through (master prompt Rule 4 — avoid
        unnecessary provider calls) instead of a runtime error.
        """

    @abstractmethod
    def translate(self, text: str, source_language: str, target_language: str) -> str:
        """Translate `text`. Blocking — run in the translation executor.

        Implementations must raise only `TranslationProviderError` subclasses.
        Text is passed through Unicode-untouched (Urdu RTL content is the
        provider's output responsibility; the engine never rewrites it).
        """

    def describe(self) -> dict[str, object]:
        return {"provider": self.name}
