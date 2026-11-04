"""Download offline Piper TTS voices — Phase 4.

Downloads each voice into `models/tts/<language>/` at the project root so the
app never writes outside the repository (same pattern as the Whisper cache and
the MT models).

Files per voice:
  <name>.onnx        — the VITS voice weights (ONNX, CPU-ready)
  <name>.onnx.json   — voice config (sample rate, phonemizer, id maps)

Usage:
    uv run python scripts/download_tts_voices.py [--only ur,en,es]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
VOICES_ROOT = PROJECT_ROOT / "models" / "tts"

# Primary language -> (HuggingFace repo dir path inside rhasspy/piper-voices,
# voice file stem). ur/en are the master prompt's core pair; es covers the
# additional es<->en translation pair.
VOICE_SPECS: dict[str, tuple[str, str]] = {
    "ur": ("ur/ur_PK/fasih/medium", "ur_PK-fasih-medium"),
    "en": ("en/en_US/lessac/medium", "en_US-lessac-medium"),
    "es": ("es/es_MX/ald/medium", "es_MX-ald-medium"),
}

BASE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main"


def download(languages: list[str]) -> int:
    import urllib.request

    failures = 0
    for language in languages:
        repo_dir, stem = VOICE_SPECS[language]
        target_dir = VOICES_ROOT / language
        target_dir.mkdir(parents=True, exist_ok=True)
        print(f"[tts] {language} <- {repo_dir}/{stem}")
        for suffix in (".onnx", ".onnx.json"):
            dest = target_dir / f"{stem}{suffix}"
            url = f"{BASE_URL}/{repo_dir}/{stem}{suffix}?download=true"
            if dest.is_file() and dest.stat().st_size > 1_000:
                print(f"     {dest.name}: present, skipped")
                continue
            try:
                tmp = dest.with_suffix(dest.suffix + ".part")
                with urllib.request.urlopen(url, timeout=120) as response, tmp.open("wb") as fh:
                    while True:
                        block = response.read(1 << 20)
                        if not block:
                            break
                        fh.write(block)
                tmp.rename(dest)
                size_mb = dest.stat().st_size / 1e6
                print(f"     {dest.name}: downloaded ({size_mb:.1f} MB)")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"     {dest.name}: FAILED ({exc})", file=sys.stderr)
                if dest.exists():
                    dest.unlink()
        if not (target_dir / f"{stem}.onnx").is_file():
            failures += 1
            print(f"[tts] {language}: voice .onnx missing after download", file=sys.stderr)
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        default="ur,en,es",
        help="comma-separated languages to download (default: ur,en,es)",
    )
    args = parser.parse_args()
    languages = [lang.strip() for lang in args.only.split(",") if lang.strip()]
    unknown = [lang for lang in languages if lang not in VOICE_SPECS]
    if unknown:
        parser.error(f"unknown languages: {unknown} (known: {sorted(VOICE_SPECS)})")
    failures = download(languages)
    if failures:
        print(f"[tts] finished with {failures} failure(s)", file=sys.stderr)
        return 1
    print("[tts] all voices ready under", VOICES_ROOT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
