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

# Phase 2 VAD pipeline benchmark (gate cost, silence rejection, trim, accuracy)
cd backend && HF_HOME=../models/huggingface uv run python scripts/bench_vad.py

# Phase 2 browser smoke (headless Chromium with Chrome's fake microphone)
node scripts/smoke_phase2.mjs
```

## 2. Backend test suite — actual result (2026-10-31, after ADR-0009)

`uv run pytest` → **65 passed, 1 xfailed** in ~7 s (59 unit + 6 real-model).

| Area | Tests | Notes |
| --- | --- | --- |
| Health endpoint | 1 | payload contract (now incl. `vad` + `stt_queue` blocks) |
| Protocol models (pydantic) | 9 | version check, forbidden extras, bad audio format |
| Audio validation | 4 | size cap, 16-bit alignment |
| Session manager | 6 | limits, cleanup, buffer swap-clear |
| WS flow (FakeSTT via DI) | 17 | handshake, binary-before-hello fatal, flush→transcription, **native-language hint reaches STT**, **regional `ur-PK` reduced to `ur`**, **`no_speech_detected` on empty decode**, empty flush, ping/pong, malformed JSON, wrong version, oversized chunk, odd alignment, bad sample rate, duplicate hello, identical-language warning, 30 s auto-flush, session cleanup, mixed valid/invalid chunks |
| VAD planner (pure) | 9 | gate on no/short speech, trim ± pad clamping, split at ≥700 ms pauses, no split below threshold, span-cap forced split, `max_chunks` merge, honest source duration |
| VAD real Silero (not mocked) | 5 | fixture detected+trimmed, silence gated, low-level noise gated, two bursts segmented, disabled = exact pass-through |
| WS VAD + backpressure (fakes) | 7 | **gate rejects silence BEFORE STT (0 calls)**, trim delivers shorter audio + honest duration, split joins texts in order, per-session `server_busy`, global `server_busy` across sessions, slot released on STT failure, health exposes vad/queue |
| Real-model STT (not mocked) | 6 + 1 xfail | see §4; new: **full WS pipeline — real Silero gate + real Whisper over `/ws/audio` (ur → Arabic script)** |

The 1 xfail is **deliberate and documented**: Urdu language *label* detection
**with automatic detection** (see KI-002). It is a tracked known issue, not a
hidden failure; the production path pins the configured language and passes
deterministically.

Settings injectability note (found by the Phase 2 tests): the WS endpoint now
reads `app.state.settings` — the previously used `get_settings()` (lru_cache)
bypassed per-app overrides in tests.

## 3. Frontend — actual results (2026-10-31, Phase 2)

- `tsc --noEmit` (strict, `noUncheckedIndexedAccess`) → clean.
- `eslint . --max-warnings 0` → clean.
- `vitest run` → **33 passed** (protocol parser 10, languages 5, WS client 9 —
  fake WebSocket, fake timers, reconnect/backoff/heartbeat + **new:
  uplink-stall chunk dropping**, flags 7, no-speech policy 2).
- `vite build` → ✓ (`index.html` 1.2 kB, JS 244 kB / 77 kB gzip, CSS 6.5 kB).

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

### 5a. Phase 2 VAD pipeline benchmark (ADR-0009) — actual results

`scripts/bench_vad.py --runs 3` (same box; model `small` int8 + Silero ONNX
bundled with faster-whisper):

**Gate cost (plan latency, measured in-process):**

| Buffer | plan latency | result |
| --- | --- | --- |
| ur_greeting (6.58 s speech) | **10.3 ms** | 3 chunks, speech 3.62 s, 1.76 s dropped |
| en_hello (7.10 s speech) | **11.2 ms** | 3 chunks, speech 4.10 s, 1.81 s dropped |
| 8 s digital silence | **12.6 ms** | gated (0 chunks) |
| 8 s low-level noise (-56 dBFS) | **12.5 ms** | gated (0 chunks) |

**Silence/noise rejection** — with the gate, silence/noise never reaches the
executor (12.6 ms plan, then `no_speech_detected`). Without it, the decode
attempt still returns empty text quickly (0.2–15.5 ms — the ADR-0007
decode-time VAD already trims non-speech), so the marginal *latency* win per
junk flush is modest; the Phase 2 gate's wins are architectural: **zero
decode work dispatched**, protection independent of the internal VAD setting,
and one consistent gate for all clients. Measured honestly — the hallucination
protection itself shipped in KI-006/ADR-0007; ADR-0009 makes it structural
and cheaper.

**Trim + accuracy (best of 2 decodes each):**

| Fixture | raw | trimmed | text comparison |
| --- | --- | --- | --- |
| ur_greeting | 6.58 s → 2207 ms | 4.82 s (3 chunks) → 2304 ms | **identical text** — `السلام علیکم، آج اب کیسے ہے؟ میں بلکل ٹیک ہو شکریہ` |
| en_hello | 7.10 s → 1971 ms | 5.30 s (3 chunks) → 1923 ms | equivalent text (`Hello!` → `Hello,`, casing) |

Interpretation (honest): on these fixtures trim is **accuracy-neutral** and
latency-neutral (±5%) because the decode-time VAD already trimmed the same
silence; the server-side trim additionally protects the case where the
internal VAD is disabled (`STT_VAD_FILTER=false`) and bounds every decode
call to ≤ `VAD_SPLIT_MAX_SECONDS` (24 s default). Both fixtures contain
internal ≥700 ms pauses, so the segmentation safety net engaged (3 chunks) —
in the real app the client endpointer already sends sentence-sized buffers,
where the split does not engage. Peak RSS: bench process holding Whisper AND
Silero **1250 MiB**; running server after the E2E round **1334 MiB peak /
1233 MiB resident** (pre-Phase-2 server: 1062 MiB — Silero ONNX adds ~170 MiB,
within the 4.1 GB budget).

## 6. Live E2E — actual result (2026-10-31, after ADR-0009)

`scripts/e2e_synthetic_check.py` against `bash start.sh` (single-port :3000).
Each fixture streams on its own connection with `native_language` set to the
fixture's language — mirroring the real app:

```
session ready: s_9c3d02f5a734 native=en stt=faster-whisper/small
[PASS] en_hello.wav: lang=en (expected ['en']) p=1.00 proc_ms=4141 text='Hello. How are you doing today? This is a test of the real-t'
session ready: s_8f2545b23230 native=es stt=faster-whisper/small
[PASS] es_hola.wav:  lang=es (expected ['es']) p=1.00 proc_ms=3047 text='¡Hola! ¿Cómo estás? Espero que tengas un muy buen día.'
session ready: s_23abaa761443 native=ur stt=faster-whisper/small
[PASS] ur_greeting.wav: lang=ur (expected ['ur']) p=1.00 proc_ms=4315 text='السلام علیکم آج اب کیسے ہے؟ میں بلکل ٹیک ہو شکریہ'
all E2E checks passed
```

Protocol, buffering, flush, pinned-language inference, **the Phase 2 VAD
gate/trim/split pipeline** and event delivery all verified against the real
server (no mocks). Note: `proc_ms` is higher than the pre-Phase-2 round
(1784–1956 ms) because these fixture blobs contain internal ≥700 ms pauses —
the segmentation safety net splits them into 3 sequential decodes on the
1-worker executor. Real-app buffers are sentence-sized (client endpointing),
where the split does not engage. The quality gate: all three texts remain
correct.

Hallucination guard, live (`scripts/silence_guard_check.py`) — now served by
the **server-side Silero gate** (previously the STT dead-audio gate):

```
ready session=s_408aa97f2844 — streaming 2.0s of digital silence
PASS: server reported no_speech_detected (hallucination guard active)
```

(pre-fix, the same 2 s of silence produced a 447-character hallucinated
transcription.)

## 7. Browser smoke test — actual result (2026-10-31, Phase 2)

`node scripts/smoke_phase2.mjs` — headless Chromium with Chrome's **fake
microphone device** (`--use-fake-device-for-media-stream`), so the real
capture → WebSocket → server VAD → UI pipeline runs end to end. **10/10
checks passed**:

- App renders; WS connected; zero console/page errors while streaming.
- **Hands-free toggle visible (flag ON), default checked, `vt.hands_free`
  persists to localStorage.**
- Status wording switches: hands-free ON → "Hands-free listening…", OFF →
  "Listening…".
- 4 s of fake-audio streaming: no error banner, no crashes (the fake device
  emits a tone — the server VAD correctly classifies it as non-speech on the
  final flush; the banner that follows stop is the honest no-speech path for
  "sound that is not speech").
- Desktop (1280 px) and mobile (390×844) screenshots: `download/phase2-screens/`.

## 8. Real-device testing (P2-05) — what was verified vs what needs a human

The sandbox has **no real microphone**, so P2-05 was executed as far as
physically possible here and the rest is an explicit manual checklist:

**Verified in-sandbox (proxy):** full pipeline with Chrome's fake media
device (§7) — permission grant, AudioWorklet capture, WS streaming,
server-side VAD gating, hands-free toggle + persistence, both status modes,
zero console errors.

**Manual checklist (desktop Chrome, ~5 minutes, needs a real microphone):**

1. Open the app over `http://localhost:3000` (or HTTPS) in desktop Chrome.
2. Allow the microphone when prompted.
3. Push-to-talk: tap the mic, speak one sentence, tap stop **immediately** —
   expect the sentence transcribed correctly (KI-008 tail-capture).
