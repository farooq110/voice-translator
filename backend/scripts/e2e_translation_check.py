"""End-to-end check against a LIVE server: REAL speech → decision → REAL
offline translation (Phase 3, ADR-0012).

Usage (from backend/, with the app running on :3000):
    uv run python scripts/e2e_translation_check.py

One shared hello config (native=ur, preferred=en) for every fixture — the
decision ENGINE routes each utterance by its detected language, exactly like
a real conversation:

    ur_greeting.wav → detected ur      → Rule 1 → translate → en (Latin text)
    en_hello.wav    → detected en      → Rule 2 → translate → ur (Arabic text)
    es_hola.wav     → detected es      → Rule 3 → pass-through (NO translation)

Honesty note: REAL server, REAL WebSocket protocol, REAL Whisper detection
(auto mode), REAL CTranslate2 OPUS-MT translation. The only synthetic part
is the speaker (edge-tts fixtures, docs/testing.md).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import wave
from pathlib import Path

from websockets.asyncio.client import connect

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"

# Arabic / Devanagari / Latin script presence thresholds for output text.
def _script_ratio(text: str, lo: int, hi: int) -> float:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for ch in letters if lo <= ord(ch) <= hi) / len(letters)


def _latin_ratio(text: str) -> float:
    return _script_ratio(text, 0x41, 0x7A)


def _arabic_ratio(text: str) -> float:
    return _script_ratio(text, 0x0600, 0x06FF)


def read_wav(path: Path) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as wav:
        if wav.getsampwidth() != 2 or wav.getnchannels() != 1:
            raise SystemExit(f"{path.name}: expected 16-bit mono PCM")
        return wav.readframes(wav.getnframes()), wav.getframerate()


async def handshake(ws) -> None:
    hello = {
        "v": 1,
        "type": "hello",
        "config": {
            "native_language": "ur",
            "preferred_language": "en",
            "audio": {"encoding": "pcm_s16le", "sample_rate": 16_000, "channels": 1},
        },
    }
    await ws.send(json.dumps(hello))
    ready = json.loads(await asyncio.wait_for(ws.recv(), 15))
    if ready.get("type") != "ready":
        raise RuntimeError(f"expected ready, got: {ready}")
    print(
        f"session ready: {ready['session_id']} translation expected (health: /health)"
    )


async def stream(ws, pcm: bytes, rate: int) -> None:
    chunk = int(rate * 0.1) * 2  # 100 ms chunks, like the reference client
    for i in range(0, len(pcm), chunk):
        await ws.send(pcm[i : i + chunk])
    await ws.send(json.dumps({"v": 1, "type": "flush"}))


async def collect(ws) -> tuple[dict, dict | None, dict | None]:
    """Collect the transcription + (optional) translation + (optional) error."""
    transcription: dict | None = None
    translation: dict | None = None
    error: dict | None = None
    while transcription is None or (
        transcription.get("decision", {}).get("action") == "translate"
        and translation is None
        and error is None
    ):
        event = json.loads(await asyncio.wait_for(ws.recv(), 120))
        kind = event.get("type")
        if kind == "transcription" and transcription is None:
            transcription = event
        elif kind == "translation":
            translation = event
        elif kind == "error":
            if event.get("fatal"):
                raise RuntimeError(f"fatal error: {event}")
            error = event
        elif kind == "pong":
            continue
        else:
            continue
    return transcription or {}, translation, error


def check(name: str, transcription: dict, translation: dict | None) -> bool:
    decision = transcription.get("decision") or {}
    print(
        f"  decision: action={decision.get('action')} reason={decision.get('reason')} "
        f"src={decision.get('source_language')} tgt={decision.get('target_language')} "
        f"detection={decision.get('detection')}"
    )
    if translation is not None:
        print(
            f"  translation: provider={translation['provider']} "
            f"ms={translation['duration_ms']} text={translation['text'][:60]!r}"
        )
    return True


async def run(url: str, fixture_names: list[str]) -> int:
    failures = 0

    # -- Rule 1: Urdu speech → English translation ---------------------------
    pcm, rate = read_wav(FIXTURES / "ur_greeting.wav")
    async with connect(url, max_size=2**24) as ws:
        await handshake(ws)
        await stream(ws, pcm, rate)
        transcription, translation, error = await collect(ws)
    decision = transcription.get("decision") or {}
    ok = (
        decision.get("action") == "translate"
        and decision.get("reason") == "native_language"
        and decision.get("source_language") == "ur"
        and decision.get("target_language") == "en"
        and translation is not None
        and translation.get("target_language") == "en"
        and _latin_ratio(translation.get("text", "")) >= 0.6
    )
    failures += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] ur_greeting.wav → ur→en (Rule 1)")
    check("ur", transcription, translation)

    # -- Rule 2: English speech → Urdu translation ---------------------------
    pcm, rate = read_wav(FIXTURES / "en_hello.wav")
    async with connect(url, max_size=2**24) as ws:
        await handshake(ws)
        await stream(ws, pcm, rate)
        transcription, translation, error = await collect(ws)
    decision = transcription.get("decision") or {}
    ok = (
        decision.get("action") == "translate"
        and decision.get("reason") == "preferred_language"
        and decision.get("source_language") == "en"
        and decision.get("target_language") == "ur"
        and translation is not None
        and translation.get("target_language") == "ur"
        and _arabic_ratio(translation.get("text", "")) >= 0.4
    )
    failures += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] en_hello.wav → en→ur (Rule 2)")
    check("en", transcription, translation)

    # -- Rule 3: third language → pass-through, NO provider call -------------
    pcm, rate = read_wav(FIXTURES / "es_hola.wav")
    async with connect(url, max_size=2**24) as ws:
        await handshake(ws)
        await stream(ws, pcm, rate)
        transcription, translation, error = await collect(ws)
    decision = transcription.get("decision") or {}
    ok = (
        decision.get("action") == "passthrough"
        and decision.get("reason") == "third_language"
        and translation is None
    )
    failures += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] es_hola.wav → pass-through (Rule 3)")
    check("es", transcription, translation)

    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="ws://localhost:3000/ws/audio")
    args = parser.parse_args()
    for name in ("ur_greeting.wav", "en_hello.wav", "es_hola.wav"):
        if not (FIXTURES / name).is_file():
            raise SystemExit(f"missing fixture: {name}")
    failures = asyncio.run(run(args.url, ["ur_greeting.wav", "en_hello.wav", "es_hola.wav"]))
    if failures:
        raise SystemExit(f"{failures} check(s) FAILED")
    print("all translation E2E checks passed")


if __name__ == "__main__":
    main()
