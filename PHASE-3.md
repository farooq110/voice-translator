# Phase 3 — Translation decision engine + bidirectional offline translation (branch-local document)

> This document lives on the `phase-3` branch and covers **Phase 3 only**:
> the translation decision engine and bidirectional offline translation,
> delivered as **text** (speech arrives in Phase 4 on the `phase-4`
> branch). The product-wide documentation lives in `README.md` on `main`.
> Version: v0.3.1 · Design decision: [ADR-0012](docs/decisions/0012-translation-decision-engine.md).

## 1. What Phase 3 delivers (functionality)

Until Phase 2 the app only transcribed. Phase 3 decides **what to do with
each utterance** and produces the translated text:

- **The three language rules, exactly** (nothing hardcoded — works for any
  configured pair):

  | Detected language | Result |
  | --- | --- |
  | native (e.g. `ur`) | `translate` → preferred (`en`) — Rule 1 |
  | preferred (e.g. `en`) | `translate` → native (`ur`) — Rule 2 |
  | any third language (e.g. `es`, confident `ar`) | `passthrough` — original is final, NO provider call |

  Degenerate configs degrade honestly: `native == preferred` → permanent
  pass-through (`identical_config`); an unsupported pair → honest
  `unsupported_pair` pass-through; text over 2 000 chars → `text_too_long`
  pass-through.
- **Bidirectional offline translation by default.** Local CTranslate2
  OPUS-MT models (ur↔en, es↔en) run on CPU — no API keys, transcript
  text never leaves the machine. `libre` (LibreTranslate HTTP) is
  supported as an explicit cloud setting; `fake` (deterministic stub)
  and `none` (decisions off, protocol keeps the Phase 1/2 shape)
  complete the settings matrix.
- **Automatic source-language detection** with the "detect-first,
  pin-the-decode" architecture (§2.2) — the Phase 3 key finding.
- **Translation UI.** Per-line direction badge (`UR → EN`) with the
  translated text below the original (RTL-aware for RTL targets), honest
  pass-through chips (`third language`, `language uncertain · original
  kept`, …), and per-line warning chips for translation errors. Behind
  the build-time flag `VITE_FEATURE_TRANSLATION` (default ON).
- **Non-fatal, utterance-scoped errors.** `translation_failed`,
  `translation_timeout`, `translation_rate_limited` — each carries the
  `utterance_id`, never touches the global banner, and the original
  transcript is always kept.

## 2. Implementation

### 2.1 Pure decision engine — `backend/app/translation/decision.py`

Implements the rules above for any configured pair and emits one of
8 self-explanatory reasons (`native_language`, `preferred_language`,
`third_language`, `identical_config`, `low_confidence`,
`undetected_language`, `unsupported_pair`, `text_too_long`). The
provider's `supports()` is consulted only for would-be translations.
Unicode/RTL text is never rewritten by the engine.

### 2.2 Detection — "detect-first, pin-the-decode"

The measured problem (KI-002/KI-006): Whisper `small`'s FULL auto-decode
of Urdu is unreliable AND slow — the ur fixture decoded with
`language=None` came back labeled `ar` (p=0.97) with mixed
Arabic/Devanagari text, 3–4× slower than a pinned decode (6–9 s vs ~2 s).
The production flow therefore NEVER auto-decodes:

1. a standalone `detect_language(audio)` pass (encoder-only, ≈1.4 s,
   capped at the first 8 s — the greeting prefix of the ur fixture
   answers `ar` p=0.93, the full utterance lands in the Hindustani
   cluster);
2. `resolve_stt_language()` normalizes the label: whisper == native →
   native; cluster sibling (`hi` when native is `ur` — KI-002) → native;
   any other label at/above the 0.60 threshold → that language;
   anything uncertain → native (KI-006 safe default, `fallback` source);
3. ONE decode forced to the resolved language (Phase 1/2 text quality
   and speed preserved);
4. the decision consumes the resolved detection; the transcription event
   reports the RESOLVED language and a `decision` field
   (`{action, reason, source_language, target_language?, detection}`).

Session-context language memory was considered and dropped — the safety
rule ("never generate an incorrect translation") dominates any guess.

### 2.3 Provider abstraction — `backend/app/translation/`

`base.py` defines the contract; shipped implementations:

- `local_ct2.py` (default) — offline CTranslate2 OPUS-MT models under
  `models/translation/<src>-<tgt>` (int8, ≈40 MB RSS per direction),
  provisioned by `scripts/download_translation_models.py`. Carries the
  measured **EOS framing fix**: the pre-converted CT2 models have
  `add_source_eos: False`, but Marian was trained WITH a trailing `</s>`
  on the source — without it the decoder never emits EOS and derails
  into repetition loops ("now now now …"); the provider appends the
  model's `eos_token` from `config.json`, so translations terminate
  cleanly.
- `libre.py` — LibreTranslate HTTP (URL + optional key, httpx,
  MockTransport-tested: 200/400/5xx/429/malformed/network/api-key).
- `fake.py` — deterministic `[fake:src→tgt] …` stub for tests/demos.
- `none` — disables decisions entirely.

### 2.4 Bounded execution — `TranslationService` (`translation/engine.py`)

Dedicated single-thread executor (MT never queues behind STT decodes),
sliding-window rate limiter (30/min default), global concurrency
semaphore (≤2), hard 8 s timeout, stale-result drop plus cancellation of
in-flight tasks on session close.

