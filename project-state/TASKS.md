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

## Phase 2 — Voice Activity Detection and Audio Optimization — COMPLETE

| ID | Task | Status |
| --- | --- | --- |
| P2-01 | Server-side VAD (Silero, bundled with faster-whisper) + silence detection + segmentation (gate → trim → split, ADR-0009) — 21 new backend tests incl. real-Silero + full-pipeline real-model | DONE |
| P2-02 | Continuous (hands-free) capture mode behind `VITE_FEATURE_HANDS_FREE` — toggle, persistence, silence-drain suppression, status wording; endpointing/tail-capture stay on in both modes | DONE |
| P2-03 | Bounded buffers/queues review → global (`STT_MAX_QUEUE_DEPTH=16`) + per-session (`STT_MAX_PENDING_PER_SESSION=4`) admission caps answered `server_busy`; exception-safe slot release; stale-result dropping verified; client uplink drop policy > 1 MiB; settings-injectability bug found & fixed | DONE |
| P2-04 | CPU/memory/latency measurements before vs after VAD (`scripts/bench_vad.py`): gate 10–13 ms, trim accuracy-neutral (identical ur text), RSS +~170 MiB for Silero; trade-offs documented in docs/testing.md §5a/§6 | DONE |
| P2-05 | Real-device capture tests — sandbox proxy: headless Chromium with Chrome's fake microphone, 10/10 smoke checks (`scripts/smoke_phase2.mjs`, screenshots `download/phase2-screens/`); manual desktop-Chrome checklist documented (docs/testing.md §8); full real-device matrix remains Phase 6 (KI-004) | DONE (sandbox scope; human checklist provided) |

## Phase 2 completion

- Approval: user message "After that start the work for Phase Two and
  complete it" (2026-10-31) — explicit approval per master prompt §23.
- Verification: backend 65 passed + 1 documented xfail; frontend 33 passed +
  typecheck/lint/build clean; live E2E 3/3 PASS; silence guard PASS; browser
  smoke 10/10; benchmarks recorded (docs/testing.md).
- Docs: ADR-0009; protocol spec updated (server_busy, VAD semantics);
  architecture/deployment/README updated; KI-001 RESOLVED.

## Phase 2 defect round 2 — KI-009 (user report, 2026-11-01) — COMPLETE

| ID | Task | Status |
| --- | --- | --- |
| P2-R2-01 | Continuous-speech latency (ADR-0010): client endpointing v2 — soft cap 4.5 s + 300 ms micro-pause, hard cap 20 s → 9 s; pending-decode counter keeps the Transcribing… indicator honest while flushes overlap | DONE |
| P2-R2-02 | Transcript panel newest-first (user request); scroll-to-bottom removed; per-line ✕ delete unaffected | DONE |
| P2-R2-03 | `scripts/e2e_continuous_speech_check.py` — live old-vs-new benchmark on 22.5 s compressed-silence ur speech; old baseline reproduced (first text 22.2 s), new PASS (5/5 utterances, first text 6.6 s, flush→text 1.9–2.2 s) | DONE |
| P2-R2-04 | Re-verification: backend 65 passed + 1 xfail; frontend tsc/lint/33 tests/build clean; live E2E 3/3 PASS; silence guard PASS; built bundle carries ADR-0010 constants | DONE |
| P2-R2-05 | Docs: ADR-0010; testing.md §1/§2/§3/§5b/§6/§7/§9; KI-009 RESOLVED; CHANGELOG 0.2.1; DECISIONS D-012; CURRENT_STATE; APP_VERSION 0.2.1 | DONE |
| P2-R2-06 | Recreate lost `download/Phase-One.zip` (sandbox wiped `download/` between sessions): Phase-1-final tree @ `3c1b5c2` (tag `phase-one-final`) + git-history bundle + MANIFEST, integrity verified | DONE |

## Repository & delivery — GitHub setup (2026-11-01) — COMPLETE (push pending credentials)

| ID | Task | Status |
| --- | --- | --- |
| GIT-01 | Repo-local Git identity set to the user's Repository Details (`user.name "m Farooq"`, `user.email khanbhi88@gmail.com`) and verified before any commit/push (user rule §3) | DONE |
| GIT-02 | Pre-publish history alignment — all 11 commits' author/committer → `m Farooq <khanbhi88@gmail.com>` (repo never pushed; `filter-branch`; old-hash doc references updated; tag `phase-one-final` re-pointed) | DONE |
| GIT-03 | Working-tree normalization — 14 files with accidental +x mode bits (content unchanged, 0 insertions/deletions) reverted to 644 | DONE |
| GIT-04 | `origin` remote → `https://github.com/farooq110/voice-translator.git`; branch `phase-2` created from `main` | DONE |
| GIT-05 | Recreate `download/Phase-One.zip` (sandbox wiped `download/` again): Phase-1-final tree @ tag `phase-one-final` + git-history bundle + MANIFEST, integrity verified | DONE |
| GIT-06 | Push to origin — authenticated via the user-provided SSH key (ed25519, fingerprint `SHA256:2lsCE9…`) through a paramiko `core.sshCommand` shim (sandbox has no ssh binary; GitHub's published host-key fingerprints pinned; remote URL switched to `git@github.com:farooq110/voice-translator.git`). Remote `main` (web-UI bootstrap: one-commit README "# voice-translator") superseded by force-push; `main` + `phase-2` both pushed | DONE |

## Phase 3+ — see `prompt.md` section 17

Translation decision engine (P3), TTS + playback (P4), two-person MVP (P5),
mobile compatibility (P6), Zoom (P7), extension (P8), more apps (P9),
production (P10). All `TODO` and **blocked until the respective phase is
approved**.
