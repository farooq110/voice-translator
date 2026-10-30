# Testing — how to run + actual measured results

Environment of the recorded runs: sandbox, 2 vCPU, 4.1 GB RAM, **no GPU**,
Python 3.12.16, faster-whisper 1.2.1 / CTranslate2 4.8.4, model `base` int8.
Re-run everything with the commands below and update this file.

## 1. How to run

```bash
# Backend unit + protocol + WS-flow tests (no model needed)
cd backend && uv run pytest -m "not stt_real"

# Real-model STT tests (needs model + fixtures; skipped if absent)
cd backend && HF_HOME=../models/huggingface uv run pytest -m stt_real

# Everything
cd backend && uv run pytest

# Frontend: type-check, lint, unit tests, production build
cd frontend && npm run typecheck && npm run lint && npm run test && npm run build

# Live end-to-end (start the app first: bash start.sh → :3000)
cd backend && uv run python scripts/e2e_synthetic_check.py --url ws://localhost:3000/ws/audio

# STT benchmark (latency + peak RSS)
cd backend && HF_HOME=../models/huggingface uv run python scripts/bench_stt.py
```

## 2. Backend test suite — actual result (2026-10-31)

`uv run pytest` → **38 passed, 1 xfailed** in ~5 s.

| Area | Tests | Notes |
| --- | --- | --- |
| Health endpoint | 1 | payload contract |
| Protocol models (pydantic) | 9 | version check, forbidden extras, bad audio format |
| Audio validation | 4 | size cap, 16-bit alignment |
| Session manager | 6 | limits, cleanup, buffer swap-clear |
| WS flow (FakeSTT via DI) | 15 | handshake, binary-before-hello fatal, flush→transcription, empty flush, ping/pong, malformed JSON, wrong version, oversized chunk, odd alignment, bad sample rate, duplicate hello, identical-language warning, 30 s auto-flush, session cleanup, mixed valid/invalid chunks |
| Real-model STT (not mocked) | 3 + 1 xfail | see §4 |

The 1 xfail is **deliberate and documented**: Urdu language *label* detection
(see KI-002). It is a tracked known issue, not a hidden failure.

## 3. Frontend — actual results (2026-10-31)

- `tsc --noEmit` (strict, `noUncheckedIndexedAccess`) → clean.
- `eslint . --max-warnings 0` → clean.
- `vitest run` → **23 passed** (protocol parser 10, languages 5, WS client state machine 8 — fake WebSocket, fake timers, reconnect/backoff/heartbeat paths).
- `vite build` → ✓ (`index.html` 1.2 kB, JS 241 kB / 76 kB gzip, CSS 5.5 kB).

## 4. Real STT validation — actual results (2026-10-31)

Fixtures are **real speech audio** generated with Microsoft edge-tts **neural**
voices (`en-US-Aria`, `ur-PK-Asad`, `es-ES-Alvaro`), 16 kHz mono PCM16 — i.e.
synthetic voices, real speech; no real human/microphone audio yet (Phase 2/6).
Initial experiments with the sandbox's default TTS voices produced
unintelligible Urdu/Spanish — those fixtures were replaced.

| Fixture | Voice | Detected | Prob | Transcription (excerpt) |
| --- | --- | --- | --- | --- |
| `en_hello.wav` (7.1 s) | en-US-Aria | `en` | 0.99 | "Hello. How are you doing today? This is a test of the real-time voice translator." |
| `es_hola.wav` (4.9 s) | es-ES-Alvaro | `es` | 0.98 | "Hola, ¿cómo estás? Espero que tengas un muy buen día." |
| `ur_greeting.wav` (6.6 s) | ur-PK-Asad | `hi` ⚠ | 0.70 | "اسلام علیکم, آج آپ کیسے ہیں؟ میں بلکل تھیک ہوشکریا" |

**KI-002 (important):** Urdu is *transcribed almost perfectly* (Arabic script)
but the language **label** comes back `hi` on short utterances — Whisper's
known Hindi/Urdu (Hindustani) confusion. Measured with `small` too: label
still `hi` (p=0.78) and Urdu was even transcribed in Devanagari — so `small`
is **not** the fix. Consequence for Phase 3: the decision engine must not
trust `language` blindly for the ur/hi pair (script check / context tracking /
configurable language sets). Tracked in `project-state/KNOWN_ISSUES.md`.

## 5. Latency & resource benchmark — actual results (2026-10-31)

`scripts/bench_stt.py --runs 3`, model `base` int8, CPU:

| Fixture (audio) | stt_ms avg | stt_ms best | RTF |
| --- | --- | --- | --- |
| en_hello (7.10 s) | 926 | 916 | 0.13 |
| es_hola (4.94 s) | 903 | 902 | 0.18 |
| ur_greeting (6.58 s) | 997 | 993 | 0.15 |

- Model load: ~1.0 s warm (after download), ~16 s cold for `small`.
- Peak RSS (bench process incl. model): **609 MiB**.
- End-to-end (live WS, real-time-paced streaming, flush → event):
  `processing_ms` 940–999 ms per utterance — i.e. transcription latency ≈
  1 s after the user stops speaking, on a 2 vCPU box.

## 6. Live E2E — actual result (2026-10-31)

`scripts/e2e_synthetic_check.py` against `bash start.sh` (single-port :3000):

```
session ready: s_3c46c6e062e1 stt=faster-whisper/base
[PASS] en_hello.wav: lang=en  p=0.99 proc_ms=940
[PASS] es_hola.wav:  lang=es  p=0.98 proc_ms=946
[PASS] ur_greeting.wav: lang=hi (expected ['hi','ur']) p=0.70 proc_ms=999
all E2E checks passed
```

One session, three utterances streamed at real-time pace → protocol,
buffering, flush, inference and event delivery all verified against the real
server (no mocks).

## 7. Browser smoke test — actual result (2026-10-31)

Headless Chromium via agent-browser against `http://localhost:3000`:

- App renders; zero console errors / page errors.
- WebSocket handshake completes → badge "Connected", session id displayed.
- Defaults correct: Native=Urdu (`اردو`), Preferred=English.
- Mic button in a mic-less headless browser → graceful error banner
  ("No microphone was found on this device.") with dismiss; connection intact.
- Language selection persists via localStorage across reload (verified `ur`→`es`).
- Desktop (1280 px) and mobile (390×844) layouts verified visually —
  screenshots in `download/phase1-screens/`.

## 8. What is NOT tested (honesty list)

- **Real microphone input on real devices** (Android Chrome, iOS Safari,
  desktop Chrome) — Phase 2/6. No claims made about device behavior.
- Background tab / screen-lock / interruption behavior — Phase 6 (KI-004).
- iOS Safari `AudioContext(sampleRate: 16000)` support (fallback resampler
  exists in the worklet but is untested on real Safari).
- Multi-participant sessions, translation, TTS, meeting integrations — later
  phases by design.
- No coverage % is claimed (not measured).
