# AI Voice Translator

Real-time, AI-powered voice translation for two-person conversations: **only
the translator user** configures a Native Language and a Preferred Translation
Language — the other person installs and configures nothing. The system
detects what language each side speaks, translates only the configured pair in
the required direction, and passes every other language through untouched.

> **Status — Phase 2 complete (of 10).** Working today: responsive browser UI
> (mobile-first), microphone capture, real-time streaming to the backend,
> **local CPU speech recognition (Whisper, no GPU)** with the language pinned
> to your selection, live transcript with RTL support, **server-side Silero
> voice-activity gate (silence/noise never decoded)**, edge-trimmed and
> pause-segmented utterances, **hands-free continuous mode**, backpressured
> bounded queues, per-line transcript delete. Translation, TTS and meeting
> integrations arrive in the next phases — see the [roadmap](#roadmap).

![desktop](../download/phase2-screens/desktop.png)

## Quick start

```bash
# 1) Backend (Python 3.11–3.12 + uv)
cd backend
uv sync                                                  # deps + venv
HF_HOME=../models/huggingface uv run python scripts/download_model.py   # ~490 MB, once

# 2) Frontend (Node 20+)
cd ../frontend && npm install && npm run build

# 3) Run everything on one port (SPA + WebSocket + health)
cd .. && bash start.sh          # → http://localhost:3000
```

Open `http://localhost:3000`, pick your languages, tap the microphone, speak,
tap again → transcription appears in your language (the engine is pinned to
your selection — ADR-0006). Keep the mic open with **Hands-free** enabled and
sentences transcribe themselves at every pause.

Development mode (HMR): `cd backend && uv run uvicorn app.main:app --port 8000`
plus `cd frontend && npm run dev` (Vite :5173 proxies API/WS).

## How it works

```
Browser                        Backend (FastAPI, Python)
────────                       ─────────────────────────
getUserMedia → AudioWorklet
  (resample → PCM16, 16 kHz, 100 ms chunks)
        │  WebSocket /ws/audio — protocol v1 (docs/audio-protocol.md)
        ▼
                     bounded buffer ──flush──▶ Silero VAD gate (Phase 2):
                     reject silence/noise (~13 ms), trim edges, split at pauses
                                   │ speech only
                                   ▼
                     ThreadPoolExecutor (admission-capped → server_busy)
                     faster-whisper (CPU, int8) + language pinned to hello config
        ◀──────────────── transcription event (text, language, confidence, timings)
```

Key properties: strict TypeScript (no `any`), versioned WebSocket protocol,
bounded buffers + admission-capped decode queue with `server_busy`
backpressure (no unbounded memory anywhere), server-side VAD gate, STT off
the event loop, stale-result rejection, uplink chunk dropping on stall,
auto-reconnect with backoff, privacy-conscious logging (transcripts never
logged above DEBUG, audio never persisted).

## Project layout

```
prompt.md            product master prompt (requirements, roadmap, rules)
AGENT.md             permanent instructions for AI coding agents
backend/             FastAPI + faster-whisper (uv-managed; tests in backend/tests)
frontend/            React + Vite + TS strict (tests co-located in src)
docs/                architecture, protocol spec, testing results, deployment, ADRs
project-state/       CURRENT_STATE · TASKS · DECISIONS · KNOWN_ISSUES · CHANGELOG
start.sh             single-port production start (platform dev.sh calls this)
```

## Testing

```bash
cd backend && uv run pytest                # 65 passed + 1 documented xfail
cd backend && HF_HOME=../models/huggingface uv run pytest -m stt_real   # real-model tests
cd frontend && npm run typecheck && npm run lint && npm run test
bash start.sh   # then:
cd backend && uv run python scripts/e2e_synthetic_check.py --url ws://localhost:3000/ws/audio
cd backend && uv run python scripts/silence_guard_check.py --url ws://localhost:3000/ws/audio
node scripts/smoke_phase2.mjs               # headless browser smoke (fake mic)
```

**Measured on 2 vCPU (no GPU):** VAD gate ≈ 13 ms per flush (silence/noise
never reaches the model), transcription ≈ 2 s for a 7 s utterance via
small+beam5 (RTF ≈ 0.3), real neural-voice speech verified for English,
Spanish and Urdu (language labels deterministic, p=1.00), trim verified
accuracy-neutral. Full numbers and the honesty list (what is *not* tested
yet): [docs/testing.md](docs/testing.md).

## Known limitations (Phase 2)

- With AUTO-detection (used only in tests / Phase 3 for the other
  participant), Whisper may label short Urdu utterances as Hindi (ur/hi share
  spoken Hindustani). Your own mic is unaffected: the engine is pinned to
  your selected language ([ADR-0006](docs/decisions/0006-stt-native-language-hint.md),
  [KI-002](project-state/KNOWN_ISSUES.md)).
- No real-device testing yet (Android Chrome / iOS Safari / desktop Chrome —
  **Phase 6**); a 5-minute desktop-Chrome manual checklist is in
  [docs/testing.md §8](docs/testing.md). Background-tab/lock-screen behavior
  is undocumented until then.
- No authentication — intended for private/LAN/sandbox deployment (**Phase 10**).
- Two-person sessions (second participant) arrive in **Phase 5**; Phases 1–2
  are one mic → transcript.

## Roadmap (master prompt §17)

| Phase | Scope | Status |
| --- | --- | --- |
| 1 | Foundation + browser STT MVP | **complete** |
| 2 | VAD + audio optimization | **complete — pending approval** |
| 3 | Translation decision engine (bidirectional) | not started (needs approval) |
| 4 | TTS + browser playback | not started |
| 5 | Complete browser MVP (two participants) | not started |
| 6 | Mobile/browser compatibility on real devices | not started |
| 7 | Zoom integration (first meeting app) | not started |
| 8 | Chrome extension / advanced audio | not started |
| 9 | More meeting apps (one at a time) | not started |
| 10 | Production readiness & release | not started |
