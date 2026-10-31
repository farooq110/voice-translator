"""Benchmark the Phase 2 server-side VAD pipeline (ADR-0009).

Usage (from backend/):
    HF_HOME=../models/huggingface uv run python scripts/bench_vad.py [--runs 3]

Measures, on the real fixtures plus synthetic non-speech buffers:

1. VAD gate cost        — plan() latency on speech and on silence/noise
                          (must be milliseconds, not decode-scale);
2. silence rejection    — noise that the Phase 1 dead-audio gate could NOT
                          catch (low-level white noise above -80 dBFS):
                          pre-Phase-2 it was fully decoded (latency +
                          hallucination risk); now it is gated before STT;
3. trim benefit         — decode input size and text before vs after
                          trimming the utterance to its speech region
                          (accuracy must not regress);
4. memory               — peak RSS at the end.

Results are recorded in docs/testing.md (Phase 2 section).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402

from app.audio.vad import SileroVAD  # noqa: E402
from app.stt.faster_whisper_stt import FasterWhisperSTT  # noqa: E402


def read_wav_pcm16(path: Path) -> tuple[bytes, int, int]:
    with wave.open(str(path), "rb") as wav:
        rate = wav.getframerate()
        channels = wav.getnchannels()
        if wav.getsampwidth() != 2:
            raise SystemExit(f"{path.name}: expected 16-bit PCM")
        return wav.readframes(wav.getnframes()), rate, channels


def peak_rss_mib() -> float:
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) / 1024
    except OSError:
        pass
    return float("nan")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=os.environ.get("STT_MODEL", "small"))
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument(
        "--fixtures",
        default=str(Path(__file__).resolve().parents[1] / "tests" / "fixtures"),
    )
    args = parser.parse_args()

    fixtures_dir = Path(args.fixtures)
    vad = SileroVAD()
    vad.ensure_loaded()
    stt = FasterWhisperSTT(model_id=args.model, device="cpu", compute_type="int8")
    stt.ensure_loaded()

    ur_pcm, rate, channels = read_wav_pcm16(fixtures_dir / "ur_greeting.wav")
    en_pcm, _, _ = read_wav_pcm16(fixtures_dir / "en_hello.wav")
    assert rate == 16_000 and channels == 1

    rng = np.random.default_rng(7)
    low_noise = ((rng.standard_normal(16_000 * 8) * 0.0016).astype(np.float32) * 32_767).astype(
        "<i2"
    ).tobytes()  # ≈ -56 dBFS — above the Phase 1 dead-audio gate (-80 dBFS)
    silence = b"\x00\x00" * 16_000 * 8

    print(f"model={args.model} runs={args.runs}")
    print("\n== 1. VAD gate cost (plan latency) ==")
    for label, pcm in (
        ("ur_greeting (6.6 s speech)", ur_pcm),
        ("en_hello (7.1 s speech)", en_pcm),
        ("8 s digital silence", silence),
        ("8 s low-level noise", low_noise),
    ):
        times = []
        for _ in range(args.runs + 2):
            t0 = time.perf_counter()
            plan = vad.plan(pcm, sample_rate=16_000)
            times.append((time.perf_counter() - t0) * 1000)
        best = min(times[1:])  # first run includes any warm-up
        print(
            f"  {label}: plan {best:6.1f} ms  chunks={len(plan.chunks)} "
            f"speech_s={plan.speech_s:.2f} dropped_s={plan.dropped_s:.2f}"
        )

    print("\n== 2. silence/noise rejection before vs after the VAD gate ==")
    print("  (honesty note: since ADR-0007 the STT's internal decode-time VAD")
    print("   also trims non-speech — the server gate ADDITIONALLY avoids")
    print("   dispatching any decode work to the executor at all)")
    for label, pcm in (("digital silence (8 s)", silence), ("low-level noise (8 s)", low_noise)):
        t0 = time.perf_counter()
        gated = not vad.plan(pcm, sample_rate=16_000).chunks
        gate_ms = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        result = stt.transcribe_pcm(pcm, sample_rate=16_000, channels=1, language_hint="ur")
        decode_ms = (time.perf_counter() - t0) * 1000
        print(
            f"  {label}: gate={'REJECTED' if gated else 'passed'} in {gate_ms:6.1f} ms | "
            f"decode attempt (no server gate) took {decode_ms:7.1f} ms → text={result.text[:40]!r}"
        )

    print("\n== 3. trim: decode input + accuracy before vs after ==")
    hints = {"ur_greeting": "ur", "en_hello": "en"}
    for label, pcm in (("ur_greeting", ur_pcm), ("en_hello", en_pcm)):
        hint = hints[label]
        # warm-up decode, then best-of-2 per variant (fair timing on a shared box)
        stt.transcribe_pcm(pcm, sample_rate=16_000, channels=1, language_hint=hint)
        raw_best, trim_best, raw_text, trim_text = float("inf"), float("inf"), "", ""
        plan = vad.plan(pcm, sample_rate=16_000)
        trimmed_in = b"".join(plan.chunks)
        for _ in range(2):
            t0 = time.perf_counter()
            raw = stt.transcribe_pcm(pcm, sample_rate=16_000, channels=1, language_hint=hint)
            raw_best = min(raw_best, (time.perf_counter() - t0) * 1000)
            raw_text = raw.text
            t0 = time.perf_counter()
            trimmed = stt.transcribe_pcm(
                trimmed_in, sample_rate=16_000, channels=1, language_hint=hint
            )
            trim_best = min(trim_best, (time.perf_counter() - t0) * 1000)
            trim_text = trimmed.text
        print(
            f"  {label}: raw {len(pcm)/2/16_000:5.2f} s → {raw_best:6.0f} ms | "
            f"trimmed to {len(trimmed_in)/2/16_000:5.2f} s ({len(plan.chunks)} chunk(s)) → {trim_best:6.0f} ms"
        )
        print(f"    raw text     : {raw_text[:70]!r}")
        print(f"    trimmed text : {trim_text[:70]!r}")

    print(f"\npeak RSS: {peak_rss_mib():.0f} MiB (bench process holds Whisper AND Silero)")


if __name__ == "__main__":
    main()
