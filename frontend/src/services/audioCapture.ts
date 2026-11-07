/**
 * Microphone capture via MediaDevices + AudioWorklet.
 *
 * - requests the mic with echo cancellation / noise suppression enabled;
 * - creates an AudioContext at the target rate (16 kHz) so the browser does
 *   the resampling whenever it honors the constraint;
 * - the worklet posts 100 ms PCM16 chunks + input level to the main thread;
 * - output path is muted (gain 0) so the capture graph is always pulled,
 *   with zero risk of audible feedback.
 */

export const TARGET_SAMPLE_RATE = 16_000

export type MicErrorCode =
  | 'not_supported'
  | 'permission_denied'
  | 'no_device'
  | 'context_suspended'
  | 'start_failed'

export class MicCaptureError extends Error {
  constructor(
    public readonly code: MicErrorCode,
    message: string,
  ) {
    super(message)
    this.name = 'MicCaptureError'
  }
}

export interface CaptureCallbacks {
  /** 100 ms PCM16 mono chunk at ~16 kHz (ArrayBuffer for WebSocket.send). */
  onChunk: (pcm16: ArrayBuffer) => void
  /** Input level (RMS, 0..1) at chunk cadence. */
  onLevel: (rms: number) => void
  /** Phase 6 (KI-004/015): the recording cannot continue — the caller must
   * finalize honestly instead of streaming silence. Two distinct causes
   * (QA round v0.6.2, ADR-0018):
   *  - 'context_interrupted' — the capture AudioContext stopped running
   *    (iOS reports 'interrupted' for phone calls / Siri / control center;
   *    Android can suspend the tab);
   *  - 'device_lost' — the input device disappeared mid-recording (headset
   *    unplugged, Bluetooth earbud left the room). The MediaStreamTrack
   *    ends while the AudioContext keeps running, so ONLY a track-ended
   *    listener can see it (GAP-604). */
  onStateLost?: (reason: StateLostReason) => void
}

export type StateLostReason = 'context_interrupted' | 'device_lost'

/** User-facing banner for a mid-recording capture loss. Kept next to the
 * capture service (which owns the causes) so code and copy cannot drift
 * apart; the hook only adds the finalize side effect. */
export function stateLostBanner(reason: StateLostReason): { code: string; message: string } {
  return reason === 'device_lost'
    ? {
        code: 'device_lost',
        message:
          'Recording stopped — the microphone was disconnected. Tap the mic to continue.',
      }
    : {
        code: 'capture_interrupted',
        message:
          'Recording stopped — the phone interrupted the microphone. Tap the mic to continue.',
      }
}

export interface CaptureHandle {
  stop: () => Promise<void>
  /** Actual AudioContext rate (16 kHz when honored by the browser). */
  contextRate: number
}

const WORKLET_URL = new URL('/worklets/pcm-capture-worklet.js', window.location.origin).href

type WebkitAudioContextCtor = typeof AudioContext

function getAudioContextCtor(): WebkitAudioContextCtor | null {
  const w = window as unknown as { AudioContext?: WebkitAudioContextCtor; webkitAudioContext?: WebkitAudioContextCtor }
  return w.AudioContext ?? w.webkitAudioContext ?? null
}

function classifyGetUserMediaError(err: unknown): MicCaptureError {
  if (err instanceof DOMException) {
    switch (err.name) {
      case 'NotAllowedError':
      case 'SecurityError':
        return new MicCaptureError(
          'permission_denied',
          'Microphone permission was denied. Allow microphone access in the browser and try again.',
        )
      case 'NotFoundError':
      case 'OverconstrainedError':
        return new MicCaptureError('no_device', 'No microphone was found on this device.')
      case 'NotSupportedError':
        // BUG-603 (QA round v0.6.2): some environments (WebViews, kiosk
        // builds, headless denial quirk) surface a refused/unavailable mic
        // as NotSupportedError instead of NotAllowedError. The generic
        // start_failed fallback rendered the raw "Not supported" text —
        // give it the honest access-guidance message instead.
        return new MicCaptureError(
          'permission_denied',
          'Microphone access was refused or is unavailable — check site permissions.',
        )
      default:
        break
    }
  }
  return new MicCaptureError(
    'start_failed',
    err instanceof Error ? err.message : 'Failed to access the microphone.',
  )
}

