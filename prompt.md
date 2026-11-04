# prompt.md — Phase 3 Scope & Delivery Record

**Phase:** 3 of 10 — Translation Decision Engine and Bidirectional Translation
**Status:** implemented — independent QA round complete — **pending user approval** (§23 workflow)
**Versions:** v0.3.0 (commit `7b3d48e`) → v0.3.1 (commit `ebdf22d`, QA fix round)
**ADR:** [docs/decisions/0012-translation-decision-engine.md](docs/decisions/0012-translation-decision-engine.md)

> This repository previously carried the full product master prompt in this
> file. It now documents the **current phase only**. The complete master
> prompt (25 sections, 10-phase roadmap, approval workflow) is maintained by
> the product owner outside the repository. The overall delivery roadmap and
> phase statuses live in [README.md § Roadmap](README.md#roadmap-master-prompt-17);
> durable agent rules live in [AGENT.md](AGENT.md).

---

## 1. Phase 3 scope (master prompt §17)

Implement:

- Translation provider abstraction.
- Automatic source-language detection.
- Native Language configuration.
- Preferred Translation Language configuration.
- Translation decision engine.
- Urdu → English.
- English → Urdu.
- Additional supported language pairs.
- Unicode handling.
- Urdu RTL support.
- Translation timeouts.
- Error handling.
- Rate limiting.
- Cancellation of stale tasks.

Test all language decision scenarios using **real speech and provider
responses** (no mocked-only claims — AGENT.md § 6).

## 2. Language rules (normative, master prompt §17)

1. **Native Language speech from the translator user** → translate into the
   Preferred Translation Language for the other participant.
2. **Preferred Translation Language speech from the other participant** →
   translate into the Native Language for the translator user.
3. **Any other language** → original audio pass-through to both participants
   without translation.

No fixed Person A / Person B language assignments — the engine is
pair-agnostic and derives everything from the session's two configured
languages. The degenerate case (Native = Preferred) is resolved honestly as
pass-through with an `identical_config` reason plus an
`identical_languages` protocol warning, never a no-op translation.

## 3. Non-negotiables applied to this phase

- **Offline by default**: local CTranslate2 OPUS-MT, no API keys, no GPU;
  translation text never leaves the machine. Cloud (LibreTranslate) is an
  explicit, optional settings change — never the default.
- **Honest degradation**: when a pair is unavailable, the decision is a
  visible pass-through chip, never a silent wrong translation.
- **Replaceable providers**: `TranslationProvider` interface with three
  implementations (`local` CT2 OPUS-MT, `fake` for tests, `libre` optional);
  the WebSocket layer is provider-agnostic.
- **Bounded resources**: sliding-window rate limit (30/min, configurable),
  ≤ 2 concurrent translations, 8 s hard timeout, stale-task cancellation on
  socket close — no unbounded memory or runaway work.
- **Testing honesty**: every claim below maps to an executed test or probe
  (see § 6).

## 4. Delivered — v0.3.0 (commit `7b3d48e`)

- **Decision engine** as a separate, unit-testable module
  (`backend/app/translation/decision.py`). Translate reasons:
  `native_language`, `preferred_language`; pass-through reasons:
  `third_language`, `identical_config`, `low_confidence`,
  `undetected_language`, `unsupported_pair`, `text_too_long`.
- **Detect-first / pin-the-decode** (ADR-0012): a standalone encoder-only
  detection pass resolves the source language, then the Whisper decode is
  pinned to it. Full auto-decode was measured and rejected for Urdu
  (Arabic mis-detection p≈0.97, mixed scripts, 3–4× slower); the ur/hi
  cluster + confidence-threshold resolve also closes KI-002 for short
  clips.
- **Offline MT pairs** ur↔en and es↔en (CTranslate2 OPUS-MT), including a
  measured EOS-framing fix that removed duplicated/garbled tail output.
- **Protocol v1 additive changes** (no breaking change): `decision` field on
  transcription events, dedicated `translation` event, `identical_languages`
  warning, and 3 utterance-scoped non-fatal error codes
  (`translation_failed`, `translation_timeout`, `translation_rate_limited`)
  — the original transcript is always kept.
- **Bounded execution**: 30 translations/min sliding window, ≤ 2 concurrent,
  8 s `asyncio.wait_for` timeout backed by a single-thread executor that
  bounds provider overrun, cancellation of stale tasks.
- **Translation UI**: direction badges (`UR → EN`), pass-through chips with
  reason, RTL-aware transcript rendering (`dir="rtl"`, `lang` attribute),
  Native/Preferred selectors persisted in localStorage.
- **Unicode handling**: provider output is passed through untouched — never
  re-cased, truncated, or re-serialized.

## 5. Delivered — v0.3.1 QA round (commit `ebdf22d`)

Independent QA verification (2026-11-03, against commit `f23cd12`) returned
**FAIL for the deployed instance** with **BUG-003 (HIGH)**: the code was
verified correct, but a fresh git-based deployment never received the
translation models — `models/translation/` is a git-ignored runtime
artifact and nothing recreated it — so every Rule-1/Rule-2 utterance
degraded to pass-through (`unsupported_pair`) while `/health` still looked
healthy. Fixed in v0.3.1:

1. `start.sh` provisions MT models idempotently before boot when
   `TRANSLATION_PROVIDER=local` and `models/translation/` is empty
   (~600 MB first run; seconds when present), and **fails loudly** if
   provisioning fails instead of degrading silently.
2. `/health` now exposes the provider's pair coverage
   (`translation.pairs`, e.g. `ur-en / en-ur / es-en / en-es`) — a zero-pair
   deployment is visible instead of falsely healthy.
3. Startup warning when the local provider discovers zero pairs.
4. `docs/deployment.md` § 4 documents the provisioning step and disk
   footprint (was stale at Phase 2).

## 6. Verification (all executed, not claimed)

- Backend: **130 passed + 1 xfailed** (`pytest`, includes the
  `pairs`-exposure regression tests added in v0.3.1).
- Frontend: **57/57 vitest**, `tsc --noEmit` clean, ESLint 0 problems;
  production build 248.3 kB JS.
- E2E translation check **3/3** with real speech + real offline MT:
  ur→en (Rule 1), en→ur (Rule 2), es pass-through (Rule 3).
- Continuous hands-free speech **5/5** end-to-end, each utterance ≤ 4.0 s
  including the ~1.5 s detect pass (documented ADR-0012 trade-off).
- Browser smoke **10/10** with zero console errors; real Urdu speech shows
  the `UR → EN` badge, RTL transcript, and the English translation.
- Rate-limit, timeout, and cancellation probes verified utterance-scoped,
  non-fatal behavior with the original transcript always preserved.
- Independent QA regression points re-verified on v0.3.1: `/health` lists
  all four pairs; provisioning path exercised; Phase 2 suites stay green.

## 7. Known limitations (honest list)

- Local OPUS-MT is honest-tier, not production-tier: everyday sentences
  translate well; nuanced Urdu→English is weak (small Tatoeba-scale models,
  KI-012). Upgrading is a settings change via the provider abstraction.
- Short-utterance MT quality: a two-sentence Spanish input can drop the
  first sentence; short Urdu segments may render loosely (QA advisory,
  model-level behavior).
- Clean short Urdu greetings in Arabic script can be labelled `ar` →
  third-language pass-through; spec-compliant, a script check for
  Arabic-script output is a possible future refinement (KI-002 family).
- Urdu/Hindi share one spoken register; detection resolves them via the
  ur/hi cluster (KI-002).
- Real-device testing (Android Chrome / iOS Safari) is **Phase 6** scope.

## 8. Phase status & approval (master prompt §23)

Workflow applied: `COMPLETE → TEST → FIX → VERIFY ACCEPTANCE CRITERIA →
DOCUMENT → REPORT → REQUEST APPROVAL`.

| Acceptance criterion (§17) | Status |
| --- | --- |
| Translation provider abstraction | ✅ verified (code + tests, 3 providers) |
| Automatic source-language detection | ✅ verified (standalone pass, ADR-0012) |
| Native / Preferred configuration | ✅ verified (protocol, UI, persistence) |
| Translation decision engine (Rules 1–3) | ✅ verified (unit + E2E, real speech) |
| Urdu → English / English → Urdu | ✅ verified (real MT, both directions) |
| Additional pairs (es ↔ en) | ✅ provisioned and spot-checked |
| Unicode handling | ✅ output passed through untouched |
| Urdu RTL support | ✅ verified in browser (`dir`, `lang`, badge) |
| Translation timeouts / error handling / rate limiting / cancellation | ✅ verified (probes + tests) |
| No fixed Person A / Person B | ✅ pair-agnostic engine, asserted in tests |

**Current status:** implemented and independently QA-verified; the QA
round's blocking finding (BUG-003) is fixed and re-verified in v0.3.1.
**Phase 4 (TTS and browser audio playback) has NOT been started** and will
not start without the user's explicit approval.
