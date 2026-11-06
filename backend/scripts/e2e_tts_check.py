"""End-to-end check against a LIVE server: REAL speech → decision → REAL
offline translation → REAL offline speech synthesis (Phase 4, ADR-0013).

Usage (from backend/, with the app running on :3000):
    uv run python scripts/e2e_tts_check.py

One shared hello config (native=ur, preferred=en) for every fixture — the
decision ENGINE routes each utterance, exactly like a real conversation:

    ur_greeting.wav → Rule 1 → translate → en + SPOKEN English audio
    en_hello.wav    → Rule 2 → translate → ur + SPOKEN Urdu audio
    es_hola.wav     → Rule 3 → pass-through → NO translation, NO TTS audio
                      (original audio pass-through must bypass TTS)

Honesty note: REAL server, REAL WebSocket protocol, REAL Whisper detection,
REAL CTranslate2 OPUS-MT translation, REAL Piper voice synthesis (PCM
amplitude-checked — silence would fail). The only synthetic part is the
speaker (edge-tts fixtures, docs/testing.md).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import wave
from pathlib import Path

import numpy as np
from websockets.asyncio.client import connect

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"


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
    print(f"session ready: {ready['session_id']}")


async def stream(ws, pcm: bytes, rate: int) -> None:
    chunk = int(rate * 0.1) * 2  # 100 ms chunks, like the reference client
    for i in range(0, len(pcm), chunk):
        await ws.send(pcm[i : i + chunk])
    await ws.send(json.dumps({"v": 1, "type": "flush"}))


class Outcome:
    def __init__(self) -> None:
        self.transcription: dict | None = None
        self.translation: dict | None = None
        self.audio_meta: dict | None = None
        self.audio_pcm: bytes | None = None
        self.errors: list[dict] = []
        self.binary_frames = 0


async def collect_until_settled(ws, expect_translation: bool) -> Outcome:
    """Collect events until the utterance is fully settled:
    transcription (+ translation, + audio+binary when translated)."""
    out = Outcome()
    while out.transcription is None or (
        (out.transcription.get("decision", {}).get("action") == "translate"
         and expect_translation
         and (out.translation is None or (out.audio_pcm is None and not out.errors)))
    ):
        frame = await asyncio.wait_for(ws.recv(), 120)
        if isinstance(frame, bytes):
            out.binary_frames += 1
            if out.audio_meta is not None:
                out.audio_pcm = frame
            continue
        event = json.loads(frame)
        kind = event.get("type")
        if kind == "transcription" and out.transcription is None:
            out.transcription = event
        elif kind == "translation":
            out.translation = event
        elif kind == "audio":
            out.audio_meta = event
        elif kind == "error":
            if event.get("fatal"):
                raise RuntimeError(f"fatal error: {event}")
            out.errors.append(event)
        elif kind == "pong":
            continue
    return out


def pcm_rms(pcm: bytes) -> float:
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    return float(np.sqrt(np.mean(samples**2))) if samples.size else 0.0


def report(out: Outcome) -> None:
    decision = (out.transcription or {}).get("decision") or {}
    print(
        f"  decision: action={decision.get('action')} reason={decision.get('reason')} "
        f"src={decision.get('source_language')} tgt={decision.get('target_language')}"
    )
    if out.translation is not None:
        print(
            f"  translation: provider={out.translation['provider']} "
            f"ms={out.translation['duration_ms']} text={out.translation['text'][:48]!r}"
        )
    if out.audio_meta is not None:
        meta = out.audio_meta
        print(
            f"  audio: provider={meta['provider']} language={meta['language']} "
            f"sample_rate={meta['sample_rate']} duration_ms={meta['duration_ms']} "
            f"synthesis_ms={meta['synthesis_ms']} bytes={meta['bytes']}"
        )
    if out.audio_pcm is not None:
        print(f"  audio pcm: {len(out.audio_pcm)} bytes, rms={pcm_rms(out.audio_pcm):.4f}")
    for err in out.errors:
        print(f"  error: {err.get('code')} (utterance-scoped, non-fatal)")


async def run(url: str) -> int:
    failures = 0

    # -- Rule 1: Urdu speech → English translation → SPOKEN English ----------
    pcm, rate = read_wav(FIXTURES / "ur_greeting.wav")
    async with connect(url, max_size=2**24) as ws:
        await handshake(ws)
        await stream(ws, pcm, rate)
        out = await collect_until_settled(ws, expect_translation=True)
    report(out)
    decision = (out.transcription or {}).get("decision") or {}
    ok = (
        decision.get("action") == "translate"
        and decision.get("target_language") == "en"
        and out.translation is not None
        and _latin_ratio(out.translation.get("text", "")) >= 0.6
        and out.audio_meta is not None
        and out.audio_meta.get("language") == "en"
        and out.audio_pcm is not None
        and len(out.audio_pcm) == out.audio_meta.get("bytes")
        and pcm_rms(out.audio_pcm) > 0.03  # audible speech, not silence
        and 300 <= out.audio_meta.get("duration_ms", 0) <= 15_000
        and out.binary_frames == 1
    )
    failures += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] ur_greeting.wav → ur→en + spoken en (Rule 1)")

    # -- Rule 2: English speech → Urdu translation → SPOKEN Urdu -------------
    pcm, rate = read_wav(FIXTURES / "en_hello.wav")
    async with connect(url, max_size=2**24) as ws:
        await handshake(ws)
        await stream(ws, pcm, rate)
        out = await collect_until_settled(ws, expect_translation=True)
    report(out)
    decision = (out.transcription or {}).get("decision") or {}
    ok = (
        decision.get("action") == "translate"
        and decision.get("target_language") == "ur"
        and out.translation is not None
        and _arabic_ratio(out.translation.get("text", "")) >= 0.4
        and out.audio_meta is not None
        and out.audio_meta.get("language") == "ur"
        and out.audio_pcm is not None
        and len(out.audio_pcm) == out.audio_meta.get("bytes")
        and pcm_rms(out.audio_pcm) > 0.03
        and 300 <= out.audio_meta.get("duration_ms", 0) <= 15_000
        and out.binary_frames == 1
    )
    failures += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] en_hello.wav → en→ur + spoken ur (Rule 2)")

    # -- Rule 3: third language → pass-through, NO translation, NO TTS -------
    pcm, rate = read_wav(FIXTURES / "es_hola.wav")
    async with connect(url, max_size=2**24) as ws:
        await handshake(ws)
        await stream(ws, pcm, rate)
        out = await collect_until_settled(ws, expect_translation=False)
        # ping proves NOTHING else (no audio event, no binary) is in flight
        await ws.send(json.dumps({"v": 1, "type": "ping", "ts": 7}))
        while True:
            frame = await asyncio.wait_for(ws.recv(), 30)
            if isinstance(frame, bytes):
                out.binary_frames += 1
                continue
            event = json.loads(frame)
            if event.get("type") == "audio":
                out.audio_meta = event
            elif event.get("type") == "pong":
                break
    report(out)
    decision = (out.transcription or {}).get("decision") or {}
    ok = (
        decision.get("action") == "passthrough"
        and decision.get("reason") == "third_language"
        and out.translation is None
        and out.audio_meta is None  # TTS bypassed for pass-through
        and out.binary_frames == 0
        and not out.errors
    )
    failures += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] es_hola.wav → pass-through, TTS bypassed (Rule 3)")

    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="ws://localhost:3000/ws/audio")
    args = parser.parse_args()
    for name in ("ur_greeting.wav", "en_hello.wav", "es_hola.wav"):
        if not (FIXTURES / name).is_file():
            raise SystemExit(f"missing fixture: {name}")
    failures = asyncio.run(run(args.url))
    if failures:
        raise SystemExit(f"{failures} check(s) FAILED")
    print("all TTS E2E checks passed")


if __name__ == "__main__":
    main()
