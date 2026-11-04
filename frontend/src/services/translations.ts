/**
 * Translation state helpers (Phase 3, ADR-0012).
 *
 * Pure functions over the transcript list — the session hook stays a thin
 * event router (same pattern as FlushTracker). Everything is immutable:
 * entries never mutate, unknown ids are no-ops, and a translation error is
 * attached to ITS utterance without touching the global error banner (the
 * original transcript is always kept).
 */

import type { TranslationDecision, TranslationEvent, TranslationReason } from '../types/protocol'

export interface TranslationInfo {
  text: string
  sourceLanguage: string
  targetLanguage: string
  provider: string
  durationMs: number
}

export interface TranslationErrorInfo {
  code: string
}

/** Phase 4: a TTS problem attached to the utterance (text kept, audio lost). */
export interface VoiceErrorInfo {
  code: string
}

/** Any transcript entry that can carry a translation outcome. */
export interface TranslationCarrier {
  id: string
  translation?: TranslationInfo
  translationError?: TranslationErrorInfo
  voiceError?: VoiceErrorInfo
}

/** Attach a `translation` event to exactly its utterance (by id). */
export function applyTranslationEvent<T extends TranslationCarrier>(
  entries: readonly T[],
  event: TranslationEvent,
): T[] {
  return entries.map((entry) =>
    entry.id === event.id
      ? {
          ...entry,
          translation: {
            text: event.text,
            sourceLanguage: event.source_language,
            targetLanguage: event.target_language,
            provider: event.provider,
            durationMs: event.duration_ms,
          },
          translationError: undefined,
          voiceError: undefined,
        }
      : entry,
  )
}

const TRANSLATION_ERROR_CODES: readonly string[] = [
  'translation_failed',
  'translation_timeout',
  'translation_rate_limited',
]

/** True for the utterance-scoped translation error codes (handled per line,
 * never as the global banner). */
export function isTranslationErrorCode(code: string): boolean {
  return TRANSLATION_ERROR_CODES.includes(code)
}

/** Attach a translation error to exactly its utterance (by id). */
export function applyTranslationError<T extends TranslationCarrier>(
  entries: readonly T[],
  utteranceId: string,
  code: string,
): T[] {
  return entries.map((entry) =>
    entry.id === utteranceId ? { ...entry, translationError: { code } } : entry,
  )
}

const VOICE_ERROR_CODES: readonly string[] = [
  'tts_failed',
  'tts_timeout',
  'tts_rate_limited',
  'tts_unsupported_language',
  'tts_text_too_long',
]

/** True for the utterance-scoped TTS error codes (handled per line, never
 * as the global banner — the translated text is always kept). */
export function isVoiceErrorCode(code: string): boolean {
  return VOICE_ERROR_CODES.includes(code)
}

/** Attach a voice error to exactly its utterance (by id). */
export function applyVoiceError<T extends TranslationCarrier>(
  entries: readonly T[],
  utteranceId: string,
  code: string,
): T[] {
  return entries.map((entry) =>
    entry.id === utteranceId ? { ...entry, voiceError: { code } } : entry,
  )
}

/** Human text for an utterance-scoped voice error chip (Phase 4). */
export function voiceErrorText(code: string): string {
  switch (code) {
    case 'tts_failed':
      return 'voice failed · text kept'
    case 'tts_timeout':
      return 'voice timed out · text kept'
    case 'tts_rate_limited':
      return 'voice skipped · rate limit'
    case 'tts_unsupported_language':
      return 'no voice for this language · text kept'
    case 'tts_text_too_long':
      return 'too long to speak · text kept'
    default:
      return code
  }
}

/** Chip text for a transcription's decision (badge / pass-through reason). */
export function decisionBadgeText(decision: TranslationDecision): string {
  const source = decision.source_language.toUpperCase()
  if (decision.action === 'translate' && decision.target_language) {
    return `${source} → ${decision.target_language.toUpperCase()}`
  }
  switch (decision.reason) {
    case 'third_language':
      return 'pass-through · third language'
    case 'low_confidence':
      return 'language uncertain · original kept'
    case 'undetected_language':
      return 'language not detected · original kept'
    case 'unsupported_pair':
      return 'pass-through · pair unavailable'
    case 'identical_config':
      return 'pass-through · same languages'
    case 'text_too_long':
      return 'pass-through · too long'
    case 'native_language':
    case 'preferred_language':
      // translate decision without a target — should not happen; keep honest.
      return `${source} → translating…`
  }
}

const REASON_ORDER: readonly TranslationReason[] = [
  'native_language',
  'preferred_language',
  'third_language',
  'identical_config',
  'low_confidence',
  'undetected_language',
  'unsupported_pair',
  'text_too_long',
]

/** Exhaustiveness guard for decisionBadgeText (compile-time only). */
export function assertReasonCovered(reason: TranslationReason): void {
  if (!REASON_ORDER.includes(reason)) {
    throw new Error(`unhandled translation reason: ${reason}`)
  }
}

/** Human text for an utterance-scoped translation error chip. */
export function translationErrorText(code: string): string {
  switch (code) {
    case 'translation_failed':
      return 'translation failed · original kept'
    case 'translation_timeout':
      return 'translation timed out · original kept'
    case 'translation_rate_limited':
      return 'translation skipped · rate limit'
    default:
      return code
  }
}
