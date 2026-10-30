# TASKS

Status vocabulary: `TODO`, `IN PROGRESS`, `BLOCKED`, `DONE`.
A task may only be marked `DONE` after implementation **and** verification.

## Phase 1 — Project Foundation and Browser STT MVP

| ID | Task | Status |
| --- | --- | --- |
| P1-01 | Inspect existing repo + environment; report state and gaps | DONE |
| P1-02 | Foundation: master prompt (`prompt.md`), `AGENT.md`, project-state files, platform dev shim, `.gitignore` | DONE |
| P1-03 | Backend scaffold: uv project, settings, logging, health endpoint | IN PROGRESS |
| P1-04 | Backend: audio protocol v1 models + validation | TODO |
| P1-05 | Backend: session manager | TODO |
| P1-06 | Backend: WebSocket endpoint (`/ws/audio`) — hello / binary audio / flush / transcription flow | TODO |
| P1-07 | Backend: STT provider abstraction + faster-whisper (CPU) implementation | TODO |
| P1-08 | Backend: model download script + model pre-downloaded | TODO |
| P1-09 | Backend tests: protocol, validation, sessions, WS flow (fake STT) | TODO |
| P1-10 | Backend real-STT test + latency/memory benchmark (real audio fixtures) | TODO |
| P1-11 | Frontend scaffold: Vite + React + strict TS + ESLint + vitest | TODO |
| P1-12 | Frontend: protocol types + language config | TODO |
| P1-13 | Frontend: WS client (reconnect + heartbeat) + AudioWorklet mic capture (PCM16/16 kHz) | TODO |
| P1-14 | Frontend: UI — language selectors, mic button, transcript panel, connection status, error banner | TODO |
| P1-15 | Frontend tests + typecheck + production build | TODO |
| P1-16 | E2E: synthetic audio over live WebSocket (en/ur/es) | TODO |
| P1-17 | Browser smoke test against single-port server on :3000 | TODO |
| P1-18 | Documentation: README, docs/*, decisions, project-state finalized, CHANGELOG | TODO |
| P1-19 | Phase 1 completion report + request approval | TODO |

## Phase 2 — Voice Activity Detection and Audio Optimization (NOT APPROVED)

| ID | Task | Status |
| --- | --- | --- |
| P2-01 | Server-side VAD + silence detection + segmentation | TODO |
| P2-02 | Bounded buffers/queues, backpressure, cancellation of stale tasks | TODO |
| P2-03 | Continuous (hands-free) capture mode in UI | TODO |
| P2-04 | CPU/memory/latency measurements before vs after VAD | TODO |

## Phase 3+ — see `prompt.md` section 17 (Translation, TTS, MVP completion, mobile, Zoom, …)

All Phase 3+ tasks are `TODO` and **blocked until the respective phase is approved**.
