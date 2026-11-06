# ADR-0013: Offline Piper TTS + sequential browser playback (Phase 4)

Date: 2026-11-04 · Status: accepted · Phase: 4

## Context

Phase 3 delivers translated text for `translate` decisions. The master prompt
(§17) now requires **target-language speech generation** and **browser audio
playback** with a TTS provider abstraction, playback queue management,
overlap prevention, cancellation of outdated audio and provider failure
handling — and the standing non-negotiable that **original pass-through
audio is never regenerated with TTS**.

Constraints inherited from the earlier phases: local CPU only (no GPU),
offline by default (no API keys, no audio/text leaving the machine,
privacy-first), replaceable providers, bounded resources, honest degradation.

## Decision

1. **Engine: Piper (VITS on ONNX Runtime).** `piper-tts` runs on
   onnxruntime — already a backend dependency via faster-whisper — so the
   dependency surface does not grow (no torch, no cloud SDK). Voices are
   MIT-licensed, per-language directories under `models/tts/<lang>/`,
   provisioned by `backend/scripts/download_tts_voices.py` (~190 MB for
   ur/en/es) and git-ignored like every other model artifact.
   Measured on 2 vCPU: RTF ≈ 0.06–0.08 (a ~2–3 s sentence synthesizes in
   ~0.15–0.2 s warm).

2. **Provider abstraction mirrors translation** (`app/tts/base.py`):
   `supports(language)` gates before any call (honest "no voice" instead of
   an error), `synthesize(text, language) → SynthesizedAudio(pcm16, rate)`,
   implementations: `piper` (default, lazy per-language load),
   `fake` (deterministic sine stub for tests), `none`.
   Voice loading is lazy (~1.5 s first utterance per language, documented).

3. **TTSService = bounded execution** (`app/tts/service.py`): sliding-window
   rate limit 30/min, ≤1 concurrent (CPU-bound), 10 s hard timeout on a
   dedicated single-thread executor, 800-char text cap; every failure maps
   to a non-fatal outcome → utterance-scoped protocol error
   (`tts_failed`, `tts_timeout`, `tts_rate_limited`,
   `tts_unsupported_language`, `tts_text_too_long`). The translated text is
   never touched.

4. **Transport: additive protocol v1 change.** One `audio` JSON metadata
   event (`id`, `language`, `sample_rate`, `bytes`, `duration_ms`,
   `synthesis_ms`, `provider`) followed by exactly one binary frame of raw
   PCM16 mono. Same socket ordering pairs them; old clients already ignore
   binary frames. With TTS disabled the Phase 3 shape is preserved exactly.

5. **Playback: a client-side `SpeechPlayer`** over the Web Audio API:
   Int16→Float32 conversion, strictly sequential scheduling (every clip
   starts at `max(now, previous end)` — overlap impossible by
   construction), bounded queue (4 pending clips, oldest dropped when full),
   re-basing of remaining clips after a cancellation (no silent gaps),
   `cancel(id)` when a line is deleted, `stopAll()` on connection loss /
   user stop / mute, and AudioContext unlock on the first user gesture
   (autoplay policy). A visible mute toggle persists to localStorage.

## Alternatives considered

- **Coqui TTS / MMS-TTS (torch):** heavier runtime (+2 GB deps), same CPU
  class; rejected for dependency bloat against the AGENT.md rule.
- **Cloud TTS (Google/AWS/Azure):** best Urdu quality, but violates the
  offline/privacy default; the provider abstraction leaves the door open as
  an explicit settings change (mirrors translation's `libre` option).
- **Browser SpeechSynthesis API:** zero server work, but voice availability
  for Urdu is platform-lottery, quality uncontrollable, and the server could
  not guarantee the same voice for both participants; also unusable for the
  later meeting-integration phases which need server-side audio.
- **Streaming TTS chunks:** unnecessary now (sentences are short; whole-
  clip delivery measured fast enough); the protocol allows evolution later.

## Consequences

- `/health` exposes `tts` (provider, languages, voices, bounds) so a
  voice-less deployment is visible, mirroring BUG-003's lesson.
- `start.sh` provisions voices idempotently and fails loudly.
- Pass-through utterances bypass TTS — asserted in WS tests and verified
  live by the E2E check (es fixture: zero audio frames).
- Known trade-offs: first synthesis per language pays the lazy voice load;
  Piper medium voices are honest-tier (comprehensible, not human-equivalent).
