/**
 * Screen Wake Lock wrapper (Phase 6, KI-004/KI-015).
 *
 * A locked phone suspends microphone capture within seconds, so the screen
 * must stay awake while the mic is open. The API is progressive
 * enhancement: unsupported browsers (and denials — low-power mode, hidden
 * page) degrade to "no lock held" without any error surface. The OS may
 * drop the lock on its own (it fires a `release` event when the page is
 * hidden); the capture guard stops recording in that case anyway.
 */

export interface WakeLockSentinelLike {
  addEventListener(type: 'release', listener: () => void): void
  release(): Promise<void>
}

interface WakeLockApi {
  request(type: 'screen'): Promise<WakeLockSentinelLike>
}

type NavigatorWithWakeLock = { wakeLock?: WakeLockApi }

export class WakeLock {
  private sentinel: WakeLockSentinelLike | null = null

  constructor(private readonly api: WakeLockApi | null | undefined) {}

  static fromNavigator(nav: NavigatorWithWakeLock | null | undefined): WakeLock {
    return new WakeLock(nav?.wakeLock ?? null)
  }

  get supported(): boolean {
    return this.api != null
  }

  get held(): boolean {
    return this.sentinel !== null
  }

  /** Best-effort acquire. Resolves true only when a lock is held after. */
  async acquire(): Promise<boolean> {
    if (this.api == null) return false
    if (this.sentinel !== null) return true
    try {
      const sentinel = await this.api.request('screen')
      sentinel.addEventListener('release', () => {
        // The OS dropped the lock (page hidden, battery saver). Forget it —
        // a re-acquire is the caller's decision, not an automatic loop.
        if (this.sentinel === sentinel) this.sentinel = null
      })
      this.sentinel = sentinel
      return true
    } catch {
      return false
    }
  }

  async release(): Promise<void> {
    const sentinel = this.sentinel
    this.sentinel = null
    if (sentinel !== null) {
      try {
        await sentinel.release()
      } catch {
        // already released by the OS — nothing to do
      }
    }
  }
}
