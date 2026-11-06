"""Offline Piper neural TTS provider — the Phase 4 default (ADR-0013).

Piper is a fast, local VITS voice engine that runs on ONNX Runtime — which
the backend already ships for faster-whisper, so the dependency surface does
not grow (no torch, no cloud, no API keys; audio never leaves the machine).

Voices live under `tts_voices_dir` (default `models/tts/`), one directory per
primary language containing the `.onnx` voice and its adjacent `.onnx.json`
config, provisioned by `backend/scripts/download_tts_voices.py`:

    models/tts/ur/ur_PK-fasih-medium.onnx       (+ .onnx.json, 22050 Hz)
    models/tts/en/en_US-lessac-medium.onnx      (+ .onnx.json)
    models/tts/es/es_MX-ald-medium.onnx         (+ .onnx.json)

Measured on 2 vCPU (docs/testing.md §5): synthesis runs at RTF ≈ 0.06–0.08 —
a 2 s sentence is spoken in ~0.15 s, so TTS adds well under the translation
latency the user already waits for.

Missing voices degrade honestly: `supports()` answers False and the utterance
keeps its translated text with a visible "voice unavailable" condition —
never a silent failure, never a wrong-language voice.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from .base import (
    SynthesisRequestError,
    SynthesisUnavailableError,
    SynthesizedAudio,
    TTSProvider,
)
from .fake import primary_subtag

if TYPE_CHECKING:
    from piper import PiperVoice

logger = logging.getLogger(__name__)


class PiperTTSProvider(TTSProvider):
    name = "piper"

    def __init__(self, voices_root: Path) -> None:
        self.voices_root = Path(voices_root)
        # primary language → voice model path (first .onnx per language dir,
        # sorted for determinism).
        self.voices: dict[str, Path] = {}
        if self.voices_root.is_dir():
            for lang_dir in sorted(self.voices_root.iterdir()):
                if not lang_dir.is_dir():
                    continue
                for model in sorted(lang_dir.glob("*.onnx")):
                    if model.with_name(model.name + ".json").is_file():
                        lang = primary_subtag(lang_dir.name)
                        if lang not in self.voices:
                            self.voices[lang] = model
                        break
        self._loaded: dict[str, "PiperVoice"] = {}
        self._load_lock = threading.Lock()

    # -- provider API --------------------------------------------------------

    def supports(self, language: str) -> bool:
        return primary_subtag(language) in self.voices

    def synthesize(self, text: str, language: str) -> SynthesizedAudio:
        lang = primary_subtag(language)
        if lang not in self.voices:
            raise SynthesisUnavailableError(f"no piper voice for {language}")
        if not text.strip():
            raise SynthesisRequestError("empty text")
        voice = self._ensure_loaded(lang)
        chunks = list(voice.synthesize(text))
        pcm = b"".join(chunk.audio_int16_bytes for chunk in chunks)
        if not pcm:
            raise SynthesisRequestError("voice produced no audio")
        sample_rate = int(voice.config.sample_rate)
        return SynthesizedAudio(pcm=pcm, sample_rate=sample_rate)

    def describe(self) -> dict[str, object]:
        return {
            **super().describe(),
            "languages": sorted(self.voices),
            "voices": {
                lang: model.name for lang, model in sorted(self.voices.items())
            },
        }

    # -- internals -----------------------------------------------------------

    def _ensure_loaded(self, lang: str) -> "PiperVoice":
        existing = self._loaded.get(lang)
        if existing is not None:
            return existing
        with self._load_lock:
            existing = self._loaded.get(lang)
            if existing is not None:
                return existing
            model_path = self.voices[lang]
            logger.info("loading piper voice %s", model_path.name)
            from piper import PiperVoice  # lazy: only when synthesis is used

            voice = PiperVoice.load(str(model_path))
            self._loaded[lang] = voice
            logger.info("piper voice %s ready (sample_rate=%d)", model_path.name, voice.config.sample_rate)
            return self._loaded[lang]

    def ensure_loaded(self) -> None:
        """Lazy by design: voice loading (~1.5 s, ~100 MB RSS each) happens on
        first use per language so startup and /health stay instant."""
        return None
