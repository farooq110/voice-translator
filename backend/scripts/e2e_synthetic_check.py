"""End-to-end check against a LIVE server: stream WAV fixtures over the
WebSocket, verify transcription + language round-trip.

Usage (from backend/, with the app running e.g. on :3000):
    uv run python scripts/e2e_synthetic_check.py --url ws://localhost:3000/ws/audio

Each fixture is streamed on its own connection whose hello config sets
`native_language` to the fixture's language — mirroring the real app, where
the translator user's selection is pinned as the STT language hint (KI-006
fix). Expectations are therefore deterministic: ur→ur, en→en, es→es.

Honesty note: this drives the REAL server, REAL WebSocket protocol and REAL
STT model with TTS-generated speech — it does not mock anything except that
the "speaker" is a synthetic voice (see docs/testing.md).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import wave
from pathlib import Path

from websockets.asyncio.client import connect

# Fixture → native language configured for that stream (forced STT hint).
LANGUAGE_BY_FIXTURE: dict[str, str] = {
    "en_hello.wav": "en",
    "ur_greeting.wav": "ur",
    "es_hola.wav": "es",
}

# With the language pinned from hello config, labels are deterministic.
EXPECTED: dict[str, set[str]] = {
    "en_hello.wav": {"en"},
    "ur_greeting.wav": {"ur"},
    "es_hola.wav": {"es"},
}


def read_wav(path: Path) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as wav:
        if wav.getsampwidth() != 2 or wav.getnchannels() != 1:
            raise SystemExit(f"{path.name}: expected 16-bit mono PCM")
        return wav.readframes(wav.getnframes()), wav.getframerate()


async def handshake(ws, native_language: str) -> None:
    hello = {
        "v": 1,
        "type": "hello",
        "config": {
            "native_language": native_language,
            "preferred_language": "en" if native_language != "en" else "ur",
            "audio": {"encoding": "pcm_s16le", "sample_rate": 16_000, "channels": 1},
        },
    }
    await ws.send(json.dumps(hello))
    ready = json.loads(await asyncio.wait_for(ws.recv(), 15))
    if ready.get("type") != "ready":
        raise RuntimeError(f"expected ready, got: {ready}")
    print(f"session ready: {ready['session_id']} native={native_language} stt={ready['stt']['provider']}/{ready['stt']['model']}")


async def stream_and_collect(
    ws, path: Path, *, chunk_ms: int, pace: bool
) -> tuple[dict, float]:
    pcm, rate = read_wav(path)

    chunk_bytes = int(rate * chunk_ms / 1000) * 2
    sent_at = asyncio.get_running_loop().time()
    for i in range(0, len(pcm), chunk_bytes):
        await ws.send(pcm[i : i + chunk_bytes])
        if pace:
            await asyncio.sleep(chunk_ms / 1000)

    await ws.send(json.dumps({"v": 1, "type": "flush"}))
    while True:
        event = json.loads(await asyncio.wait_for(ws.recv(), 120))
        if event.get("type") == "transcription":
            return event, asyncio.get_running_loop().time() - sent_at
        if event.get("type") == "error" and event.get("fatal"):
            raise RuntimeError(f"fatal error: {event}")


async def run(url: str, fixtures: list[Path], chunk_ms: int, pace: bool) -> int:
    failures = 0
    for path in fixtures:
        native = LANGUAGE_BY_FIXTURE.get(path.name, path.name.split("_")[0])
        async with connect(url, max_size=2**24) as ws:
            await handshake(ws, native)
            event, wall_s = await stream_and_collect(ws, path, chunk_ms=chunk_ms, pace=pace)
        expected = EXPECTED.get(path.name)
        ok = expected is None or event["language"] in expected
        if not ok:
            failures += 1
        status = "PASS" if ok else "FAIL"
        print(
            f"[{status}] {path.name}: lang={event['language']} (expected {sorted(expected or set())}) "
            f"p={event['language_probability']:.2f} proc_ms={event['processing_ms']} "
            f"wall_s={wall_s:.1f} text={event['text'][:60]!r}"
        )
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="ws://localhost:3000/ws/audio")
    parser.add_argument(
        "--fixtures",
        default=str(Path(__file__).resolve().parents[1] / "tests" / "fixtures"),
    )
    parser.add_argument("--chunk-ms", type=int, default=100)
    parser.add_argument(
        "--fast", action="store_true", help="send audio faster than real time"
    )
    args = parser.parse_args()

    fixtures = sorted(Path(args.fixtures).glob("*.wav"))
    if not fixtures:
        raise SystemExit(f"no fixtures in {args.fixtures}")

    failures = asyncio.run(run(args.url, fixtures, args.chunk_ms, pace=not args.fast))
    if failures:
        raise SystemExit(f"{failures} check(s) FAILED")
    print("all E2E checks passed")


if __name__ == "__main__":
    main()
