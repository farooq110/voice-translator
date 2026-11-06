/**
 * PlaybackGuard — feedback-loop prevention for two-participant sessions
 * (Phase 5, ADR-0014, master prompt scenario 12).
 *
 * When the OTHER participant's audio (a translated voice clip or a relayed
 * original utterance) plays through this device's loudspeaker, the device's
 * own microphone could re-capture that output and start an acoustic
 * translation loop (A speaks → B's device plays the translation → B's mic
 * hears it → B's client transcribes+translates it → A's device plays THAT →
 * …). While a remote clip is scheduled to be audible — plus a short tail —
 * the guard says "muted", and the session hook drops uplink chunks instead
 * of sending them.
 *
 * The browser's echo canceller already removes loudspeaker audio from the
 * mic path in most cases; the guard is the deterministic second line of
 * defense (and the only one in browsers/hardware where AEC is weak).
 *
 * Own-utterance TTS (the Phase 1–4 solo local playback) is NOT remote and
 * must NOT arm the guard — muting the user's own mic while their own
 * translation plays would break solo hands-free conversations.
 */

const DEFAULT_TAIL_MS = 300

export class PlaybackGuard {
  private until = 0

  constructor(private readonly tailMs: number = DEFAULT_TAIL_MS) {}

  /** A remote clip of `durationMs` was scheduled — stay muted until it has
   * finished (sequential queue: later clips extend the window, never shorten). */
  onRemoteClip(durationMs: number, now: number = Date.now()): void {
    const safeDuration = Number.isFinite(durationMs) && durationMs > 0 ? durationMs : 0
    this.until = Math.max(this.until, now + safeDuration + this.tailMs)
  }

  /** All playback was stopped/cancelled — unmute immediately. */
  clear(): void {
    this.until = 0
  }

  /** True while remote audio is (or is about to be) audible. */
  isGuarded(now: number = Date.now()): boolean {
    return now < this.until
  }
}
