"""Logging setup (stdlib only).

Privacy: transcripts and audio content are logged at DEBUG level only; INFO
logs carry metadata (language, durations, sizes) but never spoken content.
"""

from __future__ import annotations

import logging


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        force=True,
    )
    # Third-party noise reduction.
    logging.getLogger("faster_whisper").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)
