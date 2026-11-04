# prompt.md — Phase 4 Scope & Delivery Record

**Phase:** 4 of 10 — Text-to-Speech and Browser Audio Playback
**Status:** implemented — full verification round complete — **pending user approval** (§23 workflow)
**Version:** v0.4.0
**ADR:** [docs/decisions/0013-tts-offline-piper.md](docs/decisions/0013-tts-offline-piper.md)

> This repository documents the **current phase only** in this file. The
> complete master prompt (25 sections, 10-phase roadmap, approval workflow)
> is maintained by the product owner outside the repository. The overall
> delivery roadmap and phase statuses live in
> [README.md § Roadmap](README.md#roadmap-master-prompt-17); durable agent
> rules live in [AGENT.md](AGENT.md).

---

## 1. Phase 4 scope (master prompt §17)

Implement:

- TTS provider abstraction.
- Urdu TTS evaluation and integration.
- English TTS evaluation and integration.
- Target-language speech generation.
- Browser-compatible audio output.
- Audio playback.
- Playback queue management.
- Prevention of overlapping audio.
- Cancellation of outdated audio.
- Provider failure handling.

**Original audio pass-through must bypass TTS.** Test actual generated
audio (no mocked-only claims — AGENT.md § 6).

## 2. Engine decision (ADR-0013)

**Piper** — offline VITS voices on ONNX Runtime, which the backend already
ships for faster-whisper: no torch, no API keys, no GPU, audio never leaves
the machine. MIT-licensed voices provisioned under `models/tts/<lang>/`
(git-ignored runtime artifact, idempotently provisioned by `start.sh`,
~190 MB for the three languages). Measured on 2 vCPU: synthesis RTF
≈ 0.06–0.08 warm; the FIRST synthesis into a language pays ~1.5 s of lazy
voice load (documented trade-off).

Provisioned voices: `ur_PK-fasih-medium` (Urdu, 22 050 Hz),
`en_US-lessac-medium` (English), `es_MX-ald-medium` (Spanish — covers the
Phase 3 `es↔en` pair). A missing voice is an honest, visible degradation —
never a wrong-language voice.

## 3. Non-negotiables applied to this phase

- **Offline by default**: local Piper synthesis, no keys, no network at
  inference time; cloud TTS remains possible via the provider abstraction
  (an explicit settings change, never the default).
- **Pass-through bypasses TTS**: only `translate` decisions (Rules 1–2)
  synthesize — asserted in WS-flow tests and verified live (the es fixture
  produces ZERO audio frames).
- **Replaceable providers**: `TTSProvider` interface with `piper` (default),
  `fake` (deterministic sine stub for tests), `none`; the WebSocket layer is
  provider-agnostic.
- **Bounded resources**: 30 syntheses/min sliding window, ≤ 1 concurrent,
  10 s hard timeout on a dedicated single-thread executor, 800-char text
  cap — no unbounded memory or runaway work.
- **Honest degradation**: every synthesis failure is a non-fatal,
  utterance-scoped protocol error; the translated TEXT is always kept.
- **Testing honesty**: every claim below maps to an executed test or probe
  (§ 6).

## 4. Delivered — v0.4.0

- **TTS provider abstraction** (`backend/app/tts/`): `base.py` (ABC +
  typed errors), `piper_local.py` (lazy per-language voice load, discovery
  by scanning `models/tts/<lang>/*.onnx`), `fake.py` (deterministic sine
  provider, seeded per (language, text)), `service.py` (bounded execution:
  rate limit, concurrency, timeout, outcomes — mirrors
  `TranslationService`).
- **Protocol v1 additive change** (no breaking change): `audio` JSON
  metadata event (`id`, `language`, `encoding=pcm_s16le`, `sample_rate`,
  `bytes`, `duration_ms`, `synthesis_ms`, `provider`) followed by exactly
  one binary frame of raw PCM16 mono — the pairing rides WebSocket
  ordering; metadata + binary are sent under the same per-connection lock.
  5 new utterance-scoped non-fatal error codes: `tts_failed`,
  `tts_timeout`, `tts_rate_limited`, `tts_unsupported_language`,
  `tts_text_too_long`.
- **Cancellation of outdated synthesis**: per-connection task tracking;
  in-flight syntheses are cancelled on disconnect (mirrors Phase 3
  translation cancellation); results for closed sockets are dropped.
- **Browser playback** (`frontend/src/services/playback.ts`):
  `SpeechPlayer` over the Web Audio API — Int16→Float32 conversion,
  strictly SEQUENTIAL scheduling (each clip starts at
  `max(now, previous end)` — overlap impossible by construction), bounded
  queue (4 pending clips, oldest dropped when full, remaining clips
  re-based so a cancelled/dropped clip never leaves silence), `cancel(id)`
  (line deleted), `stopAll()` (connection lost / user stop / mute),
  AudioContext unlocked on the mic gesture (autoplay policy).
- **wsClient pairing**: the `audio` metadata event is paired with the next
  binary frame and delivered as one unit (`onAudio`); stray binary frames
  are counted, never crash; pending metadata is cleared on close.
- **UI**: "Spoken translations" toggle (persists to localStorage; muting
  cancels scheduled audio), a pulsing `speaking` chip on the audible line,
  per-line voice-error chips (`no voice for this language · text kept`,
  …), and a `stop voice` button while audio is playing.
- **Observability**: `/health` now exposes the full `tts` section
  (provider, languages, voices, timeout, rate limit, concurrency, text
  cap) — a voice-less deployment is visible instead of falsely healthy
  (BUG-003 lesson applied to TTS). `start.sh` provisions voices
  idempotently and fails loudly.

## 5. Verification (all executed, not claimed)

- Backend: **155 passed + 1 xfailed** (`pytest`) — +25 Phase 4 tests:
  service bounds (timeout / rate limit / concurrency=1 / unsupported /
  too-long / disabled), provider discovery (incl. broken-voice rejection),
  WS flow (audio+binary pairing with size/RMS/duration assertions,
  pass-through bypass with `tts.calls == 0`, failure / unsupported /
  rate-limit error mapping, disabled keeps the Phase 3 shape), health
  exposure.
- `tts_real` marked test: real ur/en/es synthesis through
  `PiperTTSProvider` (RMS > 0.05, duration bounds, 22 050 Hz).
- Frontend: **81/81 vitest**, `tsc --noEmit` clean, ESLint 0 problems;
  production build 254.5 kB JS. +24 Phase 4 tests: SpeechPlayer
  (sequential scheduling, queue bound, re-basing after cancel, mute,
  autoplay resume), wsClient pairing (+ stray frame counter, stale-meta
  clear on close), `audio` event parsing (incl. rejects), voice-error
  helpers.
- **E2E TTS check 3/3 PASS** (`scripts/e2e_tts_check.py`, live server):
  Rule 1 ur→en — translation 109 ms, spoken English 2 577 ms of audio
  synthesized in 1 573 ms (first-use voice load included), PCM RMS 0.191,
  binary size = `bytes`; Rule 2 en→ur — 157 ms, spoken Urdu 3 424 ms of
  audio in 546 ms, RMS 0.188; Rule 3 es — pass-through with ZERO audio
  frames (TTS bypassed).
- Live regression: translation E2E **3/3 PASS**, silence guard **PASS**,
  browser smoke **10/10** (zero console errors) after the playback UI.
- Measured live synthesis (warm, in the Rule 2 probe): 3.4 s of Urdu audio
  in 546 ms (RTF ≈ 0.16 incl. load; 0.06–0.08 steady-state).

## 6. Known limitations (honest list)

- Piper `medium` voices are comprehension-grade, not human-equivalent; the
  Urdu voice (UAT dataset, eSpeak `ur` phonemization) can render
  anglicisms with an Urdu accent in mixed-language sentences.
- First synthesis into a language pays ~1.5 s of lazy voice load (later
  sentences are RTF ≈ 0.06–0.08); a startup warm-load of all voices would
  trade ~4.5 s boot time + ~300 MB RSS for zero first-use latency — not
  chosen.
- Playback is desktop-browser verified (headless Chromium smoke + unit
  tests with an injected fake AudioContext); real-device audio behavior
  (iOS Safari low-power mode, background tabs) is **Phase 6** scope.
- Pass-through speech is intentionally never regenerated — the other
  participant hears the ORIGINAL audio for third languages (spec rule),
  not a voiced rendition of its transcript.
- Real-device testing (Android Chrome / iOS Safari) remains **Phase 6**.

## 7. Phase status & approval (master prompt §23)

Workflow applied: `COMPLETE → TEST → FIX → VERIFY ACCEPTANCE CRITERIA →
DOCUMENT → REPORT → REQUEST APPROVAL`.

| Acceptance criterion (§17) | Status |
| --- | --- |
| TTS provider abstraction | ✅ verified (code + tests, 3 providers) |
| Urdu TTS evaluation and integration | ✅ verified (real synthesis, RMS-checked) |
| English TTS evaluation and integration | ✅ verified (real synthesis, E2E) |
| Target-language speech generation | ✅ verified (speaks the DECISION target, both directions) |
| Browser-compatible audio output | ✅ verified (PCM16 + Web Audio, autoplay unlock) |
| Audio playback | ✅ verified (live E2E + smoke) |
| Playback queue management | ✅ verified (bounded queue, oldest-drop, unit tests) |
| Prevention of overlapping audio | ✅ verified (sequential scheduling — structurally impossible) |
| Cancellation of outdated audio | ✅ verified (line delete / stop / mute / disconnect) |
| Provider failure handling | ✅ verified (5 non-fatal utterance-scoped codes, text kept) |
| Pass-through bypasses TTS | ✅ verified (WS tests + live E2E: zero audio frames) |
| Actual generated audio tested | ✅ RMS/duration/size assertions, real voices |

**Current status:** implemented and fully verified (unit + WS-flow + real
synthesis + live E2E + browser smoke). **Phase 5 (complete browser MVP —
two-person sessions) has NOT been started** and will not start without the
user's explicit approval.
