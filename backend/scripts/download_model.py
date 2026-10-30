"""Pre-download the configured faster-whisper model into the HF cache.

Usage (from backend/):
    uv run python scripts/download_model.py [--model base]

Honors HF_HOME (cache location) and HF_ENDPOINT (mirror), e.g.:
    HF_ENDPOINT=https://hf-mirror.com uv run python scripts/download_model.py
"""

from __future__ import annotations

import argparse
import os


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model",
        default=os.environ.get("STT_MODEL", "base"),
        choices=["tiny", "base", "small", "medium"],
        help="Whisper model size (default: $STT_MODEL or 'base')",
    )
    args = parser.parse_args()

    repo = f"Systran/faster-whisper-{args.model}"
    print(f"Downloading {repo} (HF_HOME={os.environ.get('HF_HOME', '<default>')}) ...")
    from huggingface_hub import snapshot_download  # deferred heavy import

    path = snapshot_download(repo)
    print(f"Model ready at: {path}")


if __name__ == "__main__":
    main()
