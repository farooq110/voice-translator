# ADR-0012 — Translation decision engine + bidirectional offline translation

Date: 2026-11-01 · Status: Accepted · Inputs: user approval "After that start
the phase 3 and complete it" (master prompt §23), master prompt §2/§3/§4/§10/§15/§17
Phase 3, KI-002 (the documented Phase 3 design input)

## Context

Phase 3 scope (master prompt §17): translation provider abstraction, automatic
source-language detection, the exact three language rules (native → preferred
for the other participant; preferred → native for the translator user; every
third language passes through untouched), Unicode/Urdu-RTL text handling,
timeouts, rate limiting and cancellation of stale tasks. TTS and two-person
audio routing are explicitly LATER phases — Phase 3 delivers the decision
engine and the translated TEXT through the existing protocol.

Two hard constraints shaped the design:

1. **KI-002/KI-006 (measured again in this phase):** Whisper `small`'s FULL
   auto-decode of Urdu speech is unreliable AND slow — the ur fixture decoded
   with `language=None` came back labeled `ar` (p=0.97) with MIXED
   Arabic/Devanagari text, 3–4× slower than a pinned decode (6–9 s vs ~2 s).
   The standalone `detect_language` pass is a different story: en p=0.997,
   es p=0.989, ur→`hi` p=0.775 with `ur` second (p=0.20) — exactly the KI-002
   signature, which the ur/hi register cluster can resolve.
2. **No translation credentials exist in this environment**, and every
   keyless web provider was probed and rejected: MyMemory (shared-IP quota
   exhausted), Google's unofficial `gtx` endpoint (bot-wall), argos model
   index (404), ct2fast HF mirrors (401). Cloud providers stay ALLOWED by the
   master prompt — they plug in through the provider abstraction.

## Decision

1. **Pure decision engine** (`app/translation/decision.py`) implementing the
   master prompt rules EXACTLY for any configured pair (nothing hardcoded):
   native → translate to preferred (Rule 1); preferred → translate to native
   (Rule 2); anything else → pass-through with NO provider call and NO TTS
   (Rules 3+4). The provider's `supports()` is consulted only for would-be
   translations; an unsupported pair degrades to an honest
   `unsupported_pair` pass-through. `native == preferred` → permanent
   pass-through (`identical_config`). Texts over `translation_max_text_chars`
   (2000) pass through (`text_too_long`).
2. **Detection architecture — "detect-first, pin-the-decode"** (the phase's
   key finding). The production flow NEVER auto-decodes:
   - standalone `detect_language(audio)` (encoder-only, ~1.4 s, capped at the
     first 8 s);
   - `resolve_stt_language()` normalizes the label: whisper==native → native;
     cluster sibling (`hi` when native is `ur`, KI-002) → native; any other
     label at/above the 0.60 threshold → that language; anything uncertain →
     native (the KI-006 safe default, `fallback` source);
   - ONE decode FORCED to the resolved language (Phase 1/2 text quality and
     speed preserved);
   - the decision consumes the resolved detection.
   The 8 s detection cap exists because detection on the 2.5 s PREFIX of the
   ur greeting answered `ar` p=0.93 (the opening "Assalam-o-Alaikum" is
   genuinely Arabic-like) while the full utterance correctly lands in the
   Hindustani cluster. Real Arabic speech still passes through: a confident
   `ar` label is kept as a third language (master prompt Rule 3 names Arabic
   explicitly). Session-context language tracking was considered and dropped —
   the safety rule ("never generate an incorrect translation") dominates any
   guess a memory could produce.
3. **Provider abstraction** (`app/translation/base.py`) with three shipped
   implementations: `local` (default) — offline CTranslate2 OPUS-MT models
   under `models/translation/<src>-<tgt>` (ur↔en + es↔en, provisioned by
   `backend/scripts/download_translation_models.py`, int8 ≈40 MB RSS per
   direction, text never leaves the machine); `fake` — deterministic
   `[fake:src→tgt] …` stub for tests/demos; `libre` — LibreTranslate HTTP
   (URL + optional key, httpx, MockTransport-tested). `none` disables
   decisions entirely (protocol keeps the Phase 1/2 shape).
   **Measured conversion quirk:** the pre-converted CT2 models carry
   `add_source_eos: False`, but Marian was trained WITH a trailing `</s>` on
   the source — without it the decoder never emits EOS and derails into
   repetition loops ("now now now …"). The provider appends the model's
   `eos_token` from `config.json`; translations terminate cleanly.
4. **Protocol v1 stays v1 (additive, ADR-0011 pattern):** `transcription`
   gains optional `decision` `{action, reason, source_language,
   target_language?, detection}` (8 self-explanatory reasons); a NEW
   `translation` event `{id, text, source_language, target_language,
   provider, duration_ms}` follows every translate decision (same utterance
   id); three new NON-FATAL utterance-scoped error codes —
   `translation_failed`, `translation_timeout`, `translation_rate_limited` —
   always carry `utterance_id` and never touch the global banner: the
   original transcript is kept untouched.
5. **Bounded execution** (`TranslationService`): dedicated single-thread
   executor (MT never queues behind STT decodes), sliding-window rate limiter
   (30/min default), global concurrency semaphore (2), hard
   `asyncio.wait_for` timeout (8 s), stale-result drop on session close plus
   cancellation of in-flight translation tasks. Unicode/RTL text is never
   rewritten by the engine.
6. **Frontend:** `VITE_FEATURE_TRANSLATION` flag (default ON); per-line
   direction badge (`UR → EN`) or honest pass-through chip (`third language`,
   `language uncertain · original kept`, …); translated text below the
   original with RTL direction for RTL targets; per-line warning chips for
   translation errors. The transcript entry state updates immutably by
   utterance id (`services/translations.ts`, pure + unit-tested).

## Consequences

- Latency: auto mode adds the detect pass (≈1.4 s measured) per utterance —
  the continuous-speech budget still holds: 5/5 utterances at 3.72–3.90 s
  flush→text (gate ≤ 4.0 s, docs/testing.md §10). `translation_detect_mode=pinned`
  restores the Phase 1/2 latency exactly (Rule 2 then cannot fire).
- KI-002: the auto path is now guarded (cluster + threshold + fallback);
  the xfail documenting the RAW label behavior remains (the normalization is
  what makes it safe). Residual corner: a THIRD-language listener speaking
  Urdu to an en-native config gets Hindi-orthography text (documented in
  KNOWN_ISSUES).
- Local OPUS-MT quality is honest-tier, not production-tier: short everyday
  sentences translate correctly and terminate cleanly; nuanced Urdu→English
  is weak (small Tatoeba-scale models, KI-012). The abstraction exists
  precisely so a better provider is a settings change, not a rewrite.
- Version skew is a non-issue in single-port mode (client+server ship
  together); the frontend tolerates `decision: null` (translation disabled).
- External data flow: with the default `local` provider, transcript TEXT is
  translated on-CPU and nothing leaves the machine (audio already stayed
  local). `libre` sends text to the configured instance — documented in the
  protocol spec and README.
