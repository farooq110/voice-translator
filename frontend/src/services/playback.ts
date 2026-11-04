/**
 * SpeechPlayer — bounded TTS playback queue (Phase 4, ADR-0013).
 *
 * Responsibilities (master prompt §17):
 * - **audio playback** — raw PCM16 (little-endian, mono) from the server is
 *   converted to Float32 and scheduled on a Web Audio timeline;
 * - **prevention of overlapping audio** — every clip is scheduled SEQUENTIALLY
 *   (each starts at `max(now, previous end)`), so two utterances can never
 *   talk over each other by construction;
 * - **playback queue management** — scheduled clips are kept in a bounded
 *   queue; when the bound is hit the OLDEST clip is dropped (in a live
 *   conversation the newest translation matters most) and the remaining
 *   clips are re-based so a cancelled clip never leaves silence behind;
 * - **cancellation of outdated audio** — `cancel(id)` (line deleted) and
 *   `stopAll()` (connection lost / user stop) stop sources immediately.
 *
 * The AudioContext is created lazily on the first user gesture (`resume()`
 * — browser autoplay policy) and is injectable for tests.
 */

export interface AudioMeta {
  id: string
  language: string
  sample_rate: number
}

export interface PlaybackState {
  /** Id of the clip currently audible (or starting). Null when silent. */
  activeId: string | null
  /** Clips scheduled after the active one. */
  waitingCount: number
}

interface QueueItem {
  id: string
  buffer: AudioBuffer
  source: AudioBufferSourceNode
  startAt: number
}

/** Bounded queue: 1 speaking + 3 waiting clips (a sentence is ~2–5 s). */
const MAX_PENDING_CLIPS = 4
const LEAD_S = 0.05

export class SpeechPlayer {
  private context: AudioContext | null = null
  private clips: QueueItem[] = []
  private nextStartAt = 0
  private enabled: boolean
  private droppedClips = 0
  private skippedClips = 0

  constructor(
    private readonly contextFactory: () => AudioContext = () => new AudioContext(),
    initialEnabled = true,
    private readonly onStateChange: (state: PlaybackState) => void = () => {},
  ) {
    this.enabled = initialEnabled
  }

  // ------------------------------------------------------------- control

  /** Create/resume the AudioContext — call from a user gesture (autoplay). */
  resume(): void {
    if (this.ensureContext() === null) return
    if (this.context !== null && this.context.state === 'suspended') {
      void this.context.resume()
    }
  }

  /** Mute/unmute. Turning mute ON cancels everything currently scheduled. */
  setEnabled(on: boolean): void {
    this.enabled = on
    if (!on) this.stopAll()
  }

  get isEnabled(): boolean {
    return this.enabled
  }

  get state(): PlaybackState {
    const now = this.context?.currentTime ?? 0
    const active = this.clips.find((clip) => clip.startAt <= now + LEAD_S)
    return {
      activeId: active?.id ?? null,
      waitingCount: Math.max(0, this.clips.length - 1),
    }
  }

  /** Clips dropped by the queue bound since creation (diagnostics). */
  get droppedClipCount(): number {
    return this.droppedClips
  }

  /** Clips skipped because playback was muted (diagnostics). */
  get skippedClipCount(): number {
    return this.skippedClips
  }

  // -------------------------------------------------------------- ingest

