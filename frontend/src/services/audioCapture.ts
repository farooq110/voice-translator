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

    return {
      contextRate: context.sampleRate,
      stop: async () => {
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
