# CHANGELOG

All meaningful project changes. Format based on Keep a Changelog.

## [0.2.0] — Phase 2 — Voice Activity Detection and Audio Optimization (2026-10-31)

### Added
- **Server-side Silero VAD pipeline** (`backend/app/audio/vad.py`, ADR-0009):
  every flush is gated (silence/noise → `no_speech_detected` in ~13 ms
  WITHOUT calling the STT — asserted 0 provider calls), edge-trimmed to its
  speech region (±200 ms; `audio_duration_s` still reports what the user
  spoke), and over-long speech spans (24 s) or pauses ≥ 700 ms segment the
  utterance into bounded decodes with texts joined in order. The Silero v6
  ONNX ships inside faster-whisper's assets — no new dependency.
- **Bounded STT admission / backpressure** (`backend/app/ws/backpressure.py`):
  global cap 16 + per-session cap 4 on utterances in flight; overflow answers
  a non-fatal `server_busy` (documented in the protocol spec) instead of
  queueing without bound; slot release is exception/timeout-safe
  (regression-tested).
- **Hands-free continuous mode** behind `VITE_FEATURE_HANDS_FREE` (default
  ON): toggle in the capture panel, persisted (`vt.hands_free`), status line
  "Hands-free listening…". Silence-only drains (20 s idle cap) no longer
  spam `no_speech_detected` banners (`noSpeechPolicy.ts`, unit-tested);
  endpointing/tail-capture stay on in both modes (they fixed KI-007/008).
- **Client uplink backpressure**: audio chunks are dropped while the socket
  send buffer exceeds 1 MiB (counted in `droppedChunkCount`); control JSON
  always passes.
- `/health` now reports `vad` (provider/enabled/loaded) and `stt_queue`
  (queued/max). New env vars: `VAD_GATE_ENABLED`, `VAD_THRESHOLD`,
  `VAD_MIN_SPEECH_MS`, `VAD_TRIM_PAD_MS`, `VAD_SPLIT_SILENCE_MS`,
  `VAD_SPLIT_MAX_SECONDS`, `STT_MAX_QUEUE_DEPTH`,
  `STT_MAX_PENDING_PER_SESSION`.
- `scripts/bench_vad.py` (gate cost, silence rejection, trim/accuracy) and
  `scripts/smoke_phase2.mjs` (headless browser smoke with Chrome's fake
  microphone; screenshots in `download/phase2-screens/`).

### Fixed
- The WS endpoint read the cached global `get_settings()` instead of the
  injected app settings — per-app overrides silently ignored (found by the
  new backpressure tests); it now reads `app.state.settings`.

### Verification
- Backend **65 passed + 1 documented xfail** (new: 9 VAD-planner, 5 real-Silero,
  7 WS/backpressure, 1 full-pipeline real-model test); frontend typecheck/
  lint clean, **33 tests** (+10), build ok.
- Live E2E 3/3 PASS (en/es/ur through the VAD pipeline), silence guard PASS,
  browser smoke **10/10** with the fake microphone.
- `bench_vad.py`: gate 10–13 ms; trim accuracy-neutral (identical Urdu text);
  peak RSS 1250 MiB (bench) / 1233 MiB resident (server, +170 MiB for Silero).
- Docs: ADR-0009, protocol spec (`server_busy`, VAD semantics, client
  obligations), architecture/testing/deployment/README, KI-001 → RESOLVED,
  TASKS/CURRENT_STATE/DECISIONS updated.

## [0.1.2] — Phase 1 bugfix rounds — KI-007 + KI-008 (2026-10-31)

User-reported: "language correct but content wrong/drifting"; then "tapping
stop immediately after speaking writes completely wrong things".

### Fixed (KI-007, ADR-0007)
- Model default `base` → `small` (measured: `base` garbles the ur fixture and
  drifts mid-sentence; `small` produces the correct text at RTF ≈ 0.32).
- Greedy `beam_size=1` → beam search `beam_size=5` with Whisper's
  quality-gated temperature ladder (recovers mid-sentence derailments).
- Decode-time Silero VAD (`vad_filter=True`, 500 ms min silence, 150→250 ms
  pad) trims non-speech before decode.
- Client-side sentence endpointing in the frontend hook: a ≥ 800 ms pause
  after ≥ 1.2 s of buffered audio flushes the sentence while the mic stays
  open (20 s hard cap) — results appear live, buffers are sentence-sized.