  /**
   * Queue one synthesized clip for playback. Returns false when muted (the
   * clip is intentionally not played) — queue overflow drops the OLDEST
   * clip and still plays the new one.
   */
  enqueue(meta: AudioMeta, pcm: ArrayBuffer): boolean {
    if (!this.enabled) {
      this.skippedClips += 1
      this.notify()
      return false
    }
    const context = this.ensureContext()
    if (context === null) return false
    if (context.state === 'suspended') void context.resume()

    const samples = new Int16Array(pcm)
    if (samples.length === 0 || meta.sample_rate <= 0) return false
    const buffer = context.createBuffer(1, samples.length, meta.sample_rate)
    const channel = buffer.getChannelData(0)
    for (let i = 0; i < samples.length; i += 1) {
      channel[i] = (samples[i] ?? 0) / 32768 // Int16 → Float32
    }

    // Queue bound: drop the oldest WAITING clip, keep the newest audio.
    while (this.clips.length >= MAX_PENDING_CLIPS) {
      const oldest = this.clips[0]
      if (oldest !== undefined && oldest.startAt > context.currentTime) {
        this.detach(oldest)
        this.stopSource(oldest.source)
        this.droppedClips += 1
        // Re-base the rest so the dropped clip leaves no silent gap.
        this.rescheduleAfter(context.currentTime)
      } else {
        break // the head is mid-play — drop nothing (bounded regardless)
      }
    }

    const startAt = Math.max(context.currentTime + LEAD_S, this.nextStartAt)
    this.startClip(meta.id, buffer, startAt)
    this.notify()
    return true
  }

  /** Remove one utterance's audio (queued or currently speaking). Any clips
   * scheduled behind it are re-based so no silence gap remains. */
  cancel(id: string): void {
    const clip = this.clips.find((item) => item.id === id)
    if (clip === undefined) return
    const now = this.context?.currentTime ?? 0
    const wasAudible = clip.startAt <= now + LEAD_S
    const removedAt = wasAudible ? now : clip.startAt
    this.detach(clip)
    this.stopSource(clip.source)
    this.rescheduleAfter(removedAt)
    this.notify()
  }

  /** Cancel everything (connection lost, user stop, mute). */
  stopAll(): void {
    if (this.clips.length === 0) return
    for (const clip of [...this.clips]) {
      this.stopSource(clip.source)
    }
    this.clips = []
    this.nextStartAt = 0
    this.notify()
  }

  dispose(): void {
    this.stopAll()
    void this.context?.close()
    this.context = null
  }

  // ----------------------------------------------------------- internals

  private ensureContext(): AudioContext | null {
    if (this.context !== null) return this.context
    try {
      this.context = this.contextFactory()
    } catch {
      this.context = null
    }
    return this.context
  }

  /** Schedule one clip at `startAt` and append it to the timeline. */
  private startClip(id: string, buffer: AudioBuffer, startAt: number): void {
    const context = this.context
    if (context === null) return
    const source = context.createBufferSource()
    source.buffer = buffer
    source.connect(context.destination)
    source.onended = () => this.removeClip(id, source)
    source.start(startAt)
    this.nextStartAt = startAt + buffer.duration
    this.clips.push({ id, buffer, source, startAt })
  }

  /** After removing `removedAt`-anchored audio, re-base every future clip so
   * the next one starts right away (never waiting for a dead clip's slot).
   * Currently playing clips are left untouched. */
  private rescheduleAfter(removedAt: number): void {
    const context = this.context
    if (context === null) return
    let t = Math.max(context.currentTime + LEAD_S, removedAt)
    for (const clip of [...this.clips]) {
      const playing = clip.startAt <= context.currentTime
      if (playing) {
        t = Math.max(t, clip.startAt + clip.buffer.duration)
        continue
      }
      if (Math.abs(clip.startAt - t) < 0.005) {
        t = clip.startAt + clip.buffer.duration
        continue // close enough — keep the existing source
      }
      // Future clip on a stale slot: stop it and re-schedule from `t`.
      this.stopSource(clip.source)
      this.detach(clip)
      this.startClip(clip.id, clip.buffer, t)
      t += clip.buffer.duration
    }
  }

  private removeClip(id: string, source: AudioBufferSourceNode): void {
    const clip = this.clips.find((item) => item.id === id)
    if (clip === undefined || clip.source !== source) return // stale event
    this.detach(clip)
    this.notify()
  }

  private detach(clip: QueueItem): void {
    this.clips = this.clips.filter((item) => item !== clip)
    if (this.clips.length === 0) this.nextStartAt = 0
  }

  private stopSource(source: AudioBufferSourceNode): void {
    try {
      source.stop()
    } catch {
      // already finished — nothing to stop
    }
  }

  private notify(): void {
    this.onStateChange(this.state)
  }
}
