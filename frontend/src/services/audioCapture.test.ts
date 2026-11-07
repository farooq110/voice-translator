/**
 * Regression coverage for the QA round v0.6.2 (Journal, 2026-11-07):
 *
 * - GAP-604 (medium): a lost input device (unplugged headset, Bluetooth
 *   earbud leaving range) ends the MediaStreamTrack while the AudioContext
 *   keeps running. The capture service must report onStateLost('device_lost')
 *   exactly once, and its own stop() must never be mistaken for a device
 *   loss (listener disarmed before teardown).
 * - BUG-603 (low): a mic refusal that surfaces as DOMException
 *   NotSupportedError (WebViews/kiosk builds, headless denial quirk) must
 *   classify as permission guidance, not the raw "start failed — Not
 *   supported" banner. The classifier is exercised through the public
 *   startMicCapture path for every DOMException name the browser emits.
 *
 * The module under test builds the worklet URL from window.location at
 * import time and vitest runs in the node environment, so the module is
 * imported dynamically after the browser globals are stubbed.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

type StateLostReason = 'context_interrupted' | 'device_lost'

interface CaptureHandle {
  stop: () => Promise<void>
  contextRate: number
}

type CaptureCallbacks = {
  onChunk: (pcm16: ArrayBuffer) => void
  onLevel: (rms: number) => void
  onStateLost?: (reason: StateLostReason) => void
}

type StartMicCapture = (callbacks: CaptureCallbacks) => Promise<CaptureHandle>

type AudioCaptureModule = {
  startMicCapture: StartMicCapture
  stateLostBanner: (reason: StateLostReason) => { code: string; message: string }
}

class FakeTrack {
  readonly listeners = new Map<string, Set<() => void>>()
  stopped = false

  addEventListener(name: string, fn: () => void): void {
    if (!this.listeners.has(name)) this.listeners.set(name, new Set())
    this.listeners.get(name)!.add(fn)
  }

  removeEventListener(name: string, fn: () => void): void {
    this.listeners.get(name)?.delete(fn)
  }

  end(): void {
    for (const fn of [...(this.listeners.get('ended') ?? [])]) fn()
  }

  stop(): void {
    this.stopped = true
  }
}

class FakeStream {
  constructor(readonly track: FakeTrack) {}
  getAudioTracks(): FakeTrack[] {
    return [this.track]
  }
  getTracks(): FakeTrack[] {
    return [this.track]
  }
}

class FakeAudioContext {
  static readonly created: FakeAudioContext[] = []

  constructor() {
    FakeAudioContext.created.push(this)
  }

  state: AudioContextState = 'running'
  sampleRate = 16_000
  destination = {}
  closed = false
  readonly workletModules: string[] = []
  private stateChangeHandlers = new Set<() => void>()
  readonly audioWorklet = {
    addModule: async (url: string) => {
      this.workletModules.push(url)
    },
  }

  addEventListener(name: string, fn: () => void): void {
    if (name === 'statechange') this.stateChangeHandlers.add(fn)
  }

  removeEventListener(name: string, fn: () => void): void {
    if (name === 'statechange') this.stateChangeHandlers.delete(fn)
  }

  /** Simulate the OS suspending/interrupting the capture graph. */
  suspend(): void {
    this.state = 'interrupted' as AudioContextState
    for (const fn of [...this.stateChangeHandlers]) fn()
  }

  resume(): Promise<void> {
    this.state = 'running'
    return Promise.resolve()
  }

  createMediaStreamSource(): { connect: () => void; disconnect: () => void } {
    return { connect: () => undefined, disconnect: () => undefined }
  }

  createGain(): { gain: { value: number }; connect: () => void; disconnect: () => void } {
    return { gain: { value: 0 }, connect: () => undefined, disconnect: () => undefined }
  }

  close(): Promise<void> {
    this.closed = true
    return Promise.resolve()
  }
}

class FakeWorkletNode {
  port = {
    onmessage: null as ((event: MessageEvent) => void) | null,
  }
  connect(): void {
    return undefined
  }
  disconnect(): void {
    return undefined
  }
}

const noopCallbacks = (): CaptureCallbacks => ({ onChunk: () => undefined, onLevel: () => undefined })

async function loadModule(): Promise<AudioCaptureModule> {
  return (await import('../services/audioCapture')) as unknown as AudioCaptureModule
}

