"""Deterministic fake translation provider — tests and out-of-the-box demos.

The fake provider makes the FULL decision-engine pipeline observable without
any network or model: every translation is a clearly-labeled marker
(`[fake:ur→en] …`), so tests can assert that the pipeline ran, in which
direction, and through which provider — while never pretending to be a real
MT engine (honesty rule: no fake text is ever presented as a translation).
"""

from __future__ import annotations

import time

from .base import (
    TranslationProvider,
    TranslationUnavailableError,
)


class FakeTranslationProvider(TranslationProvider):
    name = "fake"

    def __init__(
        self,
        *,
        supported: frozenset[tuple[str, str]] | None = None,
        fail: bool = False,
        delay_s: float = 0.0,
    ) -> None:
        self._supported = supported  # None → every pair is supported
        self._fail = bool(fail)
        self._delay_s = float(delay_s)
        self.calls: list[tuple[str, str, str]] = []

    def supports(self, source_language: str, target_language: str) -> bool:
        if self._supported is None:
            return True
        return (source_language, target_language) in self._supported

    def translate(self, text: str, source_language: str, target_language: str) -> str:
        self.calls.append((text, source_language, target_language))
        if self._fail:
            raise TranslationUnavailableError("fake provider configured to fail")
        if self._delay_s > 0:
            time.sleep(self._delay_s)
        return f"[fake:{source_language}\u2192{target_language}] {text}"

    def describe(self) -> dict[str, object]:
        return {**super().describe(), "mode": "deterministic-stub"}
