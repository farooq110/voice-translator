"""Provider tests — fake, LibreTranslate (mocked HTTP semantics), local CT2.

The LibreTranslate provider is exercised against `httpx.MockTransport`, so
the REAL request/response handling (status codes, malformed bodies, network
errors, api_key propagation) is covered without any network. The local CT2
provider is covered by a discovery test plus a REAL translation test
(marked `translation_real`, skipped when the models are not downloaded).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.translation.base import (
    TranslationRequestError,
    TranslationUnavailableError,
)
from app.translation.decision import script_profile
from app.translation.fake import FakeTranslationProvider
from app.translation.libre import LibreTranslateProvider
from app.translation.local_ct2 import LocalCT2Provider

# --------------------------------------------------------------------- fake --


def test_fake_provider_returns_deterministic_marker() -> None:
    provider = FakeTranslationProvider()
    assert provider.supports("ur", "en") is True
    out = provider.translate("سلام", "ur", "en")
    assert out == "[fake:ur\u2192en] سلام"
    assert provider.calls == [("سلام", "ur", "en")]


def test_fake_provider_failure_and_pair_support() -> None:
    provider = FakeTranslationProvider(supported=frozenset({("ur", "en")}))
    assert provider.supports("en", "ur") is False
    failing = FakeTranslationProvider(fail=True)
    with pytest.raises(TranslationUnavailableError):
        failing.translate("x", "ur", "en")


# -------------------------------------------------------------------- libre --


def _libre(handler) -> LibreTranslateProvider:
    return LibreTranslateProvider(
        url="https://libre.example.com",
        api_key="secret-key",
        transport=httpx.MockTransport(handler),
    )


def test_libre_translate_success() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = request.read()
        return httpx.Response(200, json={"translatedText": "Hello world"})

    provider = _libre(handler)
    out = provider.translate("سلام دنیا", "ur", "en")
    assert out == "Hello world"
    import json

    payload = json.loads(seen["payload"])
    assert payload == {
        "q": "سلام دنیا",
        "source": "ur",
        "target": "en",
        "format": "text",
        "api_key": "secret-key",
    }


def test_libre_translate_request_error_maps_400() -> None:
    provider = _libre(
        lambda request: httpx.Response(400, json={"error": "unsupported pair"})
    )
    with pytest.raises(TranslationRequestError, match="unsupported pair"):
        provider.translate("x", "xx", "en")


def test_libre_translate_unavailable_maps_5xx_and_429() -> None:
    for status in (429, 500, 503):
        provider = _libre(lambda request, s=status: httpx.Response(s, text="nope"))
        with pytest.raises(TranslationUnavailableError):
            provider.translate("x", "ur", "en")


def test_libre_translate_network_error_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    provider = _libre(handler)
    with pytest.raises(TranslationUnavailableError, match="boom"):
        provider.translate("x", "ur", "en")


def test_libre_translate_malformed_json_is_unavailable() -> None:
    provider = _libre(lambda request: httpx.Response(200, content=b"not-json{"))
    with pytest.raises(TranslationUnavailableError, match="malformed"):
        provider.translate("x", "ur", "en")


def test_libre_translate_missing_field_is_unavailable() -> None:
    provider = _libre(lambda request: httpx.Response(200, json={"unexpected": 1}))
    with pytest.raises(TranslationUnavailableError, match="translatedText"):
        provider.translate("x", "ur", "en")


def test_libre_provider_requires_url() -> None:
    with pytest.raises(ValueError):
        LibreTranslateProvider(url="  ")


def test_libre_describe_reports_configuration() -> None:
    provider = _libre(lambda request: httpx.Response(200, json={"translatedText": "x"}))
    info = provider.describe()
    assert info["provider"] == "libretranslate"
    assert info["url"] == "https://libre.example.com"
    assert info["api_key"] == "configured"


# --------------------------------------------------------------- local ct2 --


def test_local_provider_discovers_provisioned_pairs(tmp_path: Path) -> None:
    (tmp_path / "ur-en").mkdir()
    (tmp_path / "ur-en" / "model.bin").write_bytes(b"fake")
    (tmp_path / "en-ur").mkdir()
    (tmp_path / "en-ur" / "model.bin").write_bytes(b"fake")
    (tmp_path / "half-done").mkdir()  # no model.bin → ignored
    (tmp_path / "not-a-pair.txt").write_text("x")

    provider = LocalCT2Provider(tmp_path)
    assert provider.pairs == {("ur", "en"), ("en", "ur")}
    assert provider.supports("ur", "en") is True
    assert provider.supports("en", "ur") is True
    assert provider.supports("fr", "en") is False


def test_local_provider_missing_root_has_no_pairs(tmp_path: Path) -> None:
    provider = LocalCT2Provider(tmp_path / "does-not-exist")
    assert provider.pairs == set()
    assert provider.supports("ur", "en") is False


def test_local_provider_unsupported_pair_raises(tmp_path: Path) -> None:
    provider = LocalCT2Provider(tmp_path)
    with pytest.raises(Exception):  # UnsupportedLanguagePairError
        provider.translate("hello", "fr", "en")


PROJECT_ROOT = Path(__file__).resolve().parents[2]  # tests/… → repo root
MODELS_ROOT = PROJECT_ROOT / "models" / "translation"


def _models_present() -> bool:
    return (MODELS_ROOT / "ur-en" / "model.bin").is_file() and (
        MODELS_ROOT / "en-ur" / "model.bin"
    ).is_file()


@pytest.mark.translation_real
@pytest.mark.skipif(not _models_present(), reason="local MT models not downloaded")
class TestRealLocalTranslation:
    """REAL offline translation through the CT2 OPUS-MT models (no mocks)."""

    def test_urdu_to_english(self) -> None:
        provider = LocalCT2Provider(MODELS_ROOT)
        out = provider.translate("السلام علیکم، آج آپ کیسے ہیں؟", "ur", "en")
        assert out.strip()
        assert script_profile(out)["latin"] >= 0.6, f"expected Latin text, got {out!r}"

    def test_english_to_urdu(self) -> None:
        provider = LocalCT2Provider(MODELS_ROOT)
        out = provider.translate("Peace be upon you, how are you today?", "en", "ur")
        assert out.strip()
        assert script_profile(out)["arabic"] >= 0.6, f"expected Arabic script, got {out!r}"

    def test_empty_text_is_rejected(self) -> None:
        provider = LocalCT2Provider(MODELS_ROOT)
        from app.translation.base import TranslationProviderError

        with pytest.raises(TranslationProviderError):
            provider.translate("   ", "ur", "en")
