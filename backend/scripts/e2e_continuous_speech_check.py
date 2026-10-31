"""E2E check for live segmentation latency (KI-009 / ADR-0010).

Reproduces the user's complaint on a LIVE server: the microphone stays open
while the user keeps talking and only short (< 800 ms) micro-pauses occur.
The frontend closes a segment at an 800 ms pause or — before ADR-0010 — at
the 20 s hard cap, so continuous speech produced nothing until the cap was
hit and the whole blob then needed ~6 s to decode.

Fixture: the Urdu greeting with every silence run ≥ 250 ms compressed to
200 ms (real-time paced, 100 ms chunks) — continuous speech with natural
micro-pauses. The script runs the SAME endpointing state machine as the
frontend, in two variants, and reads server events CONCURRENTLY with
streaming so latencies are true end-to-end values:

  old (Phase 2 as shipped): close at an 800 ms pause or a 20 s hard cap
  new (ADR-0010): also close at the first 300 ms micro-pause after 4.5 s;
                  hard cap 9 s

PASS requires:
  old mode (fixture sanity):  ≤ 2 flushes — no pause below 800 ms may close
                              a segment, i.e. the fixture is truly continuous
  new mode:                   ≥ 3 utterances, all non-empty, each arriving
                              within 4.0 s of its flush (measured decode RTF
                              for 5-6 s ur segments ≈ 0.31-0.40 → 2.0-2.4 s)

Usage (from backend/, with the app running, e.g. on :3000):
    uv run python scripts/e2e_continuous_speech_check.py [--mode new|old|both]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from websockets.asyncio.client import connect

# --- Mirrors frontend/src/hooks/useTranslatorSession.ts (ADR-0010) ----------
CHUNK_MS = 100
RMS_SPEECH = 0.01
MIN_SEGMENT_MS = 1_200
SILENCE_MS = 800
SOFT_CAP_MS = 4_500
MICRO_PAUSE_MS = 300
HARD_CAP_OLD_MS = 20_000
HARD_CAP_NEW_MS = 9_000
MAX_RESULT_LATENCY_S = 4.0

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"


@dataclass
class Endpoint:
    buffered_ms: int = 0
    silent_ms: int = 0
    speech_seen: bool = False

    def reset(self) -> None:
        self.buffered_ms = 0
        self.silent_ms = 0
        self.speech_seen = False


def should_flush(ep: Endpoint, mode: str) -> bool:
    pause = ep.speech_seen and ep.buffered_ms >= MIN_SEGMENT_MS and ep.silent_ms >= SILENCE_MS
    if mode == "new":
        soft = ep.speech_seen and ep.buffered_ms >= SOFT_CAP_MS and ep.silent_ms >= MICRO_PAUSE_MS
        hard = ep.buffered_ms >= HARD_CAP_NEW_MS
    else:
        soft = False
        hard = ep.buffered_ms >= HARD_CAP_OLD_MS
    return pause or soft or hard


def rms(chunk: bytes) -> float:
    x = np.frombuffer(chunk, dtype="<i2").astype(np.float32) / 32_768.0
    return float(np.sqrt(np.mean(x * x))) if x.size else 0.0


def load_pcm(path: Path) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as wav:
        if wav.getsampwidth() != 2 or wav.getnchannels() != 1:
            raise SystemExit(f"{path.name}: expected 16-bit mono PCM")
        return wav.readframes(wav.getnframes()), wav.getframerate()


def compress_silences(
    pcm: bytes, rate: int, *, frame_ms: int = 10, min_run_ms: int = 250, keep_ms: int = 200
) -> bytes:
    """Shrink every silence run ≥ min_run_ms down to keep_ms of silence.

    Turns a multi-clause greeting into CONTINUOUS speech with only short
    micro-pauses — the speaking style the KI-009 complaint is about.
    """
    frame_bytes = int(rate * frame_ms / 1000) * 2
    keep_bytes = int(rate * keep_ms / 1000) * 2
    out = bytearray()
    silent_run: list[bytes] = []
    for i in range(0, len(pcm) - len(pcm) % frame_bytes, frame_bytes):
        frame = pcm[i : i + frame_bytes]
        if rms(frame) < RMS_SPEECH:
            silent_run.append(frame)
            continue
        if len(silent_run) * frame_ms >= min_run_ms:
            out.extend(b"\x00" * keep_bytes)
        else:
            out.extend(b"".join(silent_run))
        silent_run = []
        out.extend(frame)
    if len(silent_run) * frame_ms >= min_run_ms:
        out.extend(b"\x00" * keep_bytes)
    else:
        out.extend(b"".join(silent_run))
    return bytes(out)


def build_stream(path: Path, repeat: int, gap_ms: int) -> tuple[bytes, int, float]:
    pcm, rate = load_pcm(path)
    compressed = compress_silences(pcm, rate)
    gap = b"\x00\x00" * int(rate * gap_ms / 1000)
    parts: list[bytes] = []
    for i in range(repeat):
        parts.append(compressed)
        if i < repeat - 1:
            parts.append(gap)
    stream = b"".join(parts)
    return stream, rate, len(stream) / 2 / rate


async def run_mode(
    url: str, pcm: bytes, rate: int, mode: str, native: str, repeat: int, gap_ms: int
) -> bool:
    loop = asyncio.get_running_loop()
    async with connect(url, max_size=2**24) as ws:
        hello = {
            "v": 1,
            "type": "hello",
            "config": {
                "native_language": native,
                "preferred_language": "en" if native != "en" else "ur",
                "audio": {"encoding": "pcm_s16le", "sample_rate": 16_000, "channels": 1},
            },
        }
        await ws.send(json.dumps(hello))
        ready = json.loads(await asyncio.wait_for(ws.recv(), 15))
        if ready.get("type") != "ready":
            raise RuntimeError(f"expected ready, got: {ready}")

        ep = Endpoint()
        chunk_bytes = int(rate * CHUNK_MS / 1000) * 2
        flush_times: list[float] = []
        results: list[tuple[float, dict]] = []
        errors: list[tuple[float, dict]] = []
        start = loop.time()

        async def sender() -> None:
            next_t = start
            for i in range(0, len(pcm), chunk_bytes):
                chunk = pcm[i : i + chunk_bytes]
                await ws.send(chunk)
                ep.buffered_ms += CHUNK_MS
                if rms(chunk) >= RMS_SPEECH:
                    ep.silent_ms = 0
                    ep.speech_seen = True
                else:
                    ep.silent_ms += CHUNK_MS
                if should_flush(ep, mode):
                    await ws.send(json.dumps({"v": 1, "type": "flush"}))
                    flush_times.append(loop.time() - start)
                    ep.reset()
                next_t += CHUNK_MS / 1000
                delay = next_t - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
            if ep.buffered_ms > 0:
                await ws.send(json.dumps({"v": 1, "type": "flush"}))
                flush_times.append(loop.time() - start)
                ep.reset()

        async def receiver() -> None:
            # Reads CONCURRENTLY with streaming — event timestamps are the
            # true arrival times, not post-streaming drain artifacts.
            while True:
                try:
                    raw = await ws.recv()
                except Exception:
                    return
                event = json.loads(raw)
                t = loop.time() - start
                if event.get("type") == "transcription":
                    results.append((t, event))
                elif event.get("type") == "error":
                    errors.append((t, event))

        sender_task = asyncio.create_task(sender())
        recv_task = asyncio.create_task(receiver())
        await sender_task
        # Wait for every flush to be answered (transcription or error), then
        # close — otherwise the idle receiver would hang for the server's
        # 180 s idle timeout and log a spurious error.
        deadline = loop.time() + 15
        while len(results) + len(errors) < len(flush_times) and loop.time() < deadline:
            await asyncio.sleep(0.05)
        total_s = loop.time() - start
        await ws.close()
        try:
            await asyncio.wait_for(recv_task, 5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            recv_task.cancel()

    print(f"\n=== mode={mode} · {repeat} compressed greetings · {gap_ms} ms micro-pauses · stream {total_s:.1f}s ===")
    print(f"flushes={len(flush_times)} transcriptions={len(results)} errors={len(errors)}")
    for t, ev in errors:
        print(f"  error @ {t:6.2f}s: {ev.get('code')}")

    ok = True
    for k, (t, ev) in enumerate(results):
        flush_at = flush_times[k] if k < len(flush_times) else float("nan")
        latency = t - flush_at
        text = ev.get("text", "")
        if mode == "new" and (latency > MAX_RESULT_LATENCY_S or not text.strip()):
            ok = False
        print(
            f"  utt {k + 1}: flush @{flush_at:6.2f}s → text @{t:6.2f}s | "
            f"flush→text {latency:4.2f}s | proc {ev.get('processing_ms')}ms | "
            f"{ev.get('language')} {ev.get('language_probability'):.2f} | {text[:48]!r}"
        )

    if mode == "old":
        # Fixture sanity: with all pauses compressed below 800 ms the old
        # state machine may only flush at the 20 s hard cap (+ EOF remainder).
        if len(flush_times) > 2:
            print(f"[FAIL] mode=old produced {len(flush_times)} flushes (>2) — "
                  "fixture is not continuous (a pause ≥800 ms slipped in)")
            ok = False
        else:
            first_text = results[0][0] if results else float("nan")
            print(f"[info] mode=old baseline: first text after {first_text:.1f}s of "
                  "continuous speech (hard-cap blob + full decode)")
        return ok

    expected = len(flush_times)
    unexpected_errors = [
        e for _, e in errors
        if e.get("code") not in ("nothing_to_transcribe", "idle_timeout")
    ]
    if len(results) < 3 or unexpected_errors:
        ok = False
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] mode=new: {len(results)} utterances from {expected} flushes, "
          f"all flush→text ≤ {MAX_RESULT_LATENCY_S:.1f}s and non-empty")
    return ok


async def run(url: str, modes: list[str], fixture: Path, repeat: int, gap_ms: int) -> int:
    native = fixture.name.split("_")[0]
    pcm, rate, duration_s = build_stream(fixture, repeat, gap_ms)
    greet_s = len(load_pcm(fixture)[0]) / 2 / rate
    print(
        f"fixture {fixture.name} ({greet_s:.2f}s, silences compressed) × {repeat} "
        f"+ {gap_ms} ms gaps = {duration_s:.1f}s of continuous speech, native={native}"
    )
    failures = 0
    for mode in modes:
        if not await run_mode(url, pcm, rate, mode, native, repeat, gap_ms):
            failures += 1
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="ws://localhost:3000/ws/audio")
    parser.add_argument("--mode", choices=["new", "old", "both"], default="both")
    parser.add_argument("--fixture", default=str(FIXTURES / "ur_greeting.wav"))
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--gap-ms", type=int, default=200)
    args = parser.parse_args()

    modes = ["old", "new"] if args.mode == "both" else [args.mode]
    failures = asyncio.run(run(args.url, modes, Path(args.fixture), args.repeat, args.gap_ms))
    if failures:
        raise SystemExit(f"{failures} mode(s) FAILED")
    print("\ncontinuous-speech latency check passed")


if __name__ == "__main__":
    main()
