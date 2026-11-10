# Architecture

Phase 7 scope: the complete browser pipeline (STT + decision engine +
translation + TTS, phases 1–4), the two-participant rooms (ADR-0014),
the mobile-honest capture (ADR-0016) and the **Zoom RTMS bridge**
(ADR-0019) — the first meeting integration, attached at the extension
points the earlier phases reserved without re-architecting anything.

## 1. Component overview

```
┌────────────────────────── Browser (React + Vite + TS strict) ──────────────────────────┐
│  App.tsx → useTranslatorSession (state/business logic)                                 │
│    ├── TranslatorWsClient   hello/ready handshake · binary upload · ping/pong ·        │
│    │                        reconnect w/ backoff · typed, validated events ·            │
│    │                        uplink backpressure (drop chunks > 1 MiB buffered) ·        │
│    │                        audio/relay metadata ⇄ binary frame pairing (P4/P5)       │
│    ├── startMicCapture      getUserMedia → AudioContext(16 kHz) → AudioWorklet         │
│    │                        (pcm-capture: resample → PCM16 → 100 ms chunks + level)    │
│    ├── SpeechPlayer (P4)    bounded playback queue: sequential Web Audio scheduling    │
│    │                        (no overlap), oldest-drop bound, cancel/stopAll,            │
│    │                        autoplay unlock on the mic gesture (ADR-0013)              │
│    ├── HandsFreeToggle      VITE_FEATURE_HANDS_FREE-flagged continuous-capture mode    │
│    ├── PlaybackToggle (P4)  mute spoken translations (persists; cancel-on-mute)        │
│    │                        (ADR-0009; endpointing runs in both modes — ADR-0007)      │
│    ├── PlaybackGuard (P5)   feedback-loop prevention: uplink muted while a REMOTE      │
│    │                        clip (peer voice / relay) is audible (+300 ms tail)        │
│    └── UI components        LanguageSelector (host only) · MicButton · LevelMeter ·    │
│                             RoomPanel (P5: invite code + copy link + peer status;      │
│                             guest variant shows the host's pair, no settings) ·        │
│                             TranscriptPanel (RTL-aware, per-line ✕, speaking chip,     │
│                             voice-error chips, speaker chips) · ConnectionBadge ·      │
│                             ErrorBanner                                                │
└───────────────────────────────────────┬────────────────────────────────────────────────┘
                          WebSocket /ws/audio (protocol v1, docs/audio-protocol.md)
┌───────────────────────────────────────▼────────────────────────────────────────────────┐
│                        Backend (Python 3.12 · FastAPI · uv)                            │
│  ws/audio_ws.py      protocol state machine, framing validation, bounded buffering,    │
│                      audio/relay event + binary frame sent under one lock              │
│                      auto-flush at cap, stale-result dropping, per-event timing,       │
│                      Phase 5: room-aware hello/join/rejoin, broadcast of               │
│                      transcripts+translations (speaker-tagged), recipient-only         │
│                      voice routing, original-audio pass-through relay, leave/linger    │
│  ws/rooms.py (P5)    Room + RoomManager — two-participant rooms, 6-char invite codes,  │
│                      linger-based reconnection (ROOM_LINGER_SECONDS), in-memory only   │
│  ws/backpressure.py  SttQueue — global decode admission cap → `server_busy` (P2)       │
│  audio/vad.py        SileroVAD (ONNX bundled w/ faster-whisper): gate → trim →         │
│                      segment, BEFORE the STT model (P2, ADR-0009)                     │
│  sessions/manager.py bounded session registry (max_sessions, cleanup on disconnect)    │
│  audio/validation.py chunk rules (size, alignment)        audio/pcm.py (int16→float32) │
│  translation/…       TranslationProvider ABC ← local CT2 OPUS-MT (offline) · libre ·    │
│                      fake + decision engine (P3, ADR-0012)                             │
│  tts/… (P4, ADR-0013) TTSProvider ABC ← piper (offline VITS on onnxruntime, voices     │
│                      under models/tts/<lang>/, lazy per-language load) · fake · none;  │
│                      TTSService = rate limit + concurrency cap + timeout               │
│  stt/faster_whisper_stt.py  CTranslate2 CPU int8, lazy load, pinned language,          │
│                      beam search + decode-time VAD (ADR-0006/0007)                     │
│  api/health.py       /health (status, engines, session stats, rooms, vad, stt_queue,   │
│                      translation.pairs, tts.languages, zoom bridge state)               │
│  main.py             app factory (DI: settings/stt/vad/translator/tts), serves dist    │
└────────────────────────────────────────────────────────────────────────────────────────┘

Phase 7 — the Zoom bridge (app/zoom/, ADR-0019; mounted only when enabled
AND credentialed):

```
Zoom cloud                                    Backend
──────────                                    ──────────────────────────────────────────
webhook  meeting.rtms_started ──────────────▶ POST /zoom/webhook (router.py)
  {meeting_uuid, rtms_stream_id,                ├─ x-zm-signature v0 check (signing.py)
   server_urls}                                 ├─ endpoint.url_validation challenge
                                                └─ ZoomBridge (bridge.py): ONE worker / meeting
  signaling WS ◀────────────────────────────── RtmsClient (rtms.py): handshake msg_type 1
  media WS    ◀──────────────────────────────    → media handshake (L16 · 16 kHz · mono · 20 ms)
    {msg_type:14 audio frames}                   → CLIENT_READY_ACK 7 · keep-alive 12→13
                                                   → UtteranceSegmenter (RMS pause + soft cap;
                                                     bounded drop-on-full queue)
                                                  PipelineClient (pipeline_client.py):
                                                   protocol-v1 client of OUR /ws/audio —
                                                   hello role=host (zoom pair, platform
                                                   "zoom-bridge") → binary PCM + flush
                                                        │
                                                        ▼
                                             the EXISTING pipeline (VAD → Whisper →
                                             decision → OPUS-MT → Piper) in a normal
                                             two-participant room
  GET /zoom/meetings ────────────────────────▶ invite code + live stats per meeting
