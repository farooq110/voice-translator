/**
 * PlaybackGuard (Phase 5, ADR-0014) — the feedback-loop uplink mute.
 * Master prompt scenario 12 ("no feedback loop occurs") — client side.
 */
import { describe, expect, it } from 'vitest'
import { PlaybackGuard } from './playbackGuard'

describe('PlaybackGuard', () => {
  it('is not guarded initially', () => {
    expect(new PlaybackGuard().isGuarded(1000)).toBe(false)
  })

  it('guards for the clip duration plus the tail', () => {
    const guard = new PlaybackGuard(300)
    guard.onRemoteClip(2000, 1000)
    expect(guard.isGuarded(1000)).toBe(true)
    expect(guard.isGuarded(2500)).toBe(true)
    expect(guard.isGuarded(2300 - 1)).toBe(true)
    // 1000 + 2000 + 300 = 3300 → unguarded from 3300 on
    expect(guard.isGuarded(3300)).toBe(false)
  })

  it('extends the window for queued clips (never shortens it)', () => {
    const guard = new PlaybackGuard(100)
    guard.onRemoteClip(1000, 0)
    guard.onRemoteClip(5000, 10) // queued behind the first
    expect(guard.isGuarded(4000)).toBe(true)
    // 10 + 5000 + 100 (tail) = 5110 → unguarded from 5110 on
    expect(guard.isGuarded(5110 - 1)).toBe(true)
    expect(guard.isGuarded(5110)).toBe(false)
  })

  it('clear() unmutes immediately (user stop / mute / reconnect)', () => {
    const guard = new PlaybackGuard(300)
    guard.onRemoteClip(10_000, 0)
    guard.clear()
    expect(guard.isGuarded(1)).toBe(false)
  })

  it('tolerates a non-finite duration', () => {
    const guard = new PlaybackGuard(300)
    guard.onRemoteClip(Number.NaN, 0)
    expect(guard.isGuarded(299)).toBe(true)
    expect(guard.isGuarded(300)).toBe(false)
  })
})
