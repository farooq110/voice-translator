"""Offline CTranslate2 OPUS-MT translation provider — the Phase 3 default.

Uses pre-converted Marian/OPUS-MT models in CTranslate2 layout, one directory
per direction (`<src>-<tgt>`, e.g. `models/translation/ur-en`), provisioned
by `backend/scripts/download_translation_models.py`:

    model.bin               CT2 weights (loaded int8 — ≈40 MB RSS per pair)
    shared_vocabulary.json  joint BPE vocabulary (CT2 format)
    source.spm/target.spm   SentencePiece tokenizers (encode/decode)

Pipeline per request (all CPU, off the event loop — the service runs this on
its own single-thread executor):

    text ── source.spm.encode ──▶ CT2 Translator.translate_batch
         ◀── target.spm.decode ── target pieces

Why local models (mirrors the Phase 1 faster-whisper decision): no API keys,
no per-utterance cost, no audio/text leaving the machine, deterministic
latency — and the pair coverage (ur↔en, es↔en) is real, tested quality for
the master prompt's core scenarios. Cloud providers plug in through the same
abstraction (see libre.py) when higher quality/coverage is needed.

Missing models degrade honestly: `supports()` answers False → the decision
engine answers `passthrough` with reason `unsupported_pair` — never an
error, never a wrong translation.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from .base import (
    TranslationProvider,
    TranslationRequestError,
    UnsupportedLanguagePairError,
)

if TYPE_CHECKING:
    from ctranslate2 import Translator
    from sentencepiece import SentencePieceProcessor

logger = logging.getLogger(__name__)

_DIRNAME_RE = re.compile(r"^([a-z]{2,3})-([a-z]{2,3})$")


class LocalCT2Provider(TranslationProvider):
    name = "local-opus-mt"

    def __init__(
        self,
        models_root: Path,
        *,
        beam_size: int = 4,
        max_decoding_length: int = 256,
        compute_type: str = "int8",
    ) -> None:
        self.models_root = Path(models_root)
        self.beam_size = max(1, int(beam_size))
        self.max_decoding_length = int(max_decoding_length)
        self.compute_type = compute_type
        # Discover provisioned directions ("ur-en" → ("ur", "en")).
        self.pairs: set[tuple[str, str]] = set()
        if self.models_root.is_dir():
            for entry in sorted(self.models_root.iterdir()):
                match = _DIRNAME_RE.match(entry.name)
                if entry.is_dir() and match and (entry / "model.bin").is_file():
                    self.pairs.add((match.group(1), match.group(2)))
        self._loaded: dict[
            tuple[str, str], tuple["Translator", "SentencePieceProcessor", "SentencePieceProcessor"]
        ] = {}
        # Marian framing quirk (measured): the gaudi/CT2 conversions carry
        # `add_source_eos: False`, but the OPUS-MT/Marian models were trained
        # WITH a trailing `</s>` on the source — without it the decoder never
        # emits EOS and rambles into repetition loops ("now now now …").
        # Appending the model's EOS token (from config.json) fixes termination.
        self._eos_tokens: dict[tuple[str, str], str] = {}
        self._load_lock = threading.Lock()

    # -- provider API --------------------------------------------------------

    def supports(self, source_language: str, target_language: str) -> bool:
        return (source_language, target_language) in self.pairs

    def translate(self, text: str, source_language: str, target_language: str) -> str:
        pair = (source_language, target_language)
        if pair not in self.pairs:
            raise UnsupportedLanguagePairError(
                f"no local model for {source_language}->{target_language}"
            )
        if not text.strip():
            raise TranslationRequestError("empty text")
        translator, src_spm, tgt_spm = self._ensure_loaded(pair)
        pieces = src_spm.encode(text, out_type=str)
        if not pieces:
            raise TranslationRequestError("source text tokenized to nothing")
        # Marian source framing — see _eos_tokens comment (EOS terminates the
        # decoder cleanly; without it output derails into repetition).
        pieces = pieces + [self._eos_tokens.get(pair, "</s>")]
        results = translator.translate_batch(
            [pieces],
            beam_size=self.beam_size,
            max_decoding_length=self.max_decoding_length,
        )
        hypotheses = results[0].hypotheses if results else []
        if not hypotheses:
            raise TranslationRequestError("model produced no translation")
        return tgt_spm.decode(hypotheses[0]).strip()

    def describe(self) -> dict[str, object]:
        return {
            **super().describe(),
            "pairs": sorted("/".join(p) for p in self.pairs),
            "compute_type": self.compute_type,
            "beam_size": self.beam_size,
        }

    # -- internals -----------------------------------------------------------

    def _ensure_loaded(
        self, pair: tuple[str, str]
    ) -> tuple["Translator", "SentencePieceProcessor", "SentencePieceProcessor"]:
        existing = self._loaded.get(pair)
        if existing is not None:
            return existing
        with self._load_lock:
            existing = self._loaded.get(pair)
            if existing is not None:
                return existing
            from ctranslate2 import Translator
            from sentencepiece import SentencePieceProcessor

            src, tgt = pair
            model_dir = self.models_root / f"{src}-{tgt}"
            logger.info(
                "loading local MT model %s (compute_type=%s)", model_dir, self.compute_type
            )
            translator = Translator(
                str(model_dir), device="cpu", compute_type=self.compute_type
            )
            src_spm = SentencePieceProcessor(model_file=str(model_dir / "source.spm"))
            tgt_spm = SentencePieceProcessor(model_file=str(model_dir / "target.spm"))
            eos_token = "</s>"
            config_path = model_dir / "config.json"
            if config_path.is_file():
                try:
                    eos_token = str(json.loads(config_path.read_text()).get("eos_token", eos_token))
                except (OSError, ValueError):
                    pass
            self._eos_tokens[pair] = eos_token
            self._loaded[pair] = (translator, src_spm, tgt_spm)
            logger.info("local MT model %s ready", model_dir)
            return self._loaded[pair]

    def ensure_loaded(self) -> None:
        """Warm every provisioned direction (startup background warmup)."""
        for pair in sorted(self.pairs):
            self._ensure_loaded(pair)
