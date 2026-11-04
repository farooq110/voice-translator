# Phase 4 — Offline TTS + browser playback (branch-local document)

> This document lives on the `phase-4` branch and covers **Phase 4 only**:
> spoken output for translations and its playback in the browser. The
> product-wide documentation lives in `README.md` on `main`; Phase 3's
> branch-local record is `PHASE-3.md` on the `phase-3` branch.
> Version: v0.4.0 · Design decision: [ADR-0013](docs/decisions/0013-tts-offline-piper.md).

## 1. What Phase 4 delivers (functionality)

Phase 3 ended at translated text on screen. Phase 4 makes the translation
**speak**:

- **Offline neural voices.** Every `translate` decision (Rules 1–2) is
  synthesized with Piper voices (ur · en · es) on CPU — no GPU, no API
  keys, no network; audio never leaves the machine.
- **Sequential browser playback.** Translated clips play through a
  client-side `SpeechPlayer` queue in which overlap is impossible by
  construction: every clip is scheduled at `max(now, previous end)`.
- **Pass-through is never re-spoken.** Third-language utterances bypass
  TTS entirely (the master prompt's non-negotiable), asserted in the WS
  tests and verified live: the es pass-through fixture produces **zero**
  audio frames.
- **Mute control.** A **Spoken translations** toggle in the UI persists
  to `localStorage`; muting also stops any clip currently playing.
- **Honest degradation.** A language without a provisioned voice surfaces
  a per-line warning chip instead of a global error; the translated text
  is never touched. Every TTS failure is a non-fatal, utterance-scoped
  protocol error (`tts_failed`, `tts_timeout`, `tts_rate_limited`,
  `tts_unsupported_language`, `tts_text_too_long`).
- **Observability.** `GET /health` now exposes a `tts` block (provider,
  languages, voices, bounds), so a voice-less deployment is visible.

## 2. Implementation

### 2.1 Engine and providers — `backend/app/tts/`

- **Piper (VITS on ONNX Runtime)** via `piper-tts`: it rides on
  `onnxruntime`, already a backend dependency through faster-whisper, so
  the dependency surface does not grow (no torch, no cloud SDK). Voices
  are MIT-licensed, one directory per language under `models/tts/<lang>/`,
  git-ignored like every other model artifact. Measured on 2 vCPU:
  RTF ≈ 0.06–0.08 (a ~2–3 s sentence synthesizes in ~0.15–0.2 s warm).
- **Provider abstraction** (`tts/base.py`) mirrors translation:
  `supports(language)` gates before any call (an honest "no voice"
  instead of an error); `synthesize(text, language) →
  SynthesizedAudio(pcm16, rate)`. Shipped implementations: `piper`
  (default; voice loading is lazy — the first synthesis into a language
  pays ~1.5 s, documented), `fake` (deterministic sine stub for
  tests/demos), `none` (TTS disabled: translated text is displayed,
  never spoken).
- **`TTSService` = bounded execution** (`tts/service.py`): sliding-window
  rate limit 30/min, ≤1 concurrent (CPU-bound) on a dedicated
  single-thread executor, 10 s hard timeout, 800-char text cap. Every
  failure maps to a non-fatal utterance-scoped error; the translated
  text is never touched.

### 2.2 Transport — additive protocol v1 (`docs/audio-protocol.md`)

One `audio` JSON metadata event:

```json
{"v":1,"type":"audio","id":"c_0001_ab12cd","language":"en",
 "encoding":"pcm_s16le","sample_rate":22050,"channels":1,
 "bytes":113664,"duration_ms":2577,"synthesis_ms":546,"provider":"piper"}
```

followed by **exactly one binary frame** of raw PCM16 little-endian mono
at 22 050 Hz. WebSocket ordering pairs them; legacy clients already
ignore binary frames. With `TTS_PROVIDER=none` the wire keeps the exact
Phase 3 shape (no `audio` events, no server→client binary frames).

### 2.3 Browser playback — `frontend/src/services/playback.ts`

`SpeechPlayer` over the Web Audio API: Int16→Float32 conversion,
strictly sequential scheduling, bounded queue (4 pending clips, oldest
dropped when full), re-basing of the remaining clips after a
cancellation (no silent gaps), `cancel(id)` when a transcript line is
deleted, `stopAll()` on connection loss / user stop / mute, and
AudioContext unlock on the first user gesture (autoplay policy). Wired
through `wsClient.ts` (audio⇄binary pairing + stray-frame counter),
`useTranslatorSession.ts`, `App.tsx` and the `PlaybackToggle` component.

### 2.4 Supporting changes

- `backend/scripts/download_tts_voices.py` — idempotent voice
  provisioning (~190 MB for ur/en/es), run once, offline afterwards.
- `backend/scripts/e2e_tts_check.py` — live end-to-end check: real
  speech → real MT → real synthesis → binary frames over the real
  protocol (pass-through must bypass TTS).
- `start.sh` provisions voices idempotently and fails loudly.
- `backend/.env.example` gains the `TTS_*` settings block (§3).
- New tests: `test_tts_providers.py`, `test_tts_service.py`,
  `test_ws_tts.py` (backend) · `playback.test.ts`, extended
  `wsClient.test.ts` / `translations.test.ts` / `protocol.test.ts`
  (frontend).

## 3. Usage

```bash
# one-time: voices (~190 MB; fully offline afterwards)
cd backend && uv run python scripts/download_tts_voices.py

# run everything on one port (SPA + WebSocket + health)
cd .. && bash start.sh          # → http://localhost:3000
```

- Open the app, pick Native + Preferred languages, enable **Hands-free**,
  and speak. Translated sentences are spoken aloud automatically in the
  target language; the original audio of third languages is **never**
  re-spoken.
- **Spoken translations** toggle mutes/unmutes playback (persisted across
  reloads).
- Development mode (HMR): `cd backend && uv run uvicorn app.main:app
  --port 8000` + `cd frontend && npm run dev` (Vite :5173 proxies
  API/WS).
- Settings (`.env`, 12-factor; every value can also be a real environment
  variable):

  | Variable | Default | Meaning |
  | --- | --- | --- |
  | `TTS_PROVIDER` | `piper` | `piper` · `fake` (sine stub) · `none` (text only) |
  | `TTS_VOICES_DIR` | `models/tts` | per-language voice directories |
  | `TTS_TIMEOUT_S` | `10` | hard synthesis timeout |
  | `TTS_RATE_LIMIT_PER_MINUTE` | `30` | sliding-window cap |
  | `TTS_MAX_CONCURRENT` | `1` | CPU-bound concurrency |
  | `TTS_MAX_TEXT_CHARS` | `800` | longer texts are not synthesized |
  | `TTS_WORKER_THREADS` | `1` | dedicated executor threads |

- Health check: `curl http://localhost:3000/health` → the `tts` block
  shows provider, languages, voices and bounds.

## 4. Tests and measured results (v0.4.0)

| Check | Result |
| --- | --- |
| Backend suite | **155 passed + 1 xfail** — +25 Phase 4 tests: service bounds (timeout/rate/concurrency/unsupported/too-long), provider discovery, WS flow (audio+binary pairing, pass-through bypass, failure/unsupported/rate-limit error mapping, disabled shape), health exposure |
| Frontend suite | **81/81 vitest** (+24 Phase 4 tests: SpeechPlayer scheduling/queue/re-basing/mute/autoplay, wsClient pairing, audio parsing, voice-error chips), typecheck clean, ESLint 0 |
| Real synthesis probe | ur: 1.79 s audio in 0.10 s (RTF ≈ 0.06) · en: 2.09 s in 0.175 s · es: 2.98 s in 0.18 s; 22 050 Hz, RMS ≈ 0.19 (audible) |
| `e2e_tts_check.py` (live :3000) | **3/3 PASS** — Rule 1 ur→en: MT 109 ms + spoken en 2 577 ms of audio, synthesis 1 573 ms (includes the first-use voice load), PCM RMS 0.191 · Rule 2 en→ur: MT 157 ms + spoken ur 3 424 ms of audio in 546 ms · Rule 3 es pass-through: **zero audio frames** |
| `tts_real` marked test | PASS — real ur/en/es synthesis through `PiperTTSProvider` (skips when voices absent) |
| Browser smoke (headless Chromium, fake mic) | **10/10**, zero console/page errors |
| Live regression (Phase 3 paths) | translation E2E **3/3** + silence guard **PASS** — unregressed |

Known trade-offs: the first synthesis per language pays the lazy voice
load (~1.5 s); Piper `medium` voices are comprehension-grade, not
human-equivalent; the Urdu voice (UAT dataset, eSpeak phonemization) can
render anglicisms with an Urdu accent in mixed-language sentences.
Upgrading is a settings change via the provider abstraction (ADR-0013).

## 5. Design decision in one paragraph (ADR-0013)

Piper on ONNX Runtime was chosen because the runtime already ships with
faster-whisper (no dependency bloat), voices are MIT-licensed and fully
offline, and the measured RTF ≈ 0.06–0.08 fits the latency budget on
2 vCPU. Rejected alternatives: Coqui/MMS-TTS (+2 GB torch dependencies),
cloud TTS (violates the offline/privacy default — the door stays open as
an explicit settings change, mirroring translation's `libre` option),
browser SpeechSynthesis (Urdu availability is a platform lottery and the
later meeting-integration phases need server-side audio), and streaming
TTS chunks (whole-clip delivery measured fast enough for sentence-length
text; the protocol allows evolution later).