### Fixed (KI-008, ADR-0008)
- 700 ms tail-capture window on stop (UI: "Finishing… capturing the last
  words"; re-taps ignored; connection loss finalizes immediately).
- Server-side 250 ms edge padding before decode; VAD `speech_pad_ms` 150→250.
- Per-line ✕ delete button in the transcript list.

## [0.1.1] — Phase 1 bugfix round — KI-006 (2026-10-31)

User-reported: Urdu selected but transcripts appeared in Hindi/Persian-style
script and "random numbers"; 2–3 s perceived latency.

### Fixed
- **Wrong language/script/digits:** STT was running with automatic language
  detection although the user's native language was configured — the hint is
  now derived from `hello.config.native_language` (primary subtag) and pinned
  in the engine (`language_hint` on `STTProvider.transcribe_pcm`). Labels are
  deterministic (`language` = configured language, p=1.00); wrong-script
  output from ur/hi/fa confusion can no longer occur for the user's own mic.
- **Silence hallucination:** 2 s of digital silence decoded into a
  447-character pseudo-Urdu string (1594 ms) — a dead-audio gate now rejects
  peak-silent buffers before the model runs, and Whisper's no-speech segment
  filter drops classic hallucination segments. Fully filtered decodes surface
  as the new non-fatal `no_speech_detected` error (documented in
  `docs/audio-protocol.md`) instead of an empty/garbage transcript.
- **Latency:** language pinning skips the per-flush detection pass and the
  temperature-retry ladder is removed (`temperature=0.0`, `beam_size=1`) —
  measured 41–45% faster (ur fixture 1252→745 ms in-process; live
  `processing_ms` 940–999 → 537–600 ms). The UI now shows a "Transcribing…"
  indicator (status line + mic-button spinner) while a flush is in flight.

### Changed
- `e2e_synthetic_check.py`: per-fixture connection with the fixture's
  `native_language` (mirrors the real app; expectations now deterministic).
- `bench_stt.py`: benchmarks auto-detect vs pinned language side by side.
- Tests: 3 new WS-flow regression tests (hint propagation, regional-subtag
  reduction, no-speech error) + 3 new real-model tests (forced-label,
  dead-silence gate, auto-detect xfail renamed); backend 42 passed + 1
  documented xfail. Frontend unchanged tests (23 passed).
- Docs: ADR-0006, protocol spec (pinned language + `no_speech_detected`),
  testing results, KNOWN_ISSUES (KI-002 mitigated, KI-006 resolved).

## [0.1.0] — Phase 1 — Browser STT MVP (2026-10-31)

### Added
- **Foundation:** master prompt stored as `prompt.md`; `AGENT.md` agent rules;
  project-state tracking (CURRENT_STATE/TASKS/DECISIONS/KNOWN_ISSUES/CHANGELOG);
  platform dev shim (`.zscripts/dev.sh` → `start.sh`, single-port :3000);
  `.gitignore`.
- **Backend (Python 3.12, FastAPI, uv):**
  - audio WebSocket protocol **v1** (versioned pydantic contracts, strict
    validation, documented error codes — `docs/audio-protocol.md`);
  - `/ws/audio` state machine: hello/ready handshake, binary framing rules,
    bounded buffering with 30 s auto-flush, flush→transcription flow,
    idle-timeout reap, stale-result dropping;
  - bounded session manager (`MAX_SESSIONS`, cleanup on disconnect);
  - `STTProvider` abstraction + `FasterWhisperSTT` (CPU int8, lazy load,
    beam=1, automatic language detection, low-confidence flag);
  - `/health` (status, engine info, session stats, uptime);
  - centralized settings (env/.env), privacy-conscious logging;
  - scripts: `download_model.py`, `bench_stt.py`, `e2e_synthetic_check.py`.
- **Frontend (React 19 + Vite 6 + TypeScript 5 strict, no `any`):**
  - AudioWorklet mic capture: AudioContext(16 kHz) + streaming linear
    resampler fallback → PCM16, 100 ms chunks + input level;
  - `TranslatorWsClient`: hello handshake, backoff reconnect, ping/pong
    heartbeat with dead-connection detection, runtime-validated server events;
  - `useTranslatorSession` state hook (business logic separated from UI);
  - mobile-first UI: 18-language selectors (defaults Urdu/English,
    localStorage-persisted), mic button with states, level meter, RTL-aware
    transcript with language/confidence/latency metadata, connection badge,
    dismissible error banner; graceful mic-permission/no-device errors.
- **Tests:** backend 38 passed + 1 documented xfail (protocol/validation/
  sessions/WS flow with injected FakeSTT + real-model STT tests); frontend
  23 passed (protocol parser, languages, WS client state machine); strict
  typecheck and lint clean; production build clean.
- **Real-audio validation:** edge-tts neural fixtures (en/ur/es) transcribed
  by the real model; benchmark recorded (RTF 0.13–0.18, ~1 s per 7 s
  utterance, 609 MiB RSS); live E2E 3/3 PASS; headless-browser smoke test
  (render/connect/error/persistence/responsive).
- **Docs:** README, docs/architecture.md, docs/audio-protocol.md,
  docs/testing.md (measured results + honesty list), docs/deployment.md,
  ADR-0001…0005.

### Fixed
- Protocol events: `type` discriminators needed defaults for construction
  (caught by tests).
- E2E script re-sent `hello` per fixture — corrected to one handshake +
  multi-utterance session (matches protocol and browser behavior).

### Known issues at release
KI-001 (no VAD — Phase 2), KI-002 (ur/hi label confusion — Phase 3 design
input), KI-003 (buffered audio discarded on disconnect — accepted), KI-004
(real devices untested — Phase 6). See project-state/KNOWN_ISSUES.md.