### 2.5 Protocol v1 stays v1 (additive, ADR-0011 pattern)

- `transcription` gains optional `decision` (§2.2; `action="translate"`
  is followed by a `translation` event with the same id;
  `action="passthrough"` means NO translation will ever arrive).
- NEW `translation` event after every translate decision:

  ```json
  {"v":1,"type":"translation","id":"c_0001_ab12cd","text":"Hello, how are you?",
   "source_language":"ur","target_language":"en","provider":"local-opus-mt",
   "duration_ms":144}
  ```

- Three new non-fatal error codes (§1) — utterance-scoped, original kept.
- Client obligations: treat `translation` as an update to the utterance
  with the same id; never re-flush or re-request on failure.

### 2.6 Frontend wiring

`services/translations.ts` (pure, unit-tested — transcript entry state
updates immutably by utterance id), `types/protocol.ts` (decision +
translation parsing), `TranscriptPanel.tsx` (badges, chips, RTL), hook
and flag wiring.

## 3. Usage

```bash
# one-time: offline MT models (~900 MB; fully offline afterwards)
cd backend && uv run python scripts/download_translation_models.py

# run everything on one port (SPA + WebSocket + health)
cd .. && bash start.sh          # → http://localhost:3000
```

- Open the app, pick Native + Preferred languages, enable **Hands-free**,
  and speak. Each sentence shows its decision — `UR → EN` with the
  translated text below (RTL-aware), or an honest pass-through chip for
  third languages.
- Development mode (HMR): `cd backend && uv run uvicorn app.main:app
  --port 8000` + `cd frontend && npm run dev` (Vite :5173 proxies
  API/WS).
- Settings (`.env`, 12-factor):

  | Variable | Default | Meaning |
  | --- | --- | --- |
  | `TRANSLATION_PROVIDER` | `local` | `local` · `fake` · `libre` · `none` |
  | `TRANSLATION_DETECT_MODE` | `auto` | `auto` = detect per utterance (ur/hi cluster + confidence gate) · `pinned` = assume the native language (Phase 1/2 latency, but Rule 2 can never fire) |
  | `TRANSLATION_CONFIDENCE_THRESHOLD` | `0.60` | detection confidence gate |
  | `TRANSLATION_TIMEOUT_S` | `8` | hard per-sentence timeout |
  | `TRANSLATION_RATE_LIMIT_PER_MINUTE` | `30` | sliding-window cap |
  | `TRANSLATION_MAX_CONCURRENT` | `2` | global concurrency semaphore |
  | `TRANSLATION_MAX_TEXT_CHARS` | `2000` | longer texts pass through |
  | `TRANSLATION_MODELS_DIR` | `models/translation` | local CT2 model location |
  | `LIBRETRANSLATE_URL` / `LIBRETRANSLATE_API_KEY` | empty | only for `libre` |

- Health check: `curl http://localhost:3000/health` → exposes the
  translation provider state (BUG-003 QA round: a missing model
  provisioning is visible in health, not silent).

## 4. Tests and measured results (v0.3.1)

| Check | Result |
| --- | --- |
| Backend suite | **128 passed + 1 xfail** (unit + real-model + real-MT; the xfail documents the RAW Whisper label behavior that the ur/hi normalization guards) |
| Frontend suite | **57 passed** vitest (protocol parser 19 — incl. the ADR-0012 decision shapes), typecheck + lint clean |
| Standalone detector (measured) | en p=0.997 · es p=0.989 · ur→`hi` p=0.775 (full utterance) · ur greeting 2.5 s prefix → `ar` p=0.93 (why the detection cap is 8 s, not 2.5 s) |
| MT termination fix (measured) | without source EOS: repetition loops; with the model's `</s>`: clean output — «موسم اچھا ہے» |
| `e2e_translation_check.py` (live :3000) | **3/3 PASS** — real speech → real WS → real offline CT2 OPUS-MT |
| Continuous speech + detection budget | **PASS** — 5/5 utterances, flush→text 3.72–3.90 s (gate ≤ 4.0 s) with the detect pass included |
| Browser smoke after the translation UI | **10/10**, zero console/page errors |
| Silence guard | **PASS** — hallucination guard active |

Known limitations (documented, not hidden): the bundled OPUS-MT small
models are everyday-sentence-grade, not production-tier — nuanced
Urdu→English is weak (KI-012); a third-language listener speaking Urdu to
an en-native config may get Hindi-orthography text (KI-002 residual
corner); `pinned` mode trades Rule 2 away for Phase 1/2 latency. The
provider abstraction exists precisely so a stronger engine is a settings
change, not a rewrite.

## 5. Design decision in one paragraph (ADR-0012)

Phase 3 was shaped by two hard constraints: the measured KI-002 signature
(auto-decode of Urdu returns `ar` p=0.97 with mixed scripts, 3–4× slower)
and the absence of any translation credentials in the environment (every
keyless web provider was probed and rejected: MyMemory quota, Google
`gtx` bot-wall, argos 404, ct2fast 401). The response: a pure decision
engine implementing the three rules exactly, detection separated from
decoding ("detect-first, pin-the-decode" — one decode, pinned to the
resolved language), an offline-first provider abstraction (local CT2
OPUS-MT default, cloud `libre` pluggable by settings), additive protocol
v1 changes, and bounded execution with cancellation of stale tasks.
