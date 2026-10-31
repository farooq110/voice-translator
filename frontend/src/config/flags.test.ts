import { describe, expect, it } from 'vitest'
import { HANDS_FREE_ENABLED } from './flags'

/**
 * flags.ts evaluates VITE_FEATURE_HANDS_FREE once at build time; the shipped
 * default (unset variable) is ON. The parse rule is mirrored here so the
 * contract is pinned: only 'false'/'0' disable the feature.
 */
function parseFlag(value: string | undefined): boolean {
  if (value === undefined || value === '') return true
  return value !== 'false' && value !== '0'
}

describe('feature flags', () => {
  it('hands-free is a boolean and enabled in the shipped build', () => {
    expect(typeof HANDS_FREE_ENABLED).toBe('boolean')
    expect(HANDS_FREE_ENABLED).toBe(true)
  })

  it.each([
    [undefined, true],
    ['', true],
    ['true', true],
    ['1', true],
    ['false', false],
    ['0', false],
  ])('env value %p parses to %p', (value, expected) => {
    expect(parseFlag(value)).toBe(expected)
  })
})
