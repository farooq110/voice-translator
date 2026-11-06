"""Download offline Piper TTS voices — Phase 4.

Downloads each voice into `models/tts/<language>/` at the project root so the
app never writes outside the repository (same pattern as the Whisper cache and
the MT models).

Files per voice:
  <name>.onnx        — the VITS voice weights (ONNX, CPU-ready)
  <name>.onnx.json   — voice config (sample rate, phonemizer, id maps)

Usage:
    uv run python scripts/download_tts_voices.py [--only ur,en,es]
    uv run python scripts/download_tts_voices.py --verify   # exit 0 iff complete

QA (2026-11-05, BUG-001): HuggingFace answers 429 for the small `.onnx.json`
config right after a ~63 MB `.onnx` download — a boot-time provisioning run
can silently finish with voiceless directories (onnx without json). Files are
now downloaded with bounded retry/backoff, and `--verify` lets start.sh
detect INCOMPLETE provisioning instead of only an empty directory.
"""

from __future__ import annotations

import argparse
import sys
import time
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


def _fetch(url: str, dest: Path, *, attempts: int = 5) -> None:
    """Download `url` to `dest` atomically, retrying transient failures
    (HF answers 429/5xx bursts — BUG-001). Backoff: 5, 15, 30, 60 s."""
    import urllib.error
    import urllib.request

    delays = (5, 15, 30, 60)
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            tmp = dest.with_suffix(dest.suffix + ".part")
            with urllib.request.urlopen(url, timeout=120) as response, tmp.open("wb") as fh:
                while True:
                    block = response.read(1 << 20)
                    if not block:
                        break
                    fh.write(block)
            tmp.rename(dest)
            return
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if dest.exists():
                dest.unlink()
            if attempt < attempts - 1:
                delay = delays[min(attempt, len(delays) - 1)]
                print(f"     {dest.name}: retry in {delay}s ({exc})", file=sys.stderr)
                time.sleep(delay)
    raise last_exc if last_exc else RuntimeError(f"download failed: {url}")


def missing_files(languages: list[str]) -> list[Path]:
    """Voice files that must exist for a complete, usable provisioning."""
    missing: list[Path] = []
    for language in languages:
        _repo_dir, stem = VOICE_SPECS[language]
        for suffix in (".onnx", ".onnx.json"):
            dest = VOICES_ROOT / language / f"{stem}{suffix}"
            if not (dest.is_file() and dest.stat().st_size > 1_000):
                missing.append(dest)
    return missing


def verify(languages: list[str]) -> int:
    missing = missing_files(languages)
    if not missing:
        print("[tts] verify: all voices complete under", VOICES_ROOT)
        return 0
    for path in missing:
        print(f"[tts] verify: MISSING {path}", file=sys.stderr)
    return 1


def download(languages: list[str]) -> int:
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
                _fetch(url, dest)
                size_mb = dest.stat().st_size / 1e6
                print(f"     {dest.name}: downloaded ({size_mb:.1f} MB)")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"     {dest.name}: FAILED ({exc})", file=sys.stderr)
                if dest.exists():
                    dest.unlink()
    missing = missing_files(languages)
    if missing:
        failures += len(missing)
        for path in missing:
            print(f"[tts] still missing after download: {path}", file=sys.stderr)
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        default="ur,en,es",
        help="comma-separated languages to download (default: ur,en,es)",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="do not download — exit 0 only if every voice file is present",
    )
    args = parser.parse_args()
    languages = [lang.strip() for lang in args.only.split(",") if lang.strip()]
    unknown = [lang for lang in languages if lang not in VOICE_SPECS]
    if unknown:
        parser.error(f"unknown languages: {unknown} (known: {sorted(VOICE_SPECS)})")
    if args.verify:
        raise SystemExit(verify(languages))
    failures = download(languages)
    if failures:
        print(f"[tts] finished with {failures} failure(s)", file=sys.stderr)
        return 1
    print("[tts] all voices ready under", VOICES_ROOT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
