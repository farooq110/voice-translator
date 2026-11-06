/**
 * Wake Lock wrapper tests (Phase 6, KI-004/KI-015) — acquire/release
 * bookkeeping against a fake sentinel, including OS-initiated release and
 * unsupported browsers.
 */

import { describe, expect, it } from 'vitest'
import { WakeLock, type WakeLockSentinelLike } from './wakeLock'

class FakeSentinel implements WakeLockSentinelLike {
  released = false
  private listener: (() => void) | null = null

  addEventListener(type: 'release', listener: () => void): void {
    if (type === 'release') this.listener = listener
  }

  async release(): Promise<void> {
    if (this.released) return
    this.released = true
    this.listener?.()
  }

  /** Simulate the OS dropping the lock (page hidden, battery saver). */
  osDrop(): void {
    this.listener?.()
  }
}

function fakeApi() {
  const sentinels: FakeSentinel[] = []
  return {
    sentinels,
    requestCount: 0,
    api: {
      request: async () => {
        const sentinel = new FakeSentinel()
        sentinels.push(sentinel)
        return sentinel
      },
    },
  }
}

describe('WakeLock', () => {
  it('reports unsupported without the API', () => {
    expect(WakeLock.fromNavigator(undefined).supported).toBe(false)
    expect(WakeLock.fromNavigator({}).supported).toBe(false)
  })

  it('acquires and releases', async () => {
    const { api, sentinels } = fakeApi()
    const lock = new WakeLock(api)
    expect(lock.held).toBe(false)
    expect(await lock.acquire()).toBe(true)
    expect(lock.held).toBe(true)
    await lock.release()
    expect(sentinels[0]?.released).toBe(true)
    expect(lock.held).toBe(false)
  })

  it('re-acquiring while held does not stack sentinels', async () => {
    const { api } = fakeApi()
    const lock = new WakeLock(api)
    expect(await lock.acquire()).toBe(true)
    expect(await lock.acquire()).toBe(true)
    await lock.release()
    expect(lock.held).toBe(false)
  })

  it('forgets the sentinel when the OS releases it', async () => {
    const { api, sentinels } = fakeApi()
    const lock = new WakeLock(api)
    await lock.acquire()
    sentinels[0]!.osDrop()
    expect(lock.held).toBe(false)
  })

  it('degrades silently on request denial', async () => {
    const lock = new WakeLock({
      request: async () => {
        throw new Error('NotAllowedError')
      },
    })
    expect(await lock.acquire()).toBe(false)
    expect(lock.held).toBe(false)
  })

  it('release without a held lock is a no-op', async () => {
    const lock = new WakeLock(fakeApi().api)
    await expect(lock.release()).resolves.toBeUndefined()
  })
})
