/**
 * Language catalogue offered to the translator user.
 *
 * All codes are ISO-639-1 and supported by Whisper's language detection.
 * The default pair (Urdu → English) mirrors the master prompt's example; the
 * engine itself never hardcodes any pair (Phase 3 decision engine).
 */

export interface LanguageOption {
  code: string
  label: string
  native: string
  rtl?: boolean
}

export const LANGUAGES: readonly LanguageOption[] = [
  { code: 'ur', label: 'Urdu', native: 'اردو', rtl: true },
  { code: 'en', label: 'English', native: 'English' },
  { code: 'ar', label: 'Arabic', native: 'العربية', rtl: true },
  { code: 'es', label: 'Spanish', native: 'Español' },
  { code: 'fr', label: 'French', native: 'Français' },
  { code: 'de', label: 'German', native: 'Deutsch' },
  { code: 'hi', label: 'Hindi', native: 'हिन्दी' },
  { code: 'bn', label: 'Bengali', native: 'বাংলা' },
  { code: 'pa', label: 'Punjabi', native: 'ਪੰਜਾਬੀ' },
  { code: 'fa', label: 'Persian', native: 'فارسی', rtl: true },
  { code: 'ps', label: 'Pashto', native: 'پښتو', rtl: true },
  { code: 'tr', label: 'Turkish', native: 'Türkçe' },
  { code: 'zh', label: 'Chinese', native: '中文' },
  { code: 'ja', label: 'Japanese', native: '日本語' },
  { code: 'ru', label: 'Russian', native: 'Русский' },
  { code: 'pt', label: 'Portuguese', native: 'Português' },
  { code: 'id', label: 'Indonesian', native: 'Bahasa Indonesia' },
  { code: 'it', label: 'Italian', native: 'Italiano' },
]

export const DEFAULT_NATIVE_LANGUAGE = 'ur'
export const DEFAULT_PREFERRED_LANGUAGE = 'en'

export function getLanguage(code: string): LanguageOption | undefined {
  return LANGUAGES.find((lang) => lang.code === code)
}

export function isRtlLanguage(code: string): boolean {
  return getLanguage(code)?.rtl === true
}

export function languageLabel(code: string): string {
  const lang = getLanguage(code)
  if (!lang) return code.toUpperCase()
  return `${lang.label} (${lang.native})`
}
