"""TTS package (Phase 4, ADR-0013) — offline speech synthesis + providers.

`build_tts_service(settings)` is the single wiring point used by
`app.main.create_app`; `TTS_PROVIDER` selects the implementation:

- `piper` (default) — offline Piper neural voices under `tts_voices_dir`
  (one directory per language, e.g. `models/tts/ur/`); provision with
  `backend/scripts/download_tts_voices.py`. No keys, no GPU, no network.
- `fake`  — deterministic sine-tone stub (`FakeTTSProvider`) for tests/demos.
- `none`  — TTS disabled: translated text is displayed, never spoken (the
  protocol keeps the exact Phase 3 shape — no `audio` events, no binary
  frames).

Master prompt rule (§17): original audio pass-through NEVER reaches TTS —
enforced in the WebSocket layer (only `translate` decisions synthesize).
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..config import Settings
from .base import TTSProvider
from .fake import FakeTTSProvider, primary_subtag
from .piper_local import PiperTTSProvider
from .service import TTSService, TTSOutcome

_PROJECT_ROOT = Path(__file__).resolve().parents[3]  # app/tts/__init__.py → repo root

logger = logging.getLogger(__name__)

__all__ = [
    "FakeTTSProvider",
    "PiperTTSProvider",
    "TTSService",
    "TTSOutcome",
    "build_tts_service",
]


def build_tts_service(settings: Settings) -> TTSService:
    """Create the app-wide TTSService from settings (never raises for
    provider problems — misconfiguration degrades to disabled + error log)."""
    name = (settings.tts_provider or "none").strip().lower()
    provider: TTSProvider | None = None

    if name == "fake":
        provider = FakeTTSProvider()
    elif name == "piper":
        root = Path(settings.tts_voices_dir)
        if not root.is_absolute():
            root = _PROJECT_ROOT / root
        provider = PiperTTSProvider(root)
        if not provider.voices:
            logger.warning(
                "no piper voices under %s — run "
                "backend/scripts/download_tts_voices.py; translated text will "
                "be shown without audio (tts_unsupported_language)",
                root,
            )
    elif name != "none":
        logger.error(
            "unknown TTS_PROVIDER %r (expected piper|fake|none) — tts disabled",
            name,
        )

    service = TTSService(
        provider=provider,
        timeout_s=settings.tts_timeout_s,
        rate_limit_per_minute=settings.tts_rate_limit_per_minute,
        max_concurrent=settings.tts_max_concurrent,
        max_text_chars=settings.tts_max_text_chars,
        worker_threads=settings.tts_worker_threads,
    )
    logger.info(
        "tts service built provider=%s enabled=%s languages=%s",
        provider.name if provider is not None else None,
        service.enabled,
        sorted(getattr(provider, "voices", {}) or {}),
    )
    return service
