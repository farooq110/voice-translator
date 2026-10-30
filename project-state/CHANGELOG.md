# CHANGELOG

All meaningful project changes. Format based on Keep a Changelog.

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
