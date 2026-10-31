import { describe, expect, it } from 'vitest'
import {
  DEFAULT_NATIVE_LANGUAGE,
  DEFAULT_PREFERRED_LANGUAGE,
  LANGUAGES,
  getLanguage,
  isRtlLanguage,
  languageLabel,
} from './languages'

describe('language catalogue', () => {
  it('contains the default Urdu/English example pair', () => {
    expect(getLanguage(DEFAULT_NATIVE_LANGUAGE)?.code).toBe('ur')
    expect(getLanguage(DEFAULT_PREFERRED_LANGUAGE)?.code).toBe('en')
  })

  it('has unique codes', () => {
    const codes = LANGUAGES.map((lang) => lang.code)
    expect(new Set(codes).size).toBe(codes.length)
  })

  it('uses lowercase ISO-639-1 style codes and non-empty names', () => {
    for (const lang of LANGUAGES) {
      expect(lang.code).toMatch(/^[a-z]{2,3}$/)
      expect(lang.label.length).toBeGreaterThan(0)
      expect(lang.native.length).toBeGreaterThan(0)
    }
  })

  it('marks the RTL languages', () => {
    expect(isRtlLanguage('ur')).toBe(true)
    expect(isRtlLanguage('ar')).toBe(true)
    expect(isRtlLanguage('fa')).toBe(true)
    expect(isRtlLanguage('ps')).toBe(true)
    expect(isRtlLanguage('en')).toBe(false)
    expect(isRtlLanguage('xx')).toBe(false)
  })

  it('falls back to the uppercase code for unknown languages', () => {
    expect(languageLabel('xx')).toBe('XX')
    expect(languageLabel('ur')).toContain('Urdu')
  })
})
