# CHANGELOG

All meaningful project changes. Format based on Keep a Changelog.

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
