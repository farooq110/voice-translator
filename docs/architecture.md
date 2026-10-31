# Architecture

Phase 2 scope: browser → backend STT pipeline with a server-side Silero VAD
pipeline (gate → trim → segment) and bounded decode admission. Translation
(Phase 3), TTS (Phase 4) and meeting integrations (Phase 7+) attach at the
marked extension points without re-architecting.

## 1. Component overview

```
┌────────────────────────── Browser (React + Vite + TS strict) ──────────────────────────┐
│  App.tsx → useTranslatorSession (state/business logic)                                 │
│    ├── TranslatorWsClient   hello/ready handshake · binary upload · ping/pong ·        │
│    │                        reconnect w/ backoff · typed, validated events ·            │
│    │                        uplink backpressure (drop chunks > 1 MiB buffered)          │
│    ├── startMicCapture      getUserMedia → AudioContext(16 kHz) → AudioWorklet         │
│    │                        (pcm-capture: resample → PCM16 → 100 ms chunks + level)    │
│    ├── HandsFreeToggle      VITE_FEATURE_HANDS_FREE-flagged continuous-capture mode    │
│    │                        (ADR-0009; endpointing runs in both modes — ADR-0007)      │
│    └── UI components        LanguageSelector · MicButton · LevelMeter ·                │
│                             TranscriptPanel (RTL-aware, per-line ✕) ·                  │
│                             ConnectionBadge · ErrorBanner                              │
└───────────────────────────────────────┬────────────────────────────────────────────────┘
                          WebSocket /ws/audio (protocol v1, docs/audio-protocol.md)
┌───────────────────────────────────────▼────────────────────────────────────────────────┐
│                        Backend (Python 3.12 · FastAPI · uv)                            │
│  ws/audio_ws.py      protocol state machine, framing validation, bounded buffering,    │
│                      auto-flush at cap, stale-result dropping, per-event timing        │
│  ws/backpressure.py  SttQueue — global decode admission cap → `server_busy` (P2)       │
│  audio/vad.py        SileroVAD (ONNX bundled w/ faster-whisper): gate → trim →         │
│                      segment, BEFORE the STT model (P2, ADR-0009)                     │
│  sessions/manager.py bounded session registry (max_sessions, cleanup on disconnect)    │
│  audio/validation.py chunk rules (size, alignment)        audio/pcm.py (int16→float32) │
│  stt/base.py         STTProvider ABC  ←── EXTENSION POINT: translation (P3), TTS (P4)  │
│  stt/faster_whisper_stt.py  CTranslate2 CPU int8, lazy load, pinned language,          │
│                      beam search + decode-time VAD (ADR-0006/0007)                     │
│  api/health.py       /health (status, engine info, session stats, vad, stt_queue)      │
│  main.py             app factory (DI: settings/provider/vad injection), serves dist    │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

## 2. Threading & latency model

- The event loop never runs inference: `_transcribe_and_deliver` dispatches the
  VAD plan and the STT decode to a bounded `ThreadPoolExecutor`
  (`STT_WORKER_THREADS`, default 1) and awaits with `STT_TIMEOUT_SECONDS`
  (30 s per decode; split utterances get the cap × chunk count).
- **Admission control (P2):** a flush acquires one `SttQueue` slot before any
  work is spawned; the slot is released in a `finally` (timeout/error-safe).
  Overflow → non-fatal `server_busy` — the queue can never grow without bound.
- Model load is lazy + background (`ensure_loaded` in warmup tasks at startup
  for Whisper and, when enabled, Silero); `/health` reports `stt.loaded`,
  `vad.enabled/loaded` and `stt_queue.queued/max` so readiness is observable.
- Cross-thread safety: the session buffer is only mutated on the event loop;
  the worker receives an immutable `bytes` snapshot (`take_buffer` swap-clear).
- Results for closed/stale sessions are dropped, never delivered (no stale
  playback in later phases). Cancellation semantics: `asyncio.wait_for`
  cancels the await; the CPU thread finishes its current chunk (cooperative,
  documented); shutdown cancels queued futures.

## 3. Memory bounds (Phase 2 guarantees)

- Per-session audio buffer: ≤ `MAX_UTTERANCE_SECONDS` (30 s = 960 KB), then
  auto-flush.
- Chunks > 256 KiB rejected; odd-alignment chunks rejected.
- **Decodes in flight: ≤ `STT_MAX_PENDING_PER_SESSION` per session and ≤
  `STT_MAX_QUEUE_DEPTH` globally (P2) — the executor queue cannot balloon.**
- **Client uplink: chunks are dropped while the socket send buffer exceeds
  1 MiB (P2) — no unbounded client-side queuing either.**
- Sessions: ≤ `MAX_SESSIONS`; buffers freed on disconnect.
- Transcripts are not retained server-side (only counters/durations in logs);
  client transcript list is capped at 50 entries.

## 4. Language handling (Phase 1 → Phase 3 path)

- Phase 1 detects the language for **every** utterance and returns
  `language` + `language_probability` + `low_confidence` — all inputs the
  Phase 3 decision engine needs:
  `native → translate to preferred · preferred → translate to native ·
  anything else → pass-through (no translation, no TTS)`.
- Known model quirk (KI-002): Whisper may label short Urdu utterances as
  `hi` (shared Hindustani). The decision engine must treat language identity
  carefully — candidates: script check on the transcription (Arabic vs
  Devanagari), utterance-context tracking, configurable language sets. This
  is deliberately NOT hacked into Phase 1 code; it is tracked and designed for.

## 5. Deployment topology

- **Single-port mode** (default, `start.sh` / platform `dev.sh`): FastAPI
  serves `frontend/dist` at `/`, the WebSocket at `/ws/audio`, health at
  `/health` — one origin, no CORS, port 3000 (the only externally exposed
  port in the sandbox).
- **Dev mode**: `uvicorn app.main:app --port 8000` + `npm run dev` (Vite :5173
  proxies `/health` and `/ws` to :8000). Same-origin from the browser's view.

## 6. Configuration

All settings centralized in `backend/app/config.py` (env vars / `backend/.env`,
see `backend/.env.example`). Secrets stay backend-side; the frontend receives
no keys (Phase 3+ providers will follow the same rule).

## 7. Testing strategy

Unit (protocol/session/validation/VAD planner) → WS flow with injected
FakeSTT + planner fakes (DI) → real-Silero VAD tests (bundled ONNX, no
download) → real-model tests with TTS-voice fixtures (marked `stt_real`,
skippable offline, incl. the full WS pipeline) → live E2E script
(`scripts/e2e_synthetic_check.py`) → headless browser smoke with Chrome's
fake microphone (`scripts/smoke_phase2.mjs`). Details and **measured
results**: docs/testing.md.
