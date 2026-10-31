"""Benchmark the local STT provider on WAV fixtures (16 kHz mono PCM16).

Usage (from backend/):
    HF_HOME=../models/huggingface uv run python scripts/bench_stt.py [--model base] [--runs 3]

Each fixture is timed in TWO modes:
- auto    : automatic language detection (pre-KI-006-fix behavior);
- forced  : language pinned from the fixture prefix (en_/ur_/es_) — the
  production path since the KI-006 fix.

Prints per-fixture language, transcription (truncated) and latency
(avg/best of N runs, RTF = processing time / audio duration) plus the
auto → forced speedup, then peak RSS. Results are recorded in
docs/testing.md after each run.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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

    provider = FasterWhisperSTT(model_id=args.model, device="cpu", compute_type="int8")
    t0 = time.perf_counter()
    provider.ensure_loaded()
    print(f"model={args.model} device=cpu compute=int8 load_time_s={time.perf_counter() - t0:.1f}")

    fixtures = sorted(Path(args.fixtures).glob("*.wav"))
    if not fixtures:
        raise SystemExit(f"no .wav fixtures found in {args.fixtures}")

    for fixture in fixtures:
        pcm, rate, channels = read_wav_pcm16(fixture)
        hint = fixture.name.split("_")[0]
        audio_s = len(pcm) / 2 / rate
        summary: dict[str, tuple[float, float, object]] = {}
        for mode in ("auto", "forced"):
            durations: list[float] = []
            result = None
            for _ in range(max(1, args.runs)):
                t = time.perf_counter()
                if mode == "forced":
                    result = provider.transcribe_pcm(
                        pcm, sample_rate=rate, channels=channels, language_hint=hint
                    )
                else:
                    result = provider.transcribe_pcm(pcm, sample_rate=rate, channels=channels)
                durations.append((time.perf_counter() - t) * 1000)
            avg = sum(durations) / len(durations)
            best = min(durations)
            summary[mode] = (avg, best, result)

        auto_avg, auto_best, auto_res = summary["auto"]
        forced_avg, forced_best, forced_res = summary["forced"]
        auto_rtf = (auto_avg / 1000) / audio_s if audio_s else float("nan")
        forced_rtf = (forced_avg / 1000) / audio_s if audio_s else float("nan")
        gain = (1 - forced_avg / auto_avg) * 100 if auto_avg else float("nan")
        print(
            f"{fixture.name}: audio={audio_s:.2f}s\n"
            f"  auto   lang={auto_res.language}(p={auto_res.language_probability:.2f}) "
            f"stt_ms avg={auto_avg:.0f} best={auto_best:.0f} rtf={auto_rtf:.2f} text={auto_res.text[:48]!r}\n"
            f"  forced lang={forced_res.language}(p={forced_res.language_probability:.2f}) "
            f"stt_ms avg={forced_avg:.0f} best={forced_best:.0f} rtf={forced_rtf:.2f} text={forced_res.text[:48]!r}\n"
            f"  speedup(auto→forced)={gain:.0f}%"
        )

    print(f"peak_rss_mib={peak_rss_mib():.0f}")


if __name__ == "__main__":
    main()
