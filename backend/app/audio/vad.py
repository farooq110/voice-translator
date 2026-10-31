"""Server-side Silero VAD pipeline (Phase 2 — ADR-0009).

Purpose (master prompt §9 and §17 "Phase 2 — Voice Activity Detection and
Audio Optimization"):

1. Silence gate — buffers with no recognizable speech are rejected BEFORE the
   STT model is invoked (`no_speech_detected`). This extends the Phase 1
   dead-audio gate (which only caught pure digital silence, peak < -80 dBFS)
   to real-world noise: room tone, breaths, keyboard clicks, fan hum. The
   decoder never runs on them, so it cannot hallucinate on them and the CPU
   is not spent.
2. Edge trimming — the utterance is cut to its speech region (plus a small
   pad) so Whisper decodes actual speech instead of seconds of leading/
   trailing silence: lower latency, fewer derailment opportunities.
3. Segmentation — utterances whose speech span exceeds
   `split_max_seconds` are split into sentence-sized chunks at natural pauses
   (silence ≥ `split_silence_ms`) and decoded separately; the texts are
   joined. This is a server-side safety net for clients that do not do their
   own sentence endpointing (the Phase 1 frontend does, ADR-0007), and it
   keeps any single decode call bounded.

Excessive segmentation is explicitly avoided (master prompt §9): splitting
only engages at ≥ 700 ms pauses or the 24 s span cap, never inside fluent
speech.

The VAD model is the Silero v6 ONNX file bundled inside the faster-whisper
package assets — no new dependency, no download, ~2 MB, loaded lazily and
thread-safely. All CPU work runs in the STT executor, never on the event
loop. `enabled=False` turns the whole pipeline into a pass-through (Phase 1
behavior, kill switch via STT/VAD env vars).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from .pcm import pcm16_bytes_to_float32

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SpeechRegion:
    """A time span (seconds) in which speech was detected."""

    start_s: float
    end_s: float

    @property
    def duration_s(self) -> float:
        return max(0.0, self.end_s - self.start_s)


@dataclass(frozen=True)
class VadAnalysis:
    """Raw VAD result for one utterance buffer."""

    duration_s: float
    regions: tuple[SpeechRegion, ...]

    @property
    def speech_s(self) -> float:
        return sum(region.duration_s for region in self.regions)

    @property
    def has_speech(self) -> bool:
        return bool(self.regions)


@dataclass(frozen=True)
class UtterancePlan:
    """What to do with a flushed utterance — decided BEFORE any STT call.

    `chunks` empty  → no speech (the gate rejects the utterance);
    `chunks` len 1  → single (edge-trimmed) decode;
    `chunks` len >1 → utterance was segmented at natural pauses.
    """

    chunks: tuple[bytes, ...]
    source_duration_s: float
    speech_s: float
    dropped_s: float  # non-speech seconds removed by gating/trimming
    split: bool


class UtterancePlanner(Protocol):
    """The contract the app/WS/health layers need — SileroVAD implements it,
    tests substitute fakes with the same shape."""

    enabled: bool

    def ensure_loaded(self) -> None: ...

    def describe(self) -> dict[str, object]: ...

    def plan(self, pcm16: bytes, *, sample_rate: int) -> UtterancePlan: ...


def plan_from_regions(
    analysis: VadAnalysis,
    pcm16: bytes,
    *,
    sample_rate: int,
    trim_pad_ms: int,
    split_silence_ms: int,
    split_max_seconds: float,
    min_speech_ms: int,
    max_chunks: int = 8,
) -> UtterancePlan:
    """Pure planner: map a VAD analysis to gate / trim / split decisions.

    Testable without any model — regions are plain data.
    """
    bytes_per_sample = 2
    total_samples = len(pcm16) // bytes_per_sample
    duration_s = analysis.duration_s
    speech_s = analysis.speech_s

    if total_samples == 0:
        return UtterancePlan(
            chunks=(), source_duration_s=0.0, speech_s=0.0, dropped_s=0.0, split=False
        )

    # --- gate: not enough recognizable speech → reject before STT ----------
    if not analysis.has_speech or speech_s * 1000.0 < min_speech_ms:
        return UtterancePlan(
            chunks=(),
            source_duration_s=duration_s,
            speech_s=speech_s,
            dropped_s=duration_s,
            split=False,
        )

    pad_s = trim_pad_ms / 1000.0

    # --- group regions into utterance chunks -------------------------------
    # A group closes at a pause ≥ split_silence_ms (natural sentence break)
    # or when its span exceeds split_max_seconds (hard cap safety net).
    groups: list[list[SpeechRegion]] = []
    for region in analysis.regions:
        current = groups[-1] if groups else None
        if current is None:
            groups.append([region])
            continue
        gap_s = region.start_s - current[-1].end_s
        span_s = region.end_s - current[0].start_s
        if gap_s >= split_silence_ms / 1000.0 or span_s >= split_max_seconds:
            groups.append([region])
        else:
            current.append(region)

    # Bound the number of decode calls: if a pathological buffer produced
    # more groups than allowed, merge the extras into the last group.
    while len(groups) > max_chunks:
        extra = groups.pop()
        groups[-1].extend(extra)

    chunks: list[bytes] = []
    for group in groups:
        first, last = group[0], group[-1]
        start_sample = max(0, int((first.start_s - pad_s) * sample_rate))
        end_sample = min(total_samples, int(np.ceil((last.end_s + pad_s) * sample_rate)))
        if end_sample <= start_sample:
            continue
        chunk = pcm16[start_sample * bytes_per_sample : end_sample * bytes_per_sample]
        if chunk:
            chunks.append(chunk)

    if not chunks:
        # Defensive: speech regions existed but slicing produced nothing.
        chunks = [pcm16]

    kept_s = sum(len(c) / bytes_per_sample / sample_rate for c in chunks)
    return UtterancePlan(
        chunks=tuple(chunks),
        source_duration_s=duration_s,
        speech_s=speech_s,
        dropped_s=max(0.0, duration_s - kept_s),
        split=len(chunks) > 1,
    )


class SileroVAD:
    """Silero VAD (ONNX, bundled with faster-whisper) + utterance planning."""

    name = "silero-vad"

    def __init__(
        self,
        *,
        enabled: bool = True,
        threshold: float = 0.5,
        min_speech_ms: int = 120,
        trim_pad_ms: int = 200,
        split_silence_ms: int = 700,
        split_max_seconds: float = 24.0,
        max_chunks: int = 8,
    ) -> None:
        self.enabled = bool(enabled)
        self.threshold = float(threshold)
        self.min_speech_ms = int(min_speech_ms)
        self.trim_pad_ms = int(trim_pad_ms)
        self.split_silence_ms = int(split_silence_ms)
        self.split_max_seconds = float(split_max_seconds)
        self.max_chunks = int(max_chunks)
        self._loaded = False
        self._load_lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._loaded

    def ensure_loaded(self) -> None:
        """Warm the ONNX session (thread-safe, lazy — like the STT model)."""
        if self._loaded:
            return
        with self._load_lock:
            if self._loaded:
                return
            from faster_whisper.vad import get_vad_model  # heavy import deferred

            get_vad_model()  # cached global session over the bundled ONNX
            self._loaded = True
            logger.info("silero VAD loaded (faster-whisper bundled assets)")

    def describe(self) -> dict[str, object]:
        return {
            "provider": self.name,
            "enabled": self.enabled,
            "loaded": self._loaded,
        }

    def analyze(self, pcm16: bytes, *, sample_rate: int) -> VadAnalysis:
        """Detect speech regions. Pass-through when disabled."""
        duration_s = len(pcm16) / 2 / sample_rate if sample_rate > 0 else 0.0
        if not self.enabled or not pcm16:
            regions = (
                (SpeechRegion(start_s=0.0, end_s=duration_s),) if pcm16 else ()
            )
            return VadAnalysis(duration_s=duration_s, regions=regions)

        self.ensure_loaded()
        from faster_whisper.vad import VadOptions, get_speech_timestamps

        audio = pcm16_bytes_to_float32(pcm16, sample_rate=sample_rate)
        options = VadOptions(
            threshold=self.threshold,
            min_speech_duration_ms=self.min_speech_ms,
            max_speech_duration_s=60.0,  # analysis must not force-split; planner decides
            min_silence_duration_ms=100,  # fine-grained regions → tight trim points
            speech_pad_ms=0,  # padding is applied by the planner, not here
        )
        stamps = get_speech_timestamps(audio, options, sampling_rate=16_000)
        regions = tuple(
            SpeechRegion(start_s=s["start"] / 16_000, end_s=s["end"] / 16_000)
            for s in stamps
            if s["end"] > s["start"]
        )
        return VadAnalysis(duration_s=duration_s, regions=regions)

    def plan(self, pcm16: bytes, *, sample_rate: int) -> UtterancePlan:
        """Gate + trim + segment one utterance (runs inside the executor)."""
        analysis = self.analyze(pcm16, sample_rate=sample_rate)
        return plan_from_regions(
            analysis,
            pcm16,
            sample_rate=sample_rate,
            trim_pad_ms=self.trim_pad_ms,
            split_silence_ms=self.split_silence_ms,
            split_max_seconds=self.split_max_seconds,
            min_speech_ms=self.min_speech_ms,
            max_chunks=self.max_chunks,
        )