(the translator user opens …/?room=CODE and is the room's guest — the
 meeting's translations and spoken voice arrive on THEIR device; nothing
 is injected back into the meeting in v1, KI-031)
```

## 2. Threading & latency model

- The event loop never runs inference: `_transcribe_and_deliver` dispatches the
  VAD plan and the STT decode to a bounded `ThreadPoolExecutor`
  (`STT_WORKER_THREADS`, default 1) and awaits with `STT_TIMEOUT_SECONDS`
  (30 s per decode; split utterances get the cap × chunk count).
- Translation (P3) and synthesis (P4) each run on their OWN single-thread
  executor (`translation_worker_threads` / `tts_worker_threads`) behind
  `asyncio.wait_for` timeouts — STT decodes never queue behind MT, and MT
  never queues behind synthesis.
- **Admission control (P2):** a flush acquires one `SttQueue` slot before any
  work is spawned; the slot is released in a `finally` (timeout/error-safe).
  Overflow → non-fatal `server_busy` — the queue can never grow without bound.
- Model load is lazy + background (`ensure_loaded` in warmup tasks at startup
  for Whisper and, when enabled, Silero; TTS voices load lazily on first use
  per language — ADR-0013); `/health` reports `stt.loaded`,
  `vad.enabled/loaded`, `stt_queue.queued/max`, `translation.pairs` and
  `tts.languages` so readiness is observable.
- Cross-thread safety: the session buffer is only mutated on the event loop;
  the worker receives an immutable `bytes` snapshot (`take_buffer` swap-clear).
- Results for closed/stale sessions are dropped, never delivered; in-flight
  translation AND synthesis tasks are cancelled on disconnect (P3/P4).
- Server→client audio delivery: the `audio`/`relay` metadata event and its
  binary PCM frame are sent under the same per-connection lock, so the
  pairing can never interleave with another event.
- **Rooms (P5):** the room layer is pure bookkeeping on the event loop (dict
  + linger timers) — it never blocks and never touches audio buffers; each
  connection keeps its own Session/VAD/STT pipeline. Room sends reuse the
  same per-connection send lock; a failing member never breaks another
  member's delivery.

## 3. Memory bounds (Phase 2 guarantees)

- Per-session audio buffer: ≤ `MAX_UTTERANCE_SECONDS` (30 s = 960 KB), then
  auto-flush.
- Chunks > 256 KiB rejected; odd-alignment chunks rejected.
- **Decodes in flight: ≤ `STT_MAX_PENDING_PER_SESSION` per session and ≤
  `STT_MAX_QUEUE_DEPTH` globally (P2) — the executor queue cannot balloon.**
- **Client uplink: chunks are dropped while the socket send buffer exceeds
  1 MiB (P2) — no unbounded client-side queuing either.**
- Sessions: ≤ `MAX_SESSIONS`; buffers freed on disconnect. Rooms: ≤
  MAX_SESSIONS (each member is a session); a room survives ≤
  `ROOM_LINGER_SECONDS` after its last member leaves, then is destroyed.
- Transcripts are not retained server-side (only counters/durations in logs);
  client transcript list is capped at 50 entries.

## 4. Language handling (Phase 1 → Phase 5 path)

- Phase 1 detects the language for **every** utterance and returns
  `language` + `language_probability` + `low_confidence` — all inputs the
  Phase 3 decision engine needs:
  `native → translate to preferred · preferred → translate to native ·
  anything else → pass-through (no translation, no TTS)`.
- Phase 4 adds the spoken leg: only `translate` decisions synthesize, in the
  DECISION's target language; pass-through audio is forwarded as captured
  and never regenerated (master prompt §17, ADR-0013).
- Phase 5 routes the outputs (ADR-0014): decisions are evaluated against the
  ROOM's pair (the host's) for BOTH participants; voices reach only the
  other participant; third-language audio is RELAYED as captured (16 kHz) to
  the peer.
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

Unit (protocol/session/validation/VAD planner/TTS service) → WS flow with
injected FakeSTT + planner fakes + fake translation + fake TTS (DI) →
real-Silero VAD tests (bundled ONNX, no download) → real-model tests (marked
`stt_real` / `translation_real` / `tts_real`, skippable offline, incl. the
full WS pipeline) → live E2E scripts (`scripts/e2e_synthetic_check.py`,
`scripts/e2e_translation_check.py`, `scripts/e2e_tts_check.py`) → headless
browser smoke with Chrome's fake microphone (`scripts/smoke_phase2.mjs`).
Details and **measured results**: docs/testing.md.
