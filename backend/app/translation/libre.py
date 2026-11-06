"""LibreTranslate HTTP provider — the production cloud path (Phase 3).

Works with any LibreTranslate instance (self-hosted or public) with or
without an API key:

    TRANSLATION_PROVIDER=libre
    LIBRETRANSLATE_URL=https://libretranslate.example.com
    LIBRETRANSLATE_API_KEY=…            # optional, instance-dependent

Implementation notes:
- synchronous `httpx.Client` — calls run on the translation executor thread,
  never on the event loop (same pattern as the STT provider);
- the HTTP transport is injectable so tests exercise REAL request/response
  semantics (timeouts, status codes, malformed bodies) via
  `httpx.MockTransport` without any network;
- errors map to the `TranslationProviderError` hierarchy; nothing here is
  ever fatal to a WebSocket session.
"""

from __future__ import annotations

import httpx

from .base import (
    TranslationProvider,
    TranslationRequestError,
    TranslationUnavailableError,
)


class LibreTranslateProvider(TranslationProvider):
    name = "libretranslate"

    def __init__(
        self,
        *,
        url: str,
        api_key: str | None = None,
        transport: httpx.BaseTransport | None = None,
        request_timeout_s: float = 8.0,
    ) -> None:
        if not url or not url.strip():
            raise ValueError("LibreTranslateProvider requires a non-empty url")
        self._api_key = (api_key or "").strip() or None
        self._client = httpx.Client(
            base_url=url.strip().rstrip("/"),
            timeout=httpx.Timeout(request_timeout_s),
            transport=transport,
            headers={"User-Agent": "ai-voice-translator/0.3"},
        )

    def supports(self, source_language: str, target_language: str) -> bool:
        # LibreTranslate serves its language list via GET /languages; probing
        # it per utterance would add a round trip per decision. The endpoint
        # is assumed to serve the configured pair — a rejection surfaces as a
        # `translation_failed` error on the utterance, never as a crash.
        return True

    def translate(self, text: str, source_language: str, target_language: str) -> str:
        payload: dict[str, str] = {
            "q": text,
            "source": source_language,
            "target": target_language,
            "format": "text",
        }
        if self._api_key:
            payload["api_key"] = self._api_key
        try:
            response = self._client.post("/translate", json=payload)
        except httpx.HTTPError as exc:
            raise TranslationUnavailableError(f"libretranslate unreachable: {exc}") from exc

        if response.status_code == 400:
            detail = self._error_detail(response)
            raise TranslationRequestError(f"libretranslate rejected the request: {detail}")
        if response.status_code in (429, 500, 502, 503):
            raise TranslationUnavailableError(
                f"libretranslate unhealthy (HTTP {response.status_code})"
            )
        if response.status_code != 200:
            raise TranslationRequestError(
                f"libretranslate unexpected status {response.status_code}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise TranslationUnavailableError("libretranslate returned malformed JSON") from exc
        translated = data.get("translatedText") if isinstance(data, dict) else None
        if not isinstance(translated, str):
            raise TranslationUnavailableError("libretranslate response missing translatedText")
        return translated

    @staticmethod
    def _error_detail(response: httpx.Response) -> str:
        try:
            body = response.json()
            if isinstance(body, dict) and isinstance(body.get("error"), str):
                return body["error"]
        except ValueError:
            pass
        return response.text[:120] or "no detail"

    def describe(self) -> dict[str, object]:
        info = super().describe()
        info["url"] = str(self._client.base_url)
        info["api_key"] = "configured" if self._api_key else "none"
        return info
