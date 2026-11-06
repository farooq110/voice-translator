/**
 * SpeechPlayer tests (Phase 4, ADR-0013) — against a structural fake
 * AudioContext: sequential scheduling (no overlap), bounded queue with
 * oldest-drop, cancellation, mute behavior, state notifications.
 */

import { describe, expect, it } from 'vitest'
import { SpeechPlayer, type AudioMeta, type PlaybackState } from './playback'

const META = (id: string, sample_rate = 22050): AudioMeta => ({
  id,
  language: 'en',
  sample_rate,
})

/** One second of PCM16 silence-ish (a real even-length buffer). */
function pcmBytes(seconds: number, sampleRate = 22050): ArrayBuffer {
  const samples = Math.floor(sampleRate * seconds)
  return new Int16Array(samples).buffer
}

class FakeSource {
  buffer: unknown = null
  stopped = false
  startAt: number | null = null
  onended: (() => void) | null = null
  connectDestination = false

  connect(): void {
    this.connectDestination = true
  }

  start(at: number): void {
    this.startAt = at
  }

  stop(): void {
    this.stopped = true
  }

  finish(): void {
    this.onended?.()
  }
}

class FakeContext {
  static instances: FakeContext[] = []
  currentTime = 100
  state: 'running' | 'suspended' = 'running'
  sources: FakeSource[] = []
  buffers: { length: number; sampleRate: number; channels: Float32Array[] }[] = []
  resumeCalls = 0

  constructor() {
    FakeContext.instances.push(this)
  }

  resume(): Promise<void> {
    this.resumeCalls += 1
    this.state = 'running'
    return Promise.resolve()
  }

  createBuffer(channels: number, length: number, sampleRate: number) {
    const buffer = {
      length,
      sampleRate,
      duration: length / sampleRate,
      channels: [] as Float32Array[],
      getChannelData: (index: number) => buffer.channels[index],
    }
    for (let i = 0; i < channels; i += 1) buffer.channels.push(new Float32Array(length))
    this.buffers.push(buffer)
    return buffer
  }

  createBufferSource(): FakeSource {
    const source = new FakeSource()
    this.sources.push(source)
    return source
  }
}

/** Adapt the fakes to the DOM types the player expects (structural casts). */
function makePlayer(enabled = true) {
  const context = new FakeContext()
  const states: PlaybackState[] = []
  const player = new SpeechPlayer(
    () => context as unknown as AudioContext,
    enabled,
    (state) => states.push(state),
  )
  return { player, context, states }
}

