# AGENT.md — Permanent Instructions for AI Coding Agents

This file contains durable rules for any AI agent (or human developer) working on
this repository. The full product master prompt is maintained by the product
owner outside the repository; this repo carries the current phase's scope and
delivery record in `prompt.md` (now Phase 3 — translation decision engine).
If this file and `prompt.md` disagree on that phase's scope, `prompt.md` wins;
propose an update to this file if a rule proves impractical.

---

## 1. Mission

Build a reliable, real-time AI voice translator where **only the translator user**
configures a Native Language and a Preferred Translation Language. The system
auto-detects both participants' spoken languages, translates only the configured
pair in the required direction, and forwards every other language as original
audio **without translation and without TTS**.

## 2. Phase discipline (strict)

The roadmap has 10 phases (see the Roadmap table in `README.md`; the current
phase's scope record is `prompt.md`). Work **only** on the
currently approved phase.

- Do not start a new phase without explicit user approval.
- A new chat, a status question, or general discussion is **not** approval.
- Phase completion workflow: `COMPLETE → TEST → FIX → VERIFY ACCEPTANCE
  CRITERIA → DOCUMENT → REPORT → REQUEST APPROVAL`, then stop and ask:
  _"The current phase has been completed and reviewed. Do you approve starting
  the next phase?"_
- Task workflow inside a phase: `IMPLEMENT → TEST → FIX → VERIFY → DOCUMENT →
  REPORT`. Do not start the next task while the current one has unresolved
  blocking problems.

## 3. Development strategy

Browser MVP first (mobile-first, responsive). Meeting integrations (Zoom first)
come **only after** the browser MVP and mobile compatibility are verified.
Do not build Electron, native mobile, or speculative integrations.

## 4. Non-negotiable product rules

- Only the translator user configures languages; never fixed Person A / Person B
  assignments; never hardcode a specific language pair in core logic.
- Translation decision engine must be a separate, unit-testable module
  (`native → preferred`, `preferred → native`, **everything else → pass-through**).
- Original pass-through audio must never be regenerated with TTS; avoid
  unnecessary STT/translation/TTS calls.
- Local CPU STT by default (no GPU requirement).
- Route audio to the correct recipient; prevent feedback loops.
- Keep the translation engine independent of meeting-integration adapters.

## 5. Engineering standards

- Frontend: React + TypeScript + Vite, **strict TypeScript, no `any`**.
- Backend: Python + FastAPI, dependencies managed with **uv** (lockfile committed).
- WebSocket for real-time audio; the protocol is versioned (see
  `docs/audio-protocol.md`) — evolve it via the `v` field, never by silent change.
- Separate business logic from UI; loosely coupled modules; provider interfaces
  (STT / translation / TTS) must stay replaceable.
- Centralize configuration; secrets and provider keys live in backend env vars
  only, never in the frontend, never committed.
- Avoid unnecessary dependencies and abstractions; prefer simple, measurable,
  maintainable code.
- Do not block the backend event loop with CPU-heavy work (run inference in
  worker threads/executors).

## 6. Testing honesty

- Tests are mandatory from Phase 1. Unit, integration, E2E, real-audio tests.
- Mocks are allowed for isolated unit tests, but **mocked tests must never be
  used to claim that real microphone capture, STT, translation, TTS, or meeting
  audio routing works**.
- Never claim a test passed if it was not executed; never claim coverage that
  was not measured; never claim unverified functionality works.
- Measure and record real numbers (latency, CPU, memory) in
  `docs/testing.md` and `project-state/CURRENT_STATE.md`.

## 7. Bug fixing

Reproduce → inspect logs/code → root cause → focused fix → regression test →
re-run relevant tests → verify → document in `project-state/KNOWN_ISSUES.md`.
Do not ignore bugs, do not rewrite unrelated modules, do not mark incomplete
features complete.

## 8. Documentation duties

Maintain (and update after every meaningful task and at each phase end):

| File | Purpose |
| --- | --- |
| `prompt.md` | Current phase scope & delivery record (Phase 3 — translation decision engine) |
| `AGENT.md` | This file — permanent agent instructions |
| `README.md` | Quickstart, architecture overview, limitations |
| `docs/architecture.md` | Components, data flow, extension points |
| `docs/audio-protocol.md` | Versioned WebSocket protocol spec |
| `docs/testing.md` | How to test + **actual measured results** |
| `docs/deployment.md` | Dev/prod deployment, env vars, security notes |
| `docs/decisions/` | Architecture decision records (ADRs) |
| `project-state/CURRENT_STATE.md` | Phase, task, verified work, results, next step |
| `project-state/TASKS.md` | TODO / IN PROGRESS / BLOCKED / DONE (DONE only after verification) |
| `project-state/DECISIONS.md` | Decision log (summary; details in ADRs) |
| `project-state/KNOWN_ISSUES.md` | Issue, repro, root cause, fix, verification, status |
| `project-state/CHANGELOG.md` | Meaningful changes |

Never claim documentation was updated unless it was actually edited.

## 9. New chat / project recovery

When resuming work in a fresh conversation:

1. Read `AGENT.md`, `prompt.md`, `project-state/CURRENT_STATE.md`,
   `project-state/TASKS.md`, `project-state/DECISIONS.md`,
   `project-state/KNOWN_ISSUES.md`.
2. Check `git status` / `git log` and inspect relevant source code.
3. Run the test suites where appropriate; compare docs with the implementation.
4. Identify discrepancies; find the earliest incomplete task in the approved
   phase; continue from there.
5. Do **not** restart the project or redo verified work without a technical
   reason. If required files are inaccessible, ask — never pretend to have
   inspected them.

## 10. Git rules

- Inspect `git status` before changing anything; preserve unrelated work.
- No destructive operations (`reset --hard`, `clean -fd`, force push) without
  explicit permission.
- Keep changes focused; use meaningful checkpoints; never claim a commit exists
  unless it does.

## 11. Quick reference

```bash
# Backend (from backend/)
uv sync                                   # install deps (creates .venv, uv.lock)
uv run python scripts/download_model.py   # pre-download Whisper model
uv run pytest                             # backend tests
uv run uvicorn app.main:app --port 8000   # dev server (API only)

# Frontend (from frontend/)
npm install
npm run dev        # Vite dev server :5173, proxies /health,/ws to :8000
npm run test       # vitest
npm run lint && npm run typecheck

# Single-port production start (serves built frontend + API on :3000)
bash start.sh      # platform dev.sh calls this automatically
```
