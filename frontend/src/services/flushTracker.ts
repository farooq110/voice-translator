/**
 * FlushTracker — per-utterance attribution of in-flight decodes (BUG-001).
 *
 * The QA audit (Phase 2, BUG-001) showed the old single-ref attribution
 * (`lastFlushHadSpeechRef` + one shared counter) mis-attributes outcomes
 * when errors arrive out of order under server load:
 *
 *  (a) a speech flush answered `no_speech_detected` AFTER a newer
 *      silence-only drain overwrote the shared ref took the silent-return
 *      branch without settling → the pending counter leaked and the
 *      `Transcribing…` indicator stayed up forever;
 *  (b) a `server_busy`/`stt_timeout` for a silence-only drain (which never
 *      incremented the counter) settled unconditionally → the indicator
 *      cleared while a real speech decode was still in flight.
 *
 * The server (protocol v1, BUG-001 fix) now echoes a client-generated
 * `utterance_id` on every transcription and flush-scoped error. The tracker
 * maps id → hadSpeech at flush time and settles strictly by id, so each
 * outcome touches exactly the utterance it belongs to. The indicator is
 * DERIVED (`hasSpeechPending()`), so a stuck state is structurally
 * impossible: any leak would require a map entry that never settles, and
 * every entry is removed by its own outcome or `clear()` on reconnect.
 */

export class FlushTracker {
  private readonly pending = new Map<string, boolean>()

  /** Record a flushed utterance and whether the client heard speech in it. */
  register(id: string, hadSpeech: boolean): void {
    this.pending.set(id, hadSpeech)
  }

  /** hadSpeech for a known id; undefined when the id is unknown. */
  hadSpeech(id: string): boolean | undefined {
    return this.pending.get(id)
  }

  /** Remove one settled utterance (any outcome). Returns whether speech
   * flushes are still in flight afterwards (the Transcribing… state). */
  settle(id: string): boolean {
    this.pending.delete(id)
    return this.hasSpeechPending()
  }

  /** True while at least one flush that contained speech is unsettled. */
  hasSpeechPending(): boolean {
    for (const hadSpeech of this.pending.values()) {
      if (hadSpeech) return true
    }
    return false
  }

  /** Number of in-flight utterances (speech and silence drains). */
  get size(): number {
    return this.pending.size
  }

  /** Drop everything — used when the connection is lost (the server drops
   * in-flight results for closed sessions anyway, ADR-0009). */
  clear(): void {
    this.pending.clear()
  }
}