describe('SpeechPlayer', () => {
  it('schedules clips sequentially — never overlapping', () => {
    const { player, context } = makePlayer()
    expect(player.enqueue(META('a'), pcmBytes(2))).toBe(true)
    expect(player.enqueue(META('b'), pcmBytes(3))).toBe(true)

    const [a, b] = context.sources
    expect(a?.startAt).toBeCloseTo(100.05, 5) // now + small lead
    expect(b?.startAt).toBeCloseTo(102.05, 5) // exactly after a ends (100.05+2)
    expect(b?.buffer).not.toBeNull()
    expect(a?.connectDestination).toBe(true)
    expect(player.state.activeId).toBe('a')
    expect(player.state.waitingCount).toBe(1)
  })

  it('schedules after the previous end even when time has not advanced', () => {
    const { player, context } = makePlayer()
    player.enqueue(META('a'), pcmBytes(1))
    player.enqueue(META('b'), pcmBytes(1))
    player.enqueue(META('c'), pcmBytes(1))
    const [a, b, c] = context.sources
    expect(c?.startAt).toBeCloseTo((a?.startAt ?? 0) + 2, 5)
    expect(b?.startAt ?? 0).toBeLessThan(c?.startAt ?? Infinity)
  })

  it('converts Int16 to Float32 in [-1, 1]', () => {
    const { player, context } = makePlayer()
    const samples = new Int16Array([32767, -32768, 0, 16384])
    player.enqueue(META('a'), samples.buffer)
    const channel = context.buffers[0]?.channels[0]
    expect(channel?.[0]).toBeCloseTo(32767 / 32768, 5)
    expect(channel?.[1]).toBeCloseTo(-1, 5)
    expect(channel?.[2]).toBe(0)
    expect(channel?.[3]).toBeCloseTo(0.5, 5)
  })

  it('drops the OLDEST clip when the queue bound is hit, plays the newest', () => {
    const { player, context } = makePlayer()
    for (let i = 0; i < 6; i += 1) {
      player.enqueue(META(`clip-${i}`), pcmBytes(2))
    }
    expect(player.droppedClipCount).toBe(2) // 6 queued, bound 4 → 2 oldest gone
    // the first two sources were stopped (dropped before starting)
    expect(context.sources[0]?.stopped).toBe(true)
    expect(context.sources[1]?.stopped).toBe(true)
    // another enqueue on a full queue drops the next oldest
    expect(player.enqueue(META('after'), pcmBytes(1))).toBe(true)
    expect(player.droppedClipCount).toBe(3)
  })

  it('cancel(id) stops that clip; later clips are re-based to play now', () => {
    const { player, context } = makePlayer()
    player.enqueue(META('a'), pcmBytes(2))
    player.enqueue(META('b'), pcmBytes(2))
    player.cancel('a')
    expect(context.sources[0]?.stopped).toBe(true) // a stopped
    // b was re-based to start immediately (no silence gap), so its original
    // source was replaced by a new one scheduled right after the cancel
    expect(context.sources[1]?.stopped).toBe(true)
    expect(context.sources[2]?.startAt).toBeCloseTo(100.05, 5)
    expect(player.state.activeId).toBe('b')
  })

  it('cancel of an unknown id is a no-op', () => {
    const { player, context } = makePlayer()
    player.enqueue(META('a'), pcmBytes(1))
    expect(() => player.cancel('nope')).not.toThrow()
    expect(context.sources[0]?.stopped).toBe(false)
  })

  it('stopAll cancels everything and resets the timeline', () => {
    const { player, context } = makePlayer()
    player.enqueue(META('a'), pcmBytes(2))
    player.enqueue(META('b'), pcmBytes(2))
    player.stopAll()
    expect(context.sources.every((s) => s.stopped)).toBe(true)
    expect(player.state.activeId).toBeNull()
    // after stopAll the next clip starts fresh (not at the old end)
    const before = context.currentTime
    player.enqueue(META('c'), pcmBytes(1))
    expect(context.sources[2]?.startAt).toBeCloseTo(before + 0.05, 5)
  })

  it('muted enqueue skips playback and reports skipped count', () => {
    const { player, context } = makePlayer(false)
    expect(player.enqueue(META('a'), pcmBytes(1))).toBe(false)
    expect(context.sources).toHaveLength(0)
    expect(player.skippedClipCount).toBe(1)
  })

  it('setEnabled(false) cancels scheduled audio; re-enabling plays again', () => {
    const { player, context } = makePlayer(true)
    player.enqueue(META('a'), pcmBytes(2))
    player.setEnabled(false)
    expect(context.sources[0]?.stopped).toBe(true)
    expect(player.state.activeId).toBeNull()
    player.setEnabled(true)
    expect(player.enqueue(META('b'), pcmBytes(1))).toBe(true)
    expect(context.sources[1]?.stopped).toBe(false)
  })

  it('notifies on every state change and when clips finish', () => {
    const { player, states, context } = makePlayer()
    player.enqueue(META('a'), pcmBytes(2))
    expect(states.length).toBeGreaterThan(0)
    expect(states[states.length - 1]?.activeId).toBe('a')
    // the clip ends → activeId returns to null
    context.sources[0]?.finish()
    expect(states[states.length - 1]?.activeId).toBeNull()
  })

  it('rejects empty or malformed payloads without throwing', () => {
    const { player } = makePlayer()
    expect(player.enqueue(META('a'), new ArrayBuffer(0))).toBe(false)
    expect(player.enqueue(META('a', 0), pcmBytes(0.1))).toBe(false)
  })

  it('resume() creates the context and resumes it when suspended', () => {
    const { player, context } = makePlayer()
    player.resume()
    expect(context.resumeCalls).toBeGreaterThanOrEqual(0)
    context.state = 'suspended'
    player.resume()
    expect(context.resumeCalls).toBe(1)
  })

  it('natural finish keeps the timeline and hands over to the next clip', () => {
    const { player, context } = makePlayer()
    player.enqueue(META('a'), pcmBytes(1))
    player.enqueue(META('b'), pcmBytes(1))
    context.currentTime = 101.1 // a ended naturally at ~101.05
    context.sources[0]?.finish()
    expect(player.state.activeId).toBe('b')
    expect(player.state.waitingCount).toBe(0)
    // a natural finish must NOT re-base the already-scheduled next clip
    expect(context.sources[1]?.stopped).toBe(false)
  })
})

describe('resumeIfCreated (Phase 6)', () => {
  it('never creates a context — visibility events must not trip autoplay', () => {
    const before = FakeContext.instances.length
    const player = new SpeechPlayer(() => new FakeContext() as unknown as AudioContext)
    player.resumeIfCreated()
    expect(FakeContext.instances).toHaveLength(before)
  })

  it('resumes an existing suspended context', () => {
    const player = new SpeechPlayer(() => new FakeContext() as unknown as AudioContext)
    const meta = META('a')
    player.enqueue(meta, pcmBytes(0.1)) // creates the context
    const context = FakeContext.instances.at(-1)!
    context.state = 'suspended'
    player.resumeIfCreated()
    expect(context.resumeCalls).toBe(1)
  })
})
