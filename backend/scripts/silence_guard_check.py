"""Live check for the KI-006 dead-audio guard: stream pure digital silence
to a running server and verify the server answers `no_speech_detected`
(NOT a hallucinated transcription of digits/pseudo-text).

Usage (from backend/, with the app running):
    uv run python scripts/silence_guard_check.py --url ws://localhost:3000/ws/audio
"""

from __future__ import annotations

import argparse
import asyncio
import json

from websockets.asyncio.client import connect


async def run(url: str, seconds: float) -> int:
    pcm = b"\x00\x00" * int(16_000 * seconds)
    async with connect(url, max_size=2**24) as ws:
        await ws.send(
            json.dumps(
                {
                    "v": 1,
                    "type": "hello",
                    "config": {
                        "native_language": "ur",
                        "preferred_language": "en",
                        "audio": {"encoding": "pcm_s16le", "sample_rate": 16_000, "channels": 1},
                    },
                }
            )
        )
        ready = json.loads(await asyncio.wait_for(ws.recv(), 15))
        assert ready.get("type") == "ready", f"expected ready, got {ready}"
        print(f"ready session={ready['session_id']} — streaming {seconds:.1f}s of digital silence")

        await ws.send(pcm)
        await ws.send(json.dumps({"v": 1, "type": "flush"}))

        while True:
            event = json.loads(await asyncio.wait_for(ws.recv(), 60))
            if event.get("type") == "error" and event.get("code") == "no_speech_detected":
                print("PASS: server reported no_speech_detected (hallucination guard active)")
                return 0
            if event.get("type") == "transcription":
                print(f"FAIL: silence was transcribed as {event['text'][:80]!r}")
                return 1
            if event.get("type") == "error":
                print(f"FAIL: unexpected error event: {event}")
                return 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="ws://localhost:3000/ws/audio")
    parser.add_argument("--seconds", type=float, default=2.0)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.url, args.seconds)))


if __name__ == "__main__":
    main()
