# AI Voice Translator

Real-time, AI-powered voice translation for two-person conversations: **only
the translator user** configures a Native Language and a Preferred Translation
Language — the other person installs and configures nothing. The system
detects what language each side speaks, translates only the configured pair in
the required direction, and passes every other language through untouched.

> **Status — Phase 1 complete (of 10).** Working today: responsive browser UI
> (mobile-first), microphone capture, real-time streaming to the backend,
> **local CPU speech recognition (Whisper, no GPU)** with automatic language
> detection, live transcript with RTL support. Translation, TTS and meeting
> integrations arrive in the next phases — see the [roadmap](#roadmap).

![desktop](../download/phase1-screens/desktop.png)

## Quick start

```bash
# 1) Backend (Python 3.11–3.12 + uv)
cd backend
uv sync                                                  # deps + venv
HF_HOME=../models/huggingface uv run python scripts/download_model.py   # ~150 MB, once

# 2) Frontend (Node 20+)
cd ../frontend && npm install && npm run build

# 3) Run everything on one port (SPA + WebSocket + health)
cd .. && bash start.sh          # → http://localhost:3000
```

Open `http://localhost:3000`, pick your languages, tap the microphone, speak,
tap again → transcription appears with the detected language.

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
                     bounded buffer ──flush──▶ ThreadPoolExecutor
                                               faster-whisper (CPU, int8)
                                               + automatic language detection
        ◀──────────────── transcription event (text, language, confidence, timings)
```

Key properties: strict TypeScript (no `any`), versioned WebSocket protocol,
bounded buffers/queues (no unbounded memory), STT off the event loop,
stale-result rejection, auto-reconnect with backoff, privacy-conscious logging
(transcripts never logged above DEBUG, audio never persisted).

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
cd backend && uv run pytest                # 38 passed + 1 documented xfail
cd backend && HF_HOME=../models/huggingface uv run pytest -m stt_real   # real-model tests
cd frontend && npm run typecheck && npm run lint && npm run test
bash start.sh   # then:
cd backend && uv run python scripts/e2e_synthetic_check.py --url ws://localhost:3000/ws/audio
```

**Measured on 2 vCPU (no GPU):** transcription ≈ 1 s for a 7 s utterance
(RTF 0.13–0.18), 609 MiB peak RSS, real neural-voice speech verified for
English, Spanish and Urdu. Full numbers and the honesty list (what is *not*
tested yet): [docs/testing.md](docs/testing.md).

## Known limitations (Phase 1)

- Push-to-talk only — hands-free continuous mode needs VAD (**Phase 2**).
- Whisper may label short Urdu utterances as Hindi (ur/hi share spoken
  Hindustani) — transcription itself is correct Urdu; the Phase 3 decision
  engine will handle the pair explicitly ([KI-002](project-state/KNOWN_ISSUES.md)).
- No real-device testing yet (Android Chrome / iOS Safari / desktop Chrome —
  **Phase 6**); background-tab/lock-screen behavior is undocumented until then.
- No authentication — intended for private/LAN/sandbox deployment (**Phase 10**).
- Two-person sessions (second participant) arrive in **Phase 5**; Phase 1 is
  one mic → transcript.

## Roadmap (master prompt §17)

| Phase | Scope | Status |
| --- | --- | --- |
| 1 | Foundation + browser STT MVP | **complete — pending approval** |
| 2 | VAD + audio optimization | not started (needs approval) |
| 3 | Translation decision engine (bidirectional) | not started |
| 4 | TTS + browser playback | not started |
| 5 | Complete browser MVP (two participants) | not started |
| 6 | Mobile/browser compatibility on real devices | not started |
| 7 | Zoom integration (first meeting app) | not started |
| 8 | Chrome extension / advanced audio | not started |
| 9 | More meeting apps (one at a time) | not started |
| 10 | Production readiness & release | not started |
