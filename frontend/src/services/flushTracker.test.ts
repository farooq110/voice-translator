import { describe, expect, it } from 'vitest'
import { FlushTracker } from './flushTracker'

describe('FlushTracker (BUG-001 utterance attribution)', () => {
  it('settles exactly the utterance a transcript belongs to', () => {
    const tracker = new FlushTracker()
    tracker.register('c_0001', true)
    tracker.register('c_0002', true)
    expect(tracker.hasSpeechPending()).toBe(true)

    // first result lands — one speech flush still pending
    expect(tracker.settle('c_0001')).toBe(true)
    // last result lands — indicator can clear
    expect(tracker.settle('c_0002')).toBe(false)
  })

  it('mode (a): a speech flush rejected after a later drain settles its OWN id', () => {
    // Old bug: the drain overwrote the shared had-speech ref, so the speech
    // flush's no_speech_detected took the silent-return branch and leaked a
    // pending unit — the Transcribing… indicator never cleared.
    const tracker = new FlushTracker()
    tracker.register('c_speech', true) // user spoke → indicator on
    tracker.register('c_drain', false) // later silence drain

    // the drain's own rejection settles only the drain…
    expect(tracker.hadSpeech('c_drain')).toBe(false)
    expect(tracker.settle('c_drain')).toBe(true) // speech still pending → stay busy
    // …the speech flush is still attributed correctly and settles last
    expect(tracker.hadSpeech('c_speech')).toBe(true)
    expect(tracker.settle('c_speech')).toBe(false) // indicator clears
    expect(tracker.size).toBe(0)
  })

  it('mode (b): an error for a silence drain never settles a speech flush', () => {
    // Old bug: the unconditional settle decremented the counter owed by an
    // in-flight SPEECH flush, clearing the indicator too early.
    const tracker = new FlushTracker()
    tracker.register('c_speech', true)
    tracker.register('c_drain', false)

    // server_busy for the drain (id attributed) — speech stays pending
    expect(tracker.settle('c_drain')).toBe(true)
    // only the speech flush's own outcome may clear the indicator
    expect(tracker.settle('c_speech')).toBe(false)
  })

  it('unknown ids never settle anything', () => {
    const tracker = new FlushTracker()
    tracker.register('c_0001', true)
    // server auto-flush result (id generated server-side, unknown to us)
    expect(tracker.settle('u_0001_abcd')).toBe(true)
    expect(tracker.size).toBe(1)
    expect(tracker.hadSpeech('u_0001_abcd')).toBeUndefined()
  })

  it('clear() drops everything (reconnect path)', () => {
    const tracker = new FlushTracker()
    tracker.register('c_0001', true)
    tracker.register('c_0002', false)
    tracker.clear()
    expect(tracker.size).toBe(0)
    expect(tracker.hasSpeechPending()).toBe(false)
  })
})
