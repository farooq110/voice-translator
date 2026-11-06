import { describe, expect, it } from 'vitest'
import {
  applyTranslationError,
  applyTranslationEvent,
  applyVoiceError,
  decisionBadgeText,
  isTranslationErrorCode,
  isVoiceErrorCode,
  translationErrorText,
  voiceErrorText,
  type TranslationCarrier,
} from './translations'
import type { TranslationDecision, TranslationEvent } from '../types/protocol'

interface Entry extends TranslationCarrier {
  id: string
  text: string
}

const ENTRY_A: Entry = { id: 'c_0001', text: 'original one' }
const ENTRY_B: Entry = { id: 'c_0002', text: 'original two' }

const TRANSLATION_EVENT: TranslationEvent = {
  v: 1,
  type: 'translation',
  id: 'c_0001',
  text: 'translated one',
  source_language: 'ur',
  target_language: 'en',
  provider: 'local-opus-mt',
  duration_ms: 321,
}

const DECISION: TranslationDecision = {
  action: 'translate',
  reason: 'native_language',
  source_language: 'ur',
  target_language: 'en',
  detection: 'whisper',
}

describe('applyTranslationEvent (ADR-0012)', () => {
  it('attaches the translation to exactly its utterance', () => {
    const [a, b] = applyTranslationEvent([ENTRY_A, ENTRY_B], TRANSLATION_EVENT)
    expect(a?.translation).toEqual({
      text: 'translated one',
      sourceLanguage: 'ur',
      targetLanguage: 'en',
      provider: 'local-opus-mt',
      durationMs: 321,
    })
    expect(b?.translation).toBeUndefined()
    expect(b).toBe(ENTRY_B) // untouched entries keep their identity
  })

  it('is immutable — the original entry object is not mutated', () => {
    const snapshot = { ...ENTRY_A }
    applyTranslationEvent([ENTRY_A], TRANSLATION_EVENT)
    expect(ENTRY_A).toEqual(snapshot)
  })

  it('unknown ids (deleted lines) are a no-op', () => {
    const result = applyTranslationEvent([ENTRY_A], { ...TRANSLATION_EVENT, id: 'u_9999' })
    expect(result[0]?.translation).toBeUndefined()
  })

  it('replaces a previous translationError when the translation arrives', () => {
    const errored = applyTranslationError([ENTRY_A], 'c_0001', 'translation_timeout')
    expect(errored[0]?.translationError?.code).toBe('translation_timeout')
    const recovered = applyTranslationEvent(errored, TRANSLATION_EVENT)
    expect(recovered[0]?.translationError).toBeUndefined()
    expect(recovered[0]?.translation?.text).toBe('translated one')
  })
})

describe('applyTranslationError (ADR-0012)', () => {
  it('attaches the error to its utterance only', () => {
    const [a, b] = applyTranslationError([ENTRY_A, ENTRY_B], 'c_0002', 'translation_failed')
    expect(a?.translationError).toBeUndefined()
    expect(b?.translationError?.code).toBe('translation_failed')
  })

  it('never touches the original text', () => {
    const [a] = applyTranslationError([ENTRY_A], 'c_0001', 'translation_rate_limited')
    expect(a?.text).toBe('original one')
  })
})

describe('isTranslationErrorCode', () => {
  it('recognizes the three utterance-scoped codes', () => {
    expect(isTranslationErrorCode('translation_failed')).toBe(true)
    expect(isTranslationErrorCode('translation_timeout')).toBe(true)
    expect(isTranslationErrorCode('translation_rate_limited')).toBe(true)
    expect(isTranslationErrorCode('no_speech_detected')).toBe(false)
    expect(isTranslationErrorCode('server_busy')).toBe(false)
  })
})

describe('decisionBadgeText', () => {
  it('shows the direction for a translate decision', () => {
    expect(decisionBadgeText(DECISION)).toBe('UR → EN')
    expect(
      decisionBadgeText({
        action: 'translate',
        reason: 'preferred_language',
        source_language: 'en',
        target_language: 'ur',
        detection: 'whisper',
      }),
    ).toBe('EN → UR')
  })

  it('explains every pass-through reason honestly', () => {
    const base = { action: 'passthrough' as const, detection: 'whisper' as const }
    expect(
      decisionBadgeText({ ...base, reason: 'third_language', source_language: 'es' }),
    ).toBe('pass-through · third language')
    expect(
      decisionBadgeText({ ...base, reason: 'low_confidence', source_language: 'ur' }),
    ).toBe('language uncertain · original kept')
    expect(
      decisionBadgeText({ ...base, reason: 'undetected_language', source_language: '' }),
    ).toBe('language not detected · original kept')
    expect(
      decisionBadgeText({ ...base, reason: 'unsupported_pair', source_language: 'fr' }),
    ).toBe('pass-through · pair unavailable')
    expect(
      decisionBadgeText({ ...base, reason: 'identical_config', source_language: 'en' }),
    ).toBe('pass-through · same languages')
    expect(
      decisionBadgeText({ ...base, reason: 'text_too_long', source_language: 'ur' }),
    ).toBe('pass-through · too long')
  })
})

