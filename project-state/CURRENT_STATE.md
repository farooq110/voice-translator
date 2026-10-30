# CURRENT_STATE

_Last updated: 2026-10-31 — post-approval bugfix round (KI-006) complete_

## Phase

**Phase 1 — Project Foundation and Browser STT MVP** — **implementation,
verification COMPLETE; user-reported Phase 1 defects (wrong language, latency)
FIXED and re-verified. Awaiting explicit user approval for Phase 2.**

## Current task

None — KI-006 fix round complete (see ADR-0006, KNOWN_ISSUES.md); approval
gate active (master prompt §23).

## Completed work

- Repo + environment inspected (empty scaffold, Python 3.12, uv 0.13.6, 2 vCPU, no GPU, network OK).
- Foundation: `prompt.md`, `AGENT.md`, project-state files, platform dev shim (`dev.sh`→`start.sh`), `.gitignore`.
- Backend: uv project; settings/logging/health; protocol v1 (pydantic, strict); session manager (bounded); `/ws/audio` state machine; STT provider abstraction + faster-whisper CPU int8 (lazy load, executor off-loop); model download script; benchmark; live E2E script.
- Frontend: Vite + React 19 + strict TS; protocol mirror with runtime validation; 18-language catalogue; WS client (backoff reconnect + ping/pong heartbeat); AudioWorklet capture (16 kHz PCM16, streaming resampler fallback, level meter); session hook; mobile-first UI (RTL-aware transcript); error banner; localStorage persistence.
- **KI-006 fix (user report, this round):** STT language pinned to the hello config's `native_language` (primary subtag); dead-audio gate (silence rejected before the model → `no_speech_detected`); Whisper no-speech segment filter; single-temperature greedy decode; "Transcribing…" pending indicator (status line + mic spinner); new protocol error code documented; ADR-0006.
- Tests: backend 42 passed + 1 documented xfail; frontend 23 passed + typecheck + lint + build clean.
- Real audio: edge-tts neural fixtures (en/ur/es) transcribed by the real model — pinned-language labels deterministic (ur/en/es, p=1.00); benchmark recorded (auto vs forced: 41–45% faster; RTF 0.10–0.13 forced).
- Live E2E: 3/3 PASS against `bash start.sh` on :3000 (per-fixture pinned language); live silence-guard check PASS.
- Browser smoke test (headless Chromium): render ✓, WS connected ✓, defaults ✓, graceful no-mic error ✓, persistence ✓, desktop+mobile visuals ✓ (screenshots in `download/phase1-screens/`).
- Docs: README, docs/{architecture,audio-protocol,testing,deployment}.md, 6 ADRs, all project-state files.

## Verified work (evidence)

- `backend`: `uv run pytest` → **42 passed, 1 xfailed** (xfail = documented KI-002 auto-detect behavior, not a hidden failure).
- Real-model tests (`-m stt_real`): **5 passed, 1 xfailed** — ur forced → `ur` p=1.00 Arabic script; 2 s digital silence → empty text (no hallucination).
- `frontend`: `tsc --noEmit` ✓ · `eslint --max-warnings 0` ✓ · `vitest run` → **23 passed** · `vite build` ✓.
- Live server: `/health` 200 (`stt.loaded: true`); `/` serves the rebuilt SPA; worklet asset 200.
- `scripts/e2e_synthetic_check.py` → **all E2E checks passed** (en/en es/es ur/ur, p=1.00 each; `processing_ms` 537–600).
- `scripts/silence_guard_check.py` → **PASS** (`no_speech_detected` on 2 s of digital silence).
- Benchmark: auto 1209–1252 ms vs forced 662–745 ms avg (41–45% faster); live `processing_ms` 940–999 → 537–600 ms.
- Git history: one commit per coherent work block.

## Actual test results

See `docs/testing.md` §2–§7 (full tables) — recorded 2026-10-31, updated after the KI-006 fix.

## Known issues

`project-state/KNOWN_ISSUES.md`: KI-001 (no VAD — by design, Phase 2),
KI-002 (auto-detect ur/hi label confusion — mitigated in production by the
pinned language; Phase 3 design input), KI-003 (buffered audio discarded on
disconnect — accepted), KI-004 (real-device/background behavior untested —
Phase 6), KI-006 (wrong-language + latency user report — RESOLVED with
verification numbers).

## Blockers

None.

## Next task

**Phase 2 — Voice Activity Detection and Audio Optimization** — NOT started;
requires the user's explicit approval of the Phase 1 completion report.

## Phase approval required?

**YES** — the question
_"The current phase has been completed and reviewed. Do you approve starting
the next phase?"_
has been asked; no Phase 2 work may begin until the user answers affirmatively.
