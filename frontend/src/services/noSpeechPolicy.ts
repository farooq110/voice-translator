/**
 * Policy: which server error events surface as UI banners.
 *
 * Phase 2 adds a server-side VAD gate (ADR-0009): a flush containing only
 * silence is answered with `no_speech_detected` within milliseconds. In
 * hands-free use (mic open for long stretches, ADR-0007 endpointing drains
 * the buffer at the 20 s cap even when nobody spoke) that is EXPECTED
 * behavior, not an error — surfacing it would spam banners while the user
 * is simply quiet.
 *
 * The client knows what it flushed: if the flushed segment contained real
 * speech (RMS above the endpointing threshold) and the server still says
 * "no speech", that IS worth showing — the user spoke and it was not
 * recognized. Silence-only flushes stay silent.
 */

export function surfaceNoSpeechError(flushHadSpeech: boolean): boolean {
  return flushHadSpeech
}