describe('translationErrorText', () => {
  it('maps the codes to honest chip text', () => {
    expect(translationErrorText('translation_failed')).toBe('translation failed · original kept')
    expect(translationErrorText('translation_timeout')).toBe('translation timed out · original kept')
    expect(translationErrorText('translation_rate_limited')).toBe(
      'translation skipped · rate limit',
    )
    expect(translationErrorText('unknown_code')).toBe('unknown_code')
  })
})

describe('voice errors (Phase 4, ADR-0013)', () => {
  it('recognizes the utterance-scoped TTS codes', () => {
    expect(isVoiceErrorCode('tts_failed')).toBe(true)
    expect(isVoiceErrorCode('tts_timeout')).toBe(true)
    expect(isVoiceErrorCode('tts_rate_limited')).toBe(true)
    expect(isVoiceErrorCode('tts_unsupported_language')).toBe(true)
    expect(isVoiceErrorCode('tts_text_too_long')).toBe(true)
    expect(isVoiceErrorCode('translation_failed')).toBe(false)
    expect(isVoiceErrorCode('stt_error')).toBe(false)
  })

  it('attaches a voice error to exactly its utterance', () => {
    const result = applyVoiceError([ENTRY_A, ENTRY_B], ENTRY_B.id, 'tts_unsupported_language')
    const [first, second] = result
    expect(first?.voiceError).toBeUndefined()
    expect(second?.voiceError).toEqual({ code: 'tts_unsupported_language' })
    // the translated text is untouched
    expect(second?.translation).toBeUndefined()
  })

  it('clears a previous voice error when a translation arrives', () => {
    const errored = applyVoiceError([ENTRY_A], ENTRY_A.id, 'tts_failed')
    const recovered = applyTranslationEvent(errored, {
      ...TRANSLATION_EVENT,
      id: ENTRY_A.id,
    })
    const line = recovered[0]
    expect(line?.voiceError).toBeUndefined()
    expect(line?.translation).toBeDefined()
  })

  it('maps voice error codes to human text', () => {
    expect(voiceErrorText('tts_failed')).toBe('voice failed · text kept')
    expect(voiceErrorText('tts_unsupported_language')).toBe('no voice for this language · text kept')
    expect(voiceErrorText('tts_text_too_long')).toBe('too long to speak · text kept')
    expect(voiceErrorText('mystery')).toBe('mystery')
  })
})

describe('Phase 5 speaker-aware matching (ADR-0014)', () => {
  interface Line extends TranslationCarrier {
    text: string
  }
  const ownLine: Line = { id: 'c_0001', speaker: undefined, text: 'own' }
  const peerLine: Line = { id: 'c_0001', speaker: 'host', text: 'peer' }
  const lines: Line[] = [ownLine, peerLine]

  it('matches a translation to (id, speaker) — colliding ids stay separate', () => {
    const event = {
      v: 1, type: 'translation' as const, id: 'c_0001', speaker: 'host' as const,
      text: '[fake:ur→en] x', source_language: 'ur', target_language: 'en',
      provider: 'fake', duration_ms: 12,
    }
    const updated = applyTranslationEvent(lines, event)
    expect(updated[0]?.translation).toBeUndefined()
    expect(updated[1]?.translation?.text).toBe('[fake:ur→en] x')
  })

  it('matches solo events (no speaker on both sides) — the Phase 3/4 behavior', () => {
    const soloLines: Line[] = [{ id: 'c_0001', speaker: undefined, text: 'own' }]
    const event = {
      v: 1, type: 'translation' as const, id: 'c_0001',
      text: '[fake:ur→en] x', source_language: 'ur', target_language: 'en',
      provider: 'fake', duration_ms: 12,
    }
    const updated = applyTranslationEvent(soloLines, event)
    expect(updated[0]?.translation?.text).toBe('[fake:ur→en] x')
  })

  it('attaches voice errors to the right speaker line', () => {
    const updated = applyVoiceError(lines, 'c_0001', 'tts_unsupported_language', 'host')
    expect(updated[0]?.voiceError).toBeUndefined()
    expect(updated[1]?.voiceError?.code).toBe('tts_unsupported_language')
  })

  it('attaches translation errors to the right speaker line', () => {
    const updated = applyTranslationError(lines, 'c_0001', 'translation_timeout', 'host')
    expect(updated[0]?.translationError).toBeUndefined()
    expect(updated[1]?.translationError?.code).toBe('translation_timeout')
  })
})
