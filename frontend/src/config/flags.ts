/**
 * Feature flags (Phase 2).
 *
 * Build-time flags via Vite env vars — set e.g. VITE_FEATURE_HANDS_FREE=false
 * at build time to hide the feature without code changes. An unset variable
 * keeps the default (ON), so the shipped app exposes the toggle.
 */

function readFlag(value: string | undefined, fallback: boolean): boolean {
  if (value === undefined || value === '') return fallback
  return value !== 'false' && value !== '0'
}

export const HANDS_FREE_ENABLED = readFlag(import.meta.env.VITE_FEATURE_HANDS_FREE, true)

/** Phase 3 (ADR-0012): translation decision UI — badges, translated text,
 * pass-through chips. Server-side translation has its own setting; this flag
 * only hides the extra UI when the build must look like Phase 2. */
export const TRANSLATION_ENABLED = readFlag(import.meta.env.VITE_FEATURE_TRANSLATION, true)

/** Phase 5 (ADR-0014): two-participant rooms — invite link, guest join flow,
 * speaker labels. OFF hides the room UI (the app behaves like Phase 4 solo). */
export const ROOMS_ENABLED = readFlag(import.meta.env.VITE_FEATURE_ROOMS, true)
