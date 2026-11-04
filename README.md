# AI Voice Translator

Real-time, AI-powered voice translation for two-person conversations: **only
the translator user** configures a Native Language and a Preferred Translation
Language — the other person installs and configures nothing. The system
detects what language each side speaks, translates only the configured pair in
the required direction, and passes every other language through untouched.

> **Status — Phase 4 complete (of 10).** Working today: responsive browser UI
> (mobile-first), microphone capture, real-time streaming to the backend,
> **local CPU speech recognition (Whisper, no GPU)** with automatic language
> detection (detect-first, then a pinned decode — ADR-0012), live transcript
> with RTL support, **server-side Silero voice-activity gate (silence/noise
> never decoded)**, edge-trimmed and pause-segmented utterances,
> **hands-free continuous mode**, backpressured bounded queues, per-line
> transcript delete, the **Phase 3 translation decision engine** (your
> language is translated for the other side, theirs for you, any third
> language passes through untouched — **offline by default**, CTranslate2
> OPUS-MT, no API keys), and the **Phase 4 spoken output**: translated
> sentences are read aloud with **offline Piper neural voices (Urdu, English,
> Spanish — no GPU, no keys, audio never leaves the machine)** through a
> sequential playback queue that can never overlap, with a mute toggle and
> honest per-line degradation when a voice is missing. Meeting integrations
> arrive in the next phases — see the [roadmap](#roadmap).

![desktop](docs/screenshots/phase2/desktop.png)

## Quick start

```bash
# 1) Backend (Python 3.11–3.12 + uv)
cd backend
uv sync                                                  # deps + venv
HF_HOME=../models/huggingface uv run python scripts/download_model.py   # ~490 MB, once
uv run python scripts/download_translation_models.py     # ~900 MB, once (offline MT: ur↔en, es↔en)
uv run python scripts/download_tts_voices.py             # ~190 MB, once (offline TTS: ur, en, es)

# 2) Frontend (Node 20+)
cd ../frontend && npm install && npm run build

# 3) Run everything on one port (SPA + WebSocket + health)
cd .. && bash start.sh          # → http://localhost:3000
```

Open `http://localhost:3000`, pick your languages, tap the microphone, speak.
Each sentence shows its decision — `UR → EN` with the translated text below
(RTL-aware), or an honest pass-through chip for third languages — and the
translation is **spoken aloud** in the target language (offline voices;
pass-through speech is never repeated). Keep the mic open with **Hands-free**
enabled and sentences transcribe (and translate) at every pause. Mute spoken
output any time with the **Spoken translations** toggle.

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
                     detect_language (standalone pass → cluster/threshold resolve)
                     faster-whisper decode pinned to the RESOLVED language
        ◀──────────────── transcription event (text, language, decision…)
                                │ decision = translate (Rules 1–2 only)
                                ▼
                     TranslationService (rate-limited, ≤2 concurrent, 8 s timeout)
                     local CTranslate2 OPUS-MT  ·  libre (cloud, optional)
        ◀──────────────── translation event (translated text, provider, timings)
                                │ Phase 4 (ADR-0013) — translate decisions only
                                ▼
                     TTSService (rate-limited, ≤1 concurrent, 10 s timeout)
                     offline Piper voices: ur · en · es (models/tts/, no GPU)
        ◀──────────────── audio metadata event + binary PCM16 frame (22 050 Hz)
                     → browser SpeechPlayer: sequential queue, never overlaps,
                       cancel-on-delete/stop, mute toggle (ADR-0013)
```

Key properties: strict TypeScript (no `any`), versioned WebSocket protocol,
bounded buffers + admission-capped decode queue with `server_busy`
backpressure (no unbounded memory anywhere), server-side VAD gate, STT off
the event loop, stale-result rejection, uplink chunk dropping on stall,
auto-reconnect with backoff, privacy-conscious logging (transcripts never
logged above DEBUG, audio never persisted).

## Project layout

```
prompt.md            Phase 3 scope & delivery record (translation decision engine)
AGENT.md             permanent instructions for AI coding agents
backend/             FastAPI + faster-whisper (uv-managed; tests in backend/tests)
frontend/            React + Vite + TS strict (tests co-located in src)
docs/                architecture, protocol spec, testing results, deployment, ADRs
project-state/       CURRENT_STATE · TASKS · DECISIONS · KNOWN_ISSUES · CHANGELOG
start.sh             single-port production start (platform dev.sh calls this)
```

## Testing

```bash
cd backend && uv run pytest                # 155 passed + 1 documented xfail
cd backend && HF_HOME=../models/huggingface uv run pytest -m stt_real   # real-model STT tests
cd backend && uv run pytest -m translation_real                         # real offline MT tests
cd backend && uv run pytest -m tts_real                                 # real offline TTS tests
cd frontend && npm run typecheck && npm run lint && npm run test
bash start.sh   # then:
cd backend && uv run python scripts/e2e_synthetic_check.py --url ws://localhost:3000/ws/audio
cd backend && uv run python scripts/e2e_translation_check.py             # Rules 1–3 with real MT
cd backend && uv run python scripts/e2e_tts_check.py                     # Rules 1–3 + real spoken output
cd backend && uv run python scripts/silence_guard_check.py --url ws://localhost:3000/ws/audio
node frontend/scripts/smoke_phase2.mjs      # headless browser smoke (fake mic)
```

**Measured on 2 vCPU (no GPU):** VAD gate ≈ 13 ms per flush (silence/noise
never reaches the model), transcription ≈ 2 s for a 7 s utterance via
small+beam5 (RTF ≈ 0.3), real offline MT ≈ 110–160 ms per sentence, real
offline TTS at RTF ≈ 0.06–0.08 (a ~3 s sentence synthesizes in ~0.2 s warm;
the first sentence into a language pays ~1.5 s of voice load), spoken E2E
verified ur→en and en→ur with PCM amplitude checks. Full numbers and the
honesty list (what is *not* tested yet): [docs/testing.md](docs/testing.md).

## Known limitations (Phase 4)

- The local OPUS-MT models are honest-tier, not production-tier: everyday
  sentences translate correctly; nuanced Urdu→English is weak (small
  Tatoeba-scale models — [KI-012](project-state/KNOWN_ISSUES.md)). The
  provider abstraction makes upgrading a settings change
  ([ADR-0012](docs/decisions/0012-translation-decision-engine.md)).
- Urdu/Hindi share one spoken register: detection resolves them via the
  ur/hi cluster, and Urdu speech spoken to an en-native config may surface
  as Hindi-orthography text (KI-002; third-language corner case).
- No real-device testing yet (Android Chrome / iOS Safari / desktop Chrome —
  **Phase 6**); a 5-minute desktop-Chrome manual checklist is in
  [docs/testing.md §8](docs/testing.md). Background-tab/lock-screen behavior
  is undocumented until then.
- No authentication — intended for private/LAN/sandbox deployment (**Phase 10**).
- Piper `medium` voices are comprehension-grade, not human-equivalent; the
  Urdu voice (UAT dataset, eSpeak phonemization) can render anglicisms with
  an Urdu accent in mixed-language sentences. Upgrading is a settings change
  via the provider abstraction ([ADR-0013](docs/decisions/0013-tts-offline-piper.md)).
- The first synthesis into a language pays ~1.5 s of lazy voice load
  (measured, documented); later sentences synthesize at RTF ≈ 0.06–0.08.
- Two-person sessions (second participant) arrive in **Phase 5**; Phases 1–2
  are one mic → transcript.

## Roadmap (master prompt §17)

| Phase | Scope | Status |
| --- | --- | --- |
| 1 | Foundation + browser STT MVP | **complete** |
| 2 | VAD + audio optimization | **complete — pending approval** |
| 3 | Translation decision engine (bidirectional) | **implemented — in testing / pending approval (v0.3.1)** |
| 4 | TTS + browser playback | **implemented — in testing / pending approval (v0.4.0)** |
| 5 | Complete browser MVP (two participants) | not started |
| 6 | Mobile/browser compatibility on real devices | not started |
| 7 | Zoom integration (first meeting app) | not started |
| 8 | Chrome extension / advanced audio | not started |
| 9 | More meeting apps (one at a time) | not started |
| 10 | Production readiness & release | not started |
