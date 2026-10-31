import { describe, expect, it } from 'vitest'
import { surfaceNoSpeechError } from './noSpeechPolicy'

describe('surfaceNoSpeechError', () => {
  it('surfaces no_speech when the flushed segment contained real speech', () => {
    // the user spoke (RMS above the endpointing threshold) and the server
    // still found no speech — that is worth showing
    expect(surfaceNoSpeechError(true)).toBe(true)
  })

  it('suppresses no_speech for silence-only drains (hands-free idle)', () => {
    // nobody spoke for 20 s (endpointing cap) → server VAD gate rejects the
    // drain flush — expected, must not spam a banner
    expect(surfaceNoSpeechError(false)).toBe(false)
  })
})
