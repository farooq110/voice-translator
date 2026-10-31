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
