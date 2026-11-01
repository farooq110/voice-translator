"""Download offline MT models (CTranslate2 OPUS-MT conversions) — Phase 3.

Downloads each direction into `models/translation/<src>-<tgt>/` at the
project root so the app never writes outside the repository (the same
pattern as the Whisper model cache, P1-08).

Files per model (CTranslate2 layout):
  model.bin              — the quantization-ready CT2 model weights
  shared_vocabulary.json — joint BPE vocabulary (CT2 format)
  source.spm/target.spm  — SentencePiece tokenizers for encode/decode
  config.json            — CT2 model configuration

Usage:
    uv run python scripts/download_translation_models.py [--only ur-en,en-ur]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODELS_ROOT = PROJECT_ROOT / "models" / "translation"

# Pair (primary subtags) -> HuggingFace repo with a pre-converted CTranslate2
# OPUS-MT model. ur<->en is the master prompt's core pair; es<->en is shipped
# as the additional supported pair (also used by the pass-through tests as a
# THIRD language when the pair is ur<->en).
MODEL_REPOS: dict[str, str] = {
    "ur-en": "gaudi/opus-mt-ur-en-ctranslate2",
    "en-ur": "gaudi/opus-mt-en-ur-ctranslate2",
    "es-en": "michaelfeil/ct2fast-opus-mt-es-en",
    "en-es": "michaelfeil/ct2fast-opus-mt-en-es",
}

# All files any of the repos may carry (missing ones are skipped per repo).
WANTED_FILES = (
    "model.bin",
    "config.json",
    "shared_vocabulary.json",
    "shared_vocabulary.txt",
    "source.spm",
    "target.spm",
)


def download(pairs: list[str]) -> int:
    from huggingface_hub import hf_hub_download
    from huggingface_hub.utils import EntryNotFoundError

    failures = 0
    for pair in pairs:
        repo = MODEL_REPOS[pair]
        target_dir = MODELS_ROOT / pair
        target_dir.mkdir(parents=True, exist_ok=True)
        print(f"[mt] {pair} <- {repo}")
        for filename in WANTED_FILES:
            dest = target_dir / filename
            if dest.is_file() and dest.stat().st_size > 0:
                print(f"     {filename}: present, skipped")
                continue
            try:
                path = hf_hub_download(
                    repo_id=repo,
                    filename=filename,
                    local_dir=str(target_dir),
                )
                size_mb = Path(path).stat().st_size / 1e6
                print(f"     {filename}: downloaded ({size_mb:.1f} MB)")
            except EntryNotFoundError:
                print(f"     {filename}: not in repo, skipped")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"     {filename}: FAILED ({exc})", file=sys.stderr)
        model_bin = target_dir / "model.bin"
        if not model_bin.is_file():
            failures += 1
            print(f"[mt] {pair}: model.bin missing after download", file=sys.stderr)
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        default="ur-en,en-ur,es-en,en-es",
        help="comma-separated pairs to download (default: all four)",
    )
    args = parser.parse_args()
    pairs = [p.strip() for p in args.only.split(",") if p.strip()]
    unknown = [p for p in pairs if p not in MODEL_REPOS]
    if unknown:
        parser.error(f"unknown pairs: {unknown} (known: {sorted(MODEL_REPOS)})")
    failures = download(pairs)
    if failures:
        print(f"[mt] finished with {failures} failure(s)", file=sys.stderr)
        return 1
    print("[mt] all models ready under", MODELS_ROOT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