4. Hands-free ON: tap the mic and keep talking — sentences should appear
   live at each pause; stay silent 25 s — expect NO error banners (silence
   drains are silent now).
5. Tap ✕ next to a transcript line — only that line disappears.
6. Watch the level meter while quiet — it should stay near zero; clap — it
   should spike.
7. Record results in this section (or report issues as new KIs).

Everything else stays on the Phase 6 matrix (KI-004): Android Chrome, iOS
Safari, background tab, lock screen, interruptions.

## 9. What is NOT tested (honesty list)

- **Real microphone input on real devices** (Android Chrome, iOS Safari,
  desktop Chrome) — manual checklist above (§8) + Phase 6 (KI-004). No claims
  made about device behavior.
- **Client-side sentence endpointing thresholds** (RMS 0.01, 800 ms pause,
  1.2 s min segment, 20 s cap) are verified by typecheck/unit tests/build
  only; the sandbox has no real microphone, so threshold tuning on actual
  devices is Phase 6 (KI-004). Server behavior after a client `flush` is
  fully covered by the WS-flow tests and live E2E.
- **Silero VAD thresholds on real room noise** (fan, keyboard, street noise)
  — the gate is verified on synthetic buffers and neural-voice fixtures;
  real-world false-reject/false-accept rates are unmeasured (Phase 6 input).
- Background tab / screen-lock / interruption behavior — Phase 6 (KI-004).
- iOS Safari `AudioContext(sampleRate: 16000)` support (fallback resampler
  exists in the worklet but is untested on real Safari).
- Multi-participant sessions, translation, TTS, meeting integrations — later
  phases by design.
- No coverage % is claimed (not measured).
