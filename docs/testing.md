# Testing — how to run + actual measured results

Environment of the recorded runs: sandbox, 2 vCPU, 4.1 GB RAM, **no GPU**,
Python 3.12.16, faster-whisper 1.2.1 / CTranslate2 4.8.4, model `small` int8
(ADR-0007; earlier rounds ran `base` — noted inline where it matters).
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

# Hallucination guard: digital silence must answer no_speech_detected
cd backend && uv run python scripts/silence_guard_check.py --url ws://localhost:3000/ws/audio

# STT benchmark (latency + peak RSS; auto-detect vs pinned language)
cd backend && HF_HOME=../models/huggingface uv run python scripts/bench_stt.py
```

## 2. Backend test suite — actual result (2026-10-31, after ADR-0007)

`uv run pytest` → **42 passed, 1 xfailed** in ~6 s (37 unit + 5 real-model).

| Area | Tests | Notes |
| --- | --- | --- |
| Health endpoint | 1 | payload contract |
| Protocol models (pydantic) | 9 | version check, forbidden extras, bad audio format |
| Audio validation | 4 | size cap, 16-bit alignment |
| Session manager | 6 | limits, cleanup, buffer swap-clear |
| WS flow (FakeSTT via DI) | 17 | handshake, binary-before-hello fatal, flush→transcription, **native-language hint reaches STT**, **regional `ur-PK` reduced to `ur`**, **`no_speech_detected` on empty decode**, empty flush, ping/pong, malformed JSON, wrong version, oversized chunk, odd alignment, bad sample rate, duplicate hello, identical-language warning, 30 s auto-flush, session cleanup, mixed valid/invalid chunks |
| Real-model STT (not mocked) | 5 + 1 xfail | see §4 |

The 1 xfail is **deliberate and documented**: Urdu language *label* detection
**with automatic detection** (see KI-002). It is a tracked known issue, not a
hidden failure; the production path pins the configured language and passes
deterministically.

## 3. Frontend — actual results (2026-10-31)

- `tsc --noEmit` (strict, `noUncheckedIndexedAccess`) → clean.
- `eslint . --max-warnings 0` → clean.
- `vitest run` → **23 passed** (protocol parser 10, languages 5, WS client state machine 8 — fake WebSocket, fake timers, reconnect/backoff/heartbeat paths).
- `vite build` → ✓ (`index.html` 1.2 kB, JS 241 kB / 76 kB gzip, CSS 5.5 kB).

## 4. Real STT validation — actual results (2026-10-31, after ADR-0007: model `small`, beam5, VAD)

Fixtures are **real speech audio** generated with Microsoft edge-tts **neural**
voices (`en-US-Aria`, `ur-PK-Asad`, `es-ES-Alvaro`), 16 kHz mono PCM16 — i.e.
synthetic voices, real speech; no real human/microphone audio yet (Phase 2/6).
Initial experiments with the sandbox's default TTS voices produced
unintelligible Urdu/Spanish — those fixtures were replaced.

Production path — language **pinned** from the configured native language:

| Fixture | Voice | Language | Prob | Transcription (excerpt) |
| --- | --- | --- | --- | --- |
| `en_hello.wav` (7.1 s) | en-US-Aria | `en` (pinned) | 1.00 | "Hello, how are you doing today? This is a test of the Real Time Voice Translator." |
| `es_hola.wav` (4.9 s) | es-ES-Alvaro | `es` (pinned) | 1.00 | "Hola, ¿cómo estás? Espero que tengas un muy buen día." |
| `ur_greeting.wav` (6.6 s) | ur-PK-Asad | `ur` (pinned) | 1.00 | "السلام علیکم، آج اب کیسے ہے؟ میں بلکل ٹیک ہو شکریہ" (Arabic script, ratio ≥ 0.8 asserted) |

**Accuracy comparison on `ur_greeting.wav` (KI-007, the reason for ADR-0007):**

| Config | Transcription |
| --- | --- |
| `base` + greedy (pre-fix) | `اسلام علیکم آج آپ کیسے ہیں میں بلکل تک ہوا شکریا` — broken greeting, drifted words |
| `small` + beam5 + VAD (post-fix) | `السلام علیکم، آج اب کیسے ہے؟ میں بلکل ٹیک ہو شکریہ` — correct greeting, punctuation, coherent |

**KI-002 (still true for AUTO-detection only):** without a hint, Whisper
*transcribes* Urdu almost perfectly (Arabic script) but the language **label**
comes back `hi` on short utterances (measured p=0.70) — Whisper's known
Hindi/Urdu (Hindustani) confusion. Measured with `small` too: label still
`hi` (p=0.78) and Urdu was even transcribed in Devanagari — so `small` is not
the fix. Since the KI-006 fix, the production path never uses auto-detection
for the translator user's own mic (the selection is pinned, ADR-0006); the
xfail test remains to document the behavior Phase 3 must not trust blindly
for the *other* participant.

**Hallucination guards (KI-006, measured):** 2 s of pure digital silence
previously decoded into **447 characters of pseudo-Urdu in 1594 ms**; after
the dead-audio gate it is rejected before the model runs and surfaces as
`no_speech_detected` (unit test + live check, see §6).

## 5. Latency & resource benchmark — actual results (2026-10-31, after ADR-0007: `small` + beam5 + VAD)

`scripts/bench_stt.py --runs 3`, model `small` int8, CPU. Each fixture is
timed twice: `auto` (not used in production) vs `forced` (language pinned
from config — the production path):

| Fixture (audio) | auto avg | forced avg | speedup | RTF (forced) |
| --- | --- | --- | --- | --- |
| en_hello (7.10 s) | 3328 ms | 1887 ms | **43%** | 0.27 |
| es_hola (4.94 s) | 3172 ms | 1783 ms | **44%** | 0.36 |
| ur_greeting (6.58 s) | 4143 ms | 2046 ms | **51%** | 0.31 |

Previous rounds for comparison — `base` + greedy: ur forced 745 ms
(RTF 0.11); live `processing_ms` 537–600 ms. The ADR-0007 trade is ~3× decode
cost for usable Urdu accuracy; **with client-side endpointing the decode now
runs while the user keeps speaking, so perceived latency ≈ decode time only**
— and typical sentences (2–3 s) decode in ≈ 0.6–1.0 s.

- Model load: ~1.5 s warm (after download); first-ever `small` download is
  ~490 MB.
- Peak RSS (bench process incl. model): **1062 MiB** (`small` int8; `base`
  was 382–609 MiB) — within the 4.1 GB sandbox budget.

## 6. Live E2E — actual result (2026-10-31, after ADR-0007)

`scripts/e2e_synthetic_check.py` against `bash start.sh` (single-port :3000).
Each fixture streams on its own connection with `native_language` set to the
fixture's language — mirroring the real app:

```
session ready: s_7895962e8d08 native=en stt=faster-whisper/small
[PASS] en_hello.wav: lang=en (expected ['en']) p=1.00 proc_ms=1784
session ready: s_652dc3d04d76 native=es stt=faster-whisper/small
[PASS] es_hola.wav:  lang=es (expected ['es']) p=1.00 proc_ms=1721
session ready: s_7fcf3bdc741a native=ur stt=faster-whisper/small
[PASS] ur_greeting.wav: lang=ur (expected ['ur']) p=1.00 proc_ms=1956
all E2E checks passed
```

Protocol, buffering, flush, pinned-language inference and event delivery all
verified against the real server (no mocks).

Hallucination guard, live (`scripts/silence_guard_check.py`):

```
ready session=s_4a1af333e59b — streaming 2.0s of digital silence
PASS: server reported no_speech_detected (hallucination guard active)
```

(pre-fix, the same 2 s of silence produced a 447-character hallucinated
transcription.)

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
- **Client-side sentence endpointing thresholds** (RMS 0.01, 800 ms pause,
  1.2 s min segment, 20 s cap) are verified by typecheck/unit tests/build
  only; the sandbox has no real microphone, so threshold tuning on actual
  devices is Phase 6 (KI-004). Server behavior after a client `flush` is
  fully covered by the WS-flow tests and live E2E.
- Background tab / screen-lock / interruption behavior — Phase 6 (KI-004).
- iOS Safari `AudioContext(sampleRate: 16000)` support (fallback resampler
  exists in the worklet but is untested on real Safari).
- Multi-participant sessions, translation, TTS, meeting integrations — later
  phases by design.
- No coverage % is claimed (not measured).