export async function startMicCapture(callbacks: CaptureCallbacks): Promise<CaptureHandle> {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new MicCaptureError(
      'not_supported',
      'This browser does not support microphone capture (getUserMedia).',
    )
  }

  let stream: MediaStream
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
        channelCount: 1,
      },
    })
  } catch (err) {
    throw classifyGetUserMediaError(err)
  }

  const Ctor = getAudioContextCtor()
  if (!Ctor) {
    stream.getTracks().forEach((track) => track.stop())
    throw new MicCaptureError('not_supported', 'Web Audio API is unavailable in this browser.')
  }

  const context = new Ctor({ sampleRate: TARGET_SAMPLE_RATE })

  try {
    if (context.state === 'suspended') {
      await context.resume().catch(() => undefined)
    }
    if (context.state !== 'running') {
      throw new MicCaptureError(
        'context_suspended',
        'Audio playback/capture is blocked. Tap the page and try again.',
      )
    }

    await context.audioWorklet.addModule(WORKLET_URL)

    const source = context.createMediaStreamSource(stream)
    const node = new AudioWorkletNode(context, 'pcm-capture', {
      numberOfInputs: 1,
      numberOfOutputs: 1,
      outputChannelCount: [1],
    })

    node.port.onmessage = (event: MessageEvent) => {
      const data: unknown = event.data
      if (typeof data !== 'object' || data === null) return
      const kind = (data as { type?: unknown }).type
      if (kind === 'chunk') {
        const buffer = (data as { buffer?: unknown }).buffer
        if (buffer instanceof ArrayBuffer) callbacks.onChunk(buffer)
      } else if (kind === 'level') {
        const rms = (data as { rms?: unknown }).rms
        if (typeof rms === 'number') callbacks.onLevel(rms)
      }
    }

    // Muted monitor path: guarantees the worklet is pulled on every engine
    // without any audible output (and without feedback risk).
    const mute = context.createGain()
    mute.gain.value = 0
    source.connect(node)
    node.connect(mute)
    mute.connect(context.destination)

    // Mobile interruption watchdog (Phase 6): the OS can suspend the capture
    // graph at any moment without closing the stream. Surface it once — the
    // listener is removed in stop() so our own close never fires it.
    if (callbacks.onStateLost) {
      const onStateChange = () => {
        if (context.state !== 'running') {
          context.removeEventListener('statechange', onStateChange)
          callbacks.onStateLost?.('context_interrupted')
        }
      }
      context.addEventListener('statechange', onStateChange)
    }

    // GAP-604 (QA round v0.6.2): a lost input device ends the
    // MediaStreamTrack while the AudioContext keeps running — the
    // statechange watchdog above cannot see it and the app would stream
    // silence behind a live "recording" badge (the exact zombie-recording
    // class Phase 6 eliminated for backgrounding). End the recording
    // honestly instead. stop() removes this listener first: our own
    // teardown must never look like a device loss.
    let onTrackEnded: (() => void) | null = null
    if (callbacks.onStateLost) {
      // Self-removing, like the statechange watchdog: surface the loss once.
      const handler = (): void => {
        stream.getAudioTracks().forEach((track) => {
          track.removeEventListener('ended', handler)
        })
        onTrackEnded = null
        callbacks.onStateLost?.('device_lost')
      }
      onTrackEnded = handler
      stream.getAudioTracks().forEach((track) => {
        track.addEventListener('ended', handler)
      })
    }

    return {
      contextRate: context.sampleRate,
      stop: async () => {
        const endedHandler = onTrackEnded
        if (endedHandler !== null) {
          stream.getAudioTracks().forEach((track) => {
            track.removeEventListener('ended', endedHandler)
          })
          onTrackEnded = null
        }
        node.port.onmessage = null
        try {
          node.disconnect()
          source.disconnect()
          mute.disconnect()
        } catch {
          // already torn down
        }
        stream.getTracks().forEach((track) => track.stop())
        await context.close().catch(() => undefined)
      },
    }
  } catch (err) {
    stream.getTracks().forEach((track) => track.stop())
    await context.close().catch(() => undefined)
    if (err instanceof MicCaptureError) throw err
    throw new MicCaptureError(
      'start_failed',
      err instanceof Error ? err.message : 'Failed to start audio capture.',
    )
  }
}
