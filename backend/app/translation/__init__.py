"""Translation package (Phase 3, ADR-0012) — decision engine + providers.

`build_translation_service(settings)` is the single wiring point used by
`app.main.create_app`; `TRANSLATION_PROVIDER` selects the implementation:

- `local` (default) — offline CTranslate2 OPUS-MT models under
  `models/translation/<src>-<tgt>`; provision with
  `backend/scripts/download_translation_models.py`. No keys, no network.
- `fake`  — deterministic stub (`[fake:src→tgt] …`), for tests/demos.
- `libre` — LibreTranslate HTTP API (`LIBRETRANSLATE_URL` + optional key).
- `none`  — translation disabled: no decisions are computed, transcripts
  keep the exact Phase 1/2 shape.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..config import Settings
from .base import TranslationProvider
from .decision import DecisionEngine
from .engine import TranslationOutcome, TranslationService
from .fake import FakeTranslationProvider
from .libre import LibreTranslateProvider
from .local_ct2 import LocalCT2Provider

_PROJECT_ROOT = Path(__file__).resolve().parents[3]  # app/translation/__init__.py → repo root

logger = logging.getLogger(__name__)

__all__ = [
    "DecisionEngine",
    "LocalCT2Provider",
    "TranslationOutcome",
    "TranslationService",
    "build_translation_service",
]


def build_translation_service(settings: Settings) -> TranslationService:
    """Create the app-wide TranslationService from settings (never raises for
    provider problems — misconfiguration degrades to disabled + error log)."""
    name = (settings.translation_provider or "none").strip().lower()
    provider: TranslationProvider | None = None

    if name == "fake":
        provider = FakeTranslationProvider()
    elif name == "libre":
        url = settings.libretranslate_url.strip()
        if url:
            provider = LibreTranslateProvider(
                url=url,
                api_key=settings.libretranslate_api_key or None,
            )
        else:
            logger.error(
                "TRANSLATION_PROVIDER=libre but LIBRETRANSLATE_URL is empty — "
                "translation disabled (set the URL or pick another provider)"
            )
    elif name == "local":
        root = Path(settings.translation_models_dir)
        if not root.is_absolute():
            root = _PROJECT_ROOT / root
        provider = LocalCT2Provider(root)
        if not provider.pairs:
            logger.warning(
                "no local MT models under %s — run "
                "backend/scripts/download_translation_models.py; translation "
                "decisions will answer unsupported_pair (pass-through)",
                root,
            )
    elif name != "none":
        logger.error(
            "unknown TRANSLATION_PROVIDER %r (expected local|fake|libre|none) "
            "— translation disabled",
            name,
        )

    service = TranslationService(
        provider=provider,
        decision_engine=DecisionEngine(
            confidence_threshold=settings.translation_confidence_threshold,
            max_text_chars=settings.translation_max_text_chars,
        ),
        detect_mode=settings.translation_detect_mode,
        timeout_s=settings.translation_timeout_s,
        rate_limit_per_minute=settings.translation_rate_limit_per_minute,
        max_concurrent=settings.translation_max_concurrent,
        worker_threads=settings.translation_worker_threads,
    )
    logger.info(
        "translation service built provider=%s detect_mode=%s enabled=%s",
        provider.name if provider is not None else None,
        service.detect_mode,
        service.enabled,
    )
    return service
