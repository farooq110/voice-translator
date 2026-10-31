# TASKS

Status vocabulary: `TODO`, `IN PROGRESS`, `BLOCKED`, `DONE`.
A task may only be marked `DONE` after implementation **and** verification.

## Phase 1 — Project Foundation and Browser STT MVP — COMPLETE

| ID | Task | Status |
| --- | --- | --- |
| P1-01 | Inspect existing repo + environment; report state and gaps | DONE |
| P1-02 | Foundation: master prompt (`prompt.md`), `AGENT.md`, project-state files, platform dev shim, `.gitignore` | DONE |
| P1-03 | Backend scaffold: uv project, settings, logging, health endpoint | DONE |
| P1-04 | Backend: audio protocol v1 models + validation | DONE |
| P1-05 | Backend: session manager | DONE |
| P1-06 | Backend: WebSocket endpoint (`/ws/audio`) — hello / binary audio / flush / transcription flow | DONE |
| P1-07 | Backend: STT provider abstraction + faster-whisper (CPU) implementation | DONE |
| P1-08 | Backend: model download script + model pre-downloaded (base, cached in-project) | DONE |
| P1-09 | Backend tests: protocol, validation, sessions, WS flow (fake STT) — 38 passing | DONE |
| P1-10 | Backend real-STT tests + latency/memory benchmark (edge-tts neural fixtures) | DONE |
| P1-11 | Frontend scaffold: Vite + React + strict TS + ESLint + vitest | DONE |
| P1-12 | Frontend: protocol types (runtime-validated) + 18-language config | DONE |
| P1-13 | Frontend: WS client (reconnect + heartbeat) + AudioWorklet mic capture (PCM16/16 kHz) | DONE |
| P1-14 | Frontend: UI — language selectors, mic button, transcript panel (RTL), connection status, error banner | DONE |
| P1-15 | Frontend tests (23) + typecheck + lint + production build | DONE |
| P1-16 | E2E: synthetic speech over live WebSocket — 3/3 PASS on :3000 | DONE |
| P1-17 | Browser smoke test (headless Chromium) — render/connect/error-path/persistence/responsive | DONE |
| P1-18 | Documentation: README, docs/*, 5 ADRs, project-state finalized, CHANGELOG | DONE |
| P1-19 | Phase 1 completion report + request approval | DONE |
| P1-20 | **KI-006 fix (user report):** pin STT language to configured native language; dead-audio gate + no-speech hallucination filter; single-temperature decode; "Transcribing…" indicator; `no_speech_detected` protocol code; ADR-0006; re-verify (backend 42+1xfail, frontend 23, live E2E 3/3 with ur→ur, silence guard PASS, 41–45% faster) | DONE |

## Phase 2 — Voice Activity Detection and Audio Optimization (NOT APPROVED — do not start)

| ID | Task | Status |
| --- | --- | --- |
| P2-01 | Server-side VAD (e.g. Silero) + silence detection + segmentation | TODO |
| P2-02 | Continuous (hands-free) capture mode behind a feature flag | TODO |
| P2-03 | Bounded buffers/queues review, backpressure, cancellation of stale tasks | TODO |
| P2-04 | CPU/memory/latency measurements before vs after VAD; accuracy trade-offs documented | TODO |
| P2-05 | Real-device audio capture tests (desktop Chrome at minimum) | TODO |

## Phase 3+ — see `prompt.md` section 17

Translation decision engine (P3), TTS + playback (P4), two-person MVP (P5),
mobile compatibility (P6), Zoom (P7), extension (P8), more apps (P9),
production (P10). All `TODO` and **blocked until the respective phase is
approved**.