describe('startMicCapture — getUserMedia error classification (BUG-603)', () => {
  beforeEach(() => {
    vi.stubGlobal('window', { location: { origin: 'http://localhost:3000' } })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  const expectRejectedWith = async (
    err: DOMException | Error,
    expected: { code: string; message: string },
  ): Promise<void> => {
    vi.stubGlobal('navigator', {
      mediaDevices: { getUserMedia: () => Promise.reject(err) },
    })
    const { startMicCapture } = await loadModule()
    await expect(startMicCapture(noopCallbacks())).rejects.toMatchObject({
      name: 'MicCaptureError',
      code: expected.code,
      message: expected.message,
    })
  }

  it('maps NotAllowedError to permission guidance', async () => {
    await expectRejectedWith(new DOMException('denied', 'NotAllowedError'), {
      code: 'permission_denied',
      message:
        'Microphone permission was denied. Allow microphone access in the browser and try again.',
    })
  })

  it('maps SecurityError to permission guidance', async () => {
    await expectRejectedWith(new DOMException('insecure', 'SecurityError'), {
      code: 'permission_denied',
      message:
        'Microphone permission was denied. Allow microphone access in the browser and try again.',
    })
  })

  it('maps NotFoundError to no_device', async () => {
    await expectRejectedWith(new DOMException('gone', 'NotFoundError'), {
      code: 'no_device',
      message: 'No microphone was found on this device.',
    })
  })

  it('maps OverconstrainedError to no_device', async () => {
    await expectRejectedWith(new DOMException('unsatisfiable', 'OverconstrainedError'), {
      code: 'no_device',
      message: 'No microphone was found on this device.',
    })
  })

  it('maps NotSupportedError to permission guidance, not the raw "Not supported" text (BUG-603)', async () => {
    await expectRejectedWith(new DOMException('Not supported', 'NotSupportedError'), {
      code: 'permission_denied',
      message: 'Microphone access was refused or is unavailable — check site permissions.',
    })
  })

  it('leaves NotReadableError (device busy) as an honest start_failed with the browser message', async () => {
    await expectRejectedWith(new DOMException('device busy', 'NotReadableError'), {
      code: 'start_failed',
      message: 'device busy',
    })
  })

  it('maps non-DOM errors to start_failed with the error message', async () => {
    await expectRejectedWith(new Error('boom'), {
      code: 'start_failed',
      message: 'boom',
    })
  })

  it('reports an unsupported browser as not_supported when getUserMedia is missing', async () => {
    vi.stubGlobal('navigator', {})
    const { startMicCapture } = await loadModule()
    await expect(startMicCapture(noopCallbacks())).rejects.toMatchObject({
      code: 'not_supported',
    })
  })
})

describe('startMicCapture — device-loss and interruption watchdogs (GAP-604)', () => {
  let track: FakeTrack
  let context: FakeAudioContext

  beforeEach(() => {
    track = new FakeTrack()
    FakeAudioContext.created.length = 0
    vi.stubGlobal('window', {
      location: { origin: 'http://localhost:3000' },
      AudioContext: FakeAudioContext,
    })
    vi.stubGlobal('navigator', {
      mediaDevices: { getUserMedia: () => Promise.resolve(new FakeStream(track)) },
    })
    vi.stubGlobal('AudioWorkletNode', FakeWorkletNode)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('starts and reports the real context rate', async () => {
    const { startMicCapture } = await loadModule()
    const handle = await startMicCapture(noopCallbacks())
    expect(handle.contextRate).toBe(16_000)
    await handle.stop()
  })
  it('fires onStateLost("device_lost") exactly once when the track ends mid-recording (GAP-604)', async () => {
    const { startMicCapture } = await loadModule()
    const onStateLost = vi.fn()
    const handle = await startMicCapture({ ...noopCallbacks(), onStateLost })

    track.end()
    track.end() // a second ended event must not double-report

    expect(onStateLost).toHaveBeenCalledTimes(1)
    expect(onStateLost).toHaveBeenCalledWith('device_lost')
    await handle.stop()
  })

  it('never mistakes its own stop() for a device loss (listener disarmed before teardown)', async () => {
    const { startMicCapture } = await loadModule()
    const onStateLost = vi.fn()
    const handle = await startMicCapture({ ...noopCallbacks(), onStateLost })
    const created = FakeAudioContext.created[0]
    if (created === undefined) throw new Error('capture context was not created')
    context = created

    await handle.stop()
    track.end() // engine quirk guard: ended after stop() must stay silent
    expect(track.stopped).toBe(true)
    expect(context.closed).toBe(true)
    expect(onStateLost).not.toHaveBeenCalled()
  })

  it('keeps the interruption watchdog: a suspended context reports "context_interrupted"', async () => {
    const { startMicCapture } = await loadModule()
    const onStateLost = vi.fn()
    const handle = await startMicCapture({ ...noopCallbacks(), onStateLost })
    const created = FakeAudioContext.created[0]
    if (created === undefined) throw new Error('capture context was not created')
    context = created

    context.suspend()

    expect(onStateLost).toHaveBeenCalledTimes(1)
    expect(onStateLost).toHaveBeenCalledWith('context_interrupted')
    await handle.stop()
  })

  it('works without an onStateLost callback (a device loss then only ends the track)', async () => {
    const { startMicCapture } = await loadModule()
    const handle = await startMicCapture(noopCallbacks())
    expect(() => track.end()).not.toThrow()
    await handle.stop()
  })

  it('names the cause in the user-facing banner copy', async () => {
    const { stateLostBanner } = await loadModule()
    expect(stateLostBanner('device_lost')).toEqual({
      code: 'device_lost',
      message: 'Recording stopped — the microphone was disconnected. Tap the mic to continue.',
    })
    expect(stateLostBanner('context_interrupted')).toEqual({
      code: 'capture_interrupted',
      message:
        'Recording stopped — the phone interrupted the microphone. Tap the mic to continue.',
    })
  })
})
