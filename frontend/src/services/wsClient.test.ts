import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TranslatorWsClient, type WsStatus } from './wsClient'
import type { ServerEvent } from '../types/protocol'

/** Minimal structural fake — injected via webSocketFactory (no real sockets). */
class FakeWebSocket {
  static instances: FakeWebSocket[] = []

  sent: (string | ArrayBuffer)[] = []
  readyState = 0 // CONNECTING
  binaryType = ''
  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  onmessage: ((event: { data: unknown }) => void) | null = null

  constructor(public readonly url: string) {
    FakeWebSocket.instances.push(this)
  }

  send(data: string | ArrayBuffer): void {
    this.sent.push(data)
  }

  close(): void {
    if (this.readyState === 3) return
    this.readyState = 3
    this.onclose?.()
  }

  // -- test helpers ---------------------------------------------------------
  open(): void {
    this.readyState = 1
    this.onopen?.()
  }

  serverFrame(payload: unknown): void {
    this.onmessage?.({ data: JSON.stringify(payload) })
  }

  sentJson(): unknown[] {
    return this.sent
      .filter((item): item is string => typeof item === 'string')
      .map((item) => JSON.parse(item) as unknown)
  }
}

const CONFIG = (): {
  native_language: string
  preferred_language: string
  audio: { encoding: 'pcm_s16le'; sample_rate: number; channels: number }
} => ({
  native_language: 'ur',
  preferred_language: 'en',
  audio: { encoding: 'pcm_s16le', sample_rate: 16000, channels: 1 },
})

const READY_EVENT = {
  v: 1,
  type: 'ready',
  session_id: 's_test1234',
  audio: { encoding: 'pcm_s16le', sample_rate: 16000, channels: 1 },
  stt: { provider: 'fake', model: 'm', device: 'cpu', loaded: true },
}

function makeClient(): {
  client: TranslatorWsClient
  statuses: WsStatus[]
  events: ServerEvent[]
  protocolErrors: string[]
} {
  const statuses: WsStatus[] = []
  const events: ServerEvent[] = []
  const protocolErrors: string[] = []
  const client = new TranslatorWsClient({
    url: 'ws://testserver/ws/audio',
    config: CONFIG,
    handlers: {
      onStatus: (status) => statuses.push(status),
      onEvent: (event) => events.push(event),
      onProtocolError: (error) => protocolErrors.push(error.message),
    },
    webSocketFactory: (url) => new FakeWebSocket(url) as unknown as WebSocket,
  })
  return { client, statuses, events, protocolErrors }
}

function lastSocket(): FakeWebSocket {
  const instance = FakeWebSocket.instances.at(-1)
  if (!instance) throw new Error('no socket created')
  return instance
}

beforeEach(() => {
  FakeWebSocket.instances = []
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('TranslatorWsClient', () => {
  it('connects, sends hello, becomes ready', async () => {
    const { client, statuses } = makeClient()
    client.connect()
    expect(statuses).toEqual(['connecting'])

    const ws = lastSocket()
    ws.open()
    await vi.advanceTimersByTimeAsync(0)

    const hello = ws
      .sentJson()
      .find((msg) => (msg as { type?: string }).type === 'hello')
    expect(hello).toMatchObject({
      v: 1,
      type: 'hello',
      config: { native_language: 'ur', preferred_language: 'en' },
    })

    ws.serverFrame(READY_EVENT)
    expect(client.isReady()).toBe(true)
    expect(statuses).toContain('connected')
  })

  it('delivers server events', async () => {
    const { client, events } = makeClient()
    client.connect()
    lastSocket().open()
    await vi.advanceTimersByTimeAsync(0)
    lastSocket().serverFrame(READY_EVENT)
    lastSocket().serverFrame({ ...READY_EVENT, type: 'pong', ts: 3 })
    expect(events.map((event) => event.type)).toEqual(['ready', 'pong'])
  })

  it('sends audio chunks and flush only when ready', async () => {
    const { client } = makeClient()
    client.connect()
    const notReady = client.sendAudioChunk(new ArrayBuffer(4))
    expect(notReady).toBe(false)

    const ws = lastSocket()
    ws.open()
    await vi.advanceTimersByTimeAsync(0)
    ws.serverFrame(READY_EVENT)

    const chunk = new ArrayBuffer(32)
    expect(client.sendAudioChunk(chunk)).toBe(true)
    client.flush()
    expect(ws.sent).toContain(chunk)
    const types = ws.sentJson().map((msg) => (msg as { type?: string }).type)
    expect(types).toContain('flush')
  })

  it('sends the correlation id on flush (BUG-001)', async () => {
    const { client } = makeClient()
    client.connect()
    const ws = lastSocket()
    ws.open()
    await vi.advanceTimersByTimeAsync(0)
    ws.serverFrame(READY_EVENT)

    client.flush('c_0001_ab12cd')
    const flushMsg = ws
      .sentJson()
      .find((msg) => (msg as { type?: string }).type === 'flush') as
      | { type: string; utterance_id?: string }
      | undefined
    expect(flushMsg?.utterance_id).toBe('c_0001_ab12cd')
    // and without an id the field stays absent (server generates its own)
    client.flush()
    const plainFlush = ws
      .sentJson()
      .filter((msg) => (msg as { type?: string }).type === 'flush') as { utterance_id?: string }[]
    expect(plainFlush[1]).not.toHaveProperty('utterance_id')
  })

  it('drops audio chunks when the uplink stalls (backpressure)', async () => {
    const { client } = makeClient()
    client.connect()
    const ws = lastSocket()
    ws.open()
    await vi.advanceTimersByTimeAsync(0)
    ws.serverFrame(READY_EVENT)

    // simulate a stalled uplink: the socket's send buffer is past the cap
    Object.defineProperty(ws, 'bufferedAmount', { value: 1_048_577, configurable: true })
    const chunk = new ArrayBuffer(32)
    expect(client.sendAudioChunk(chunk)).toBe(false)
    expect(client.droppedChunkCount).toBe(1)
    expect(ws.sent).not.toContain(chunk)

    // a JSON control message (flush) still goes through — only audio drops
    client.flush()
    expect(ws.sentJson().map((msg) => (msg as { type?: string }).type)).toContain('flush')

    // buffer drains → chunks flow again
    Object.defineProperty(ws, 'bufferedAmount', { value: 0, configurable: true })
    expect(client.sendAudioChunk(chunk)).toBe(true)
  })

  it('pings periodically', async () => {
    const { client } = makeClient()
    client.connect()
    const ws = lastSocket()
    ws.open()
    await vi.advanceTimersByTimeAsync(0)
    ws.serverFrame(READY_EVENT)

    await vi.advanceTimersByTimeAsync(20_000)
    const types = ws.sentJson().map((msg) => (msg as { type?: string }).type)
    expect(types).toContain('ping')
  })

  it('treats a missing pong as a dead connection and reconnects', async () => {
    const { client, statuses } = makeClient()
    client.connect()
    const first = lastSocket()
    first.open()
    await vi.advanceTimersByTimeAsync(0)
    first.serverFrame(READY_EVENT)

    await vi.advanceTimersByTimeAsync(20_000) // ping sent, pong timer armed
    await vi.advanceTimersByTimeAsync(10_000) // pong timeout → close
    expect(statuses).toContain('reconnecting')

    await vi.advanceTimersByTimeAsync(600) // first backoff step
    const second = lastSocket()
    expect(second).not.toBe(first)
    second.open()
    await vi.advanceTimersByTimeAsync(0)
    second.serverFrame(READY_EVENT)
    expect(client.isReady()).toBe(true)
  })

  it('reconnects with capped exponential backoff', async () => {
    const { client } = makeClient()
    client.connect()
    lastSocket().close() // never opened → immediate reconnect path

    await vi.advanceTimersByTimeAsync(600)
    expect(FakeWebSocket.instances.length).toBe(2)
    lastSocket().close()
    await vi.advanceTimersByTimeAsync(1_200)
    expect(FakeWebSocket.instances.length).toBe(3)
    lastSocket().close()
    // attempt 4 waits 2.4s; only 2s elapsed → no new socket yet
    await vi.advanceTimersByTimeAsync(2_000)
    expect(FakeWebSocket.instances.length).toBe(3)
    await vi.advanceTimersByTimeAsync(400)
    expect(FakeWebSocket.instances.length).toBe(4)
  })

  it('close() stops reconnection permanently', async () => {
    const { client, statuses } = makeClient()
    client.connect()
    lastSocket().close()
    client.close()
    expect(statuses.at(-1)).toBe('closed')
    await vi.advanceTimersByTimeAsync(60_000)
    expect(FakeWebSocket.instances.length).toBe(1)
  })

  it('surfaces protocol errors without crashing', async () => {
    const { client, protocolErrors } = makeClient()
    client.connect()
    const ws = lastSocket()
    ws.open()
    await vi.advanceTimersByTimeAsync(0)
    ws.serverFrame({ v: 1, type: 'mystery' })
    expect(protocolErrors.length).toBe(1)
    // connection still usable afterwards
    ws.serverFrame(READY_EVENT)
    expect(client.isReady()).toBe(true)
  })

  // ---------------- Phase 4 (ADR-0013): audio meta + binary pairing ------

  const AUDIO_META = {
    v: 1,
    type: 'audio',
    id: 'c_0001_dead01',
    language: 'en',
    encoding: 'pcm_s16le',
    sample_rate: 22050,
    channels: 1,
    bytes: 4,
    duration_ms: 100,
    synthesis_ms: 12,
    provider: 'piper',
  }

  function makeClientWithAudio(): {
    client: TranslatorWsClient
    events: ServerEvent[]
    protocolErrors: string[]
    audio: { meta: unknown; pcm: ArrayBuffer }[]
  } {
    const events: ServerEvent[] = []
    const protocolErrors: string[] = []
    const audio: { meta: unknown; pcm: ArrayBuffer }[] = []
    const client = new TranslatorWsClient({
      url: 'ws://testserver/ws/audio',
      config: CONFIG,
      handlers: {
        onStatus: () => {},
        onEvent: (event) => events.push(event),
        onProtocolError: (error) => protocolErrors.push(error.message),
        onAudio: (meta, pcm) => audio.push({ meta, pcm }),
      },
      webSocketFactory: (url) => new FakeWebSocket(url) as unknown as WebSocket,
    })
    return { client, events, protocolErrors, audio }
  }

  it('pairs audio metadata with the following binary frame (ADR-0013)', async () => {
    const { client, events, audio } = makeClientWithAudio()
    client.connect()
    const ws = lastSocket()
    ws.open()
    await vi.advanceTimersByTimeAsync(0)
    ws.serverFrame(READY_EVENT)
    ws.serverFrame(AUDIO_META)
    const pcm = new Int16Array([1, -1, 2, -2]).buffer
    ws.onmessage?.({ data: pcm })

    // metadata is NOT forwarded via onEvent — only as a paired unit
    expect(events.map((event) => event.type)).toEqual(['ready'])
    expect(audio).toHaveLength(1)
    expect(audio[0]?.meta).toMatchObject({ id: 'c_0001_dead01', language: 'en' })
    expect(audio[0]?.pcm).toBe(pcm)
  })

  it('keeps pairing across consecutive audio clips in order', async () => {
    const { client, audio } = makeClientWithAudio()
    client.connect()
    const ws = lastSocket()
    ws.open()
    await vi.advanceTimersByTimeAsync(0)
    const pcmA = new Int16Array([1, 2]).buffer
    const pcmB = new Int16Array([3, 4]).buffer
    ws.serverFrame({ ...AUDIO_META, id: 'a' })
    ws.onmessage?.({ data: pcmA })
    ws.serverFrame({ ...AUDIO_META, id: 'b' })
    ws.onmessage?.({ data: pcmB })
    expect(audio.map((item) => (item.meta as { id: string }).id)).toEqual(['a', 'b'])
    expect(audio[0]?.pcm).toBe(pcmA)
    expect(audio[1]?.pcm).toBe(pcmB)
  })

  it('ignores binary frames without pending audio metadata', async () => {
    const { client, audio } = makeClientWithAudio()
    client.connect()
    const ws = lastSocket()
    ws.open()
    await vi.advanceTimersByTimeAsync(0)
    ws.onmessage?.({ data: new Int16Array([9, 9]).buffer })
    expect(audio).toHaveLength(0)
    expect(client.strayBinaryFrameCount).toBe(1)
  })

  it('clears pending audio metadata on close (no stale pairing)', async () => {
    const { client, audio } = makeClientWithAudio()
    client.connect()
    const ws = lastSocket()
    ws.open()
    await vi.advanceTimersByTimeAsync(0)
    ws.serverFrame(AUDIO_META)
    ws.close() // binary never arrived
    client.connect()
    lastSocket().open()
    await vi.advanceTimersByTimeAsync(0)
    lastSocket().onmessage?.({ data: new Int16Array([5, 5]).buffer })
    expect(audio).toHaveLength(0) // the stale meta was dropped with the socket
    expect(lastSocket().sentJson().some((msg) => (msg as { type?: string }).type === 'hello')).toBe(true)
  })

  it('rejects an audio event with a wrong encoding via protocol error', async () => {
    const { client, protocolErrors, audio } = makeClientWithAudio()
    client.connect()
    const ws = lastSocket()
    ws.open()
    await vi.advanceTimersByTimeAsync(0)
    ws.serverFrame({ ...AUDIO_META, encoding: 'mp3' })
    expect(protocolErrors.length).toBe(1)
    expect(audio).toHaveLength(0)
  })
})

describe('TranslatorWsClient — relay pairing (Phase 5, ADR-0014)', () => {
  const RELAY_META = {
    v: 1,
    type: 'relay',
    id: 'h_c_0001',
    speaker: 'host',
    language: 'es',
    encoding: 'pcm_s16le',
    sample_rate: 16000,
    channels: 1,
    bytes: 4,
    duration_ms: 125,
  }

  function makeClientWithRelay(): {
    client: TranslatorWsClient
    events: ServerEvent[]
    relays: { meta: unknown; pcm: ArrayBuffer }[]
    protocolErrors: string[]
  } {
    const events: ServerEvent[] = []
    const relays: { meta: unknown; pcm: ArrayBuffer }[] = []
    const protocolErrors: string[] = []
    const client = new TranslatorWsClient({
      url: 'ws://testserver/ws/audio',
      config: CONFIG,
      handlers: {
        onStatus: () => {},
        onEvent: (event) => events.push(event),
        onProtocolError: (error) => protocolErrors.push(error.message),
        onRelay: (meta, pcm) => relays.push({ meta, pcm }),
      },
      webSocketFactory: (url) => new FakeWebSocket(url) as unknown as WebSocket,
    })
    return { client, events, relays, protocolErrors }
  }

  it('pairs a relay metadata event with the following binary frame', async () => {
    const { client, relays } = makeClientWithRelay()
    client.connect()
    const ws = lastSocket()
    ws.open()
    await vi.advanceTimersByTimeAsync(0)
    const pcm = new Int16Array([7, 8]).buffer
    ws.serverFrame(RELAY_META)
    ws.onmessage?.({ data: pcm })
    expect(relays).toHaveLength(1)
    expect(relays[0]?.meta).toMatchObject({ type: 'relay', id: 'h_c_0001', speaker: 'host' })
    expect(relays[0]?.pcm).toBe(pcm)
  })

  it('does NOT deliver relay metadata through onEvent and never through onAudio', async () => {
    const { client, events, relays } = makeClientWithRelay()
    client.connect()
    const ws = lastSocket()
    ws.open()
    await vi.advanceTimersByTimeAsync(0)
    ws.serverFrame(RELAY_META)
    ws.onmessage?.({ data: new Int16Array([1, 1]).buffer })
    expect(events).toHaveLength(0)
    expect(relays).toHaveLength(1)
  })

  it('keeps audio and relay pairings independent and in order', async () => {
    const events: ServerEvent[] = []
    const audio: unknown[] = []
    const relays: unknown[] = []
    const client = new TranslatorWsClient({
      url: 'ws://testserver/ws/audio',
      config: CONFIG,
      handlers: {
        onStatus: () => {},
        onEvent: (event) => events.push(event),
        onProtocolError: () => {},
        onAudio: (meta) => audio.push(meta),
        onRelay: (meta) => relays.push(meta),
      },
      webSocketFactory: (url) => new FakeWebSocket(url) as unknown as WebSocket,
    })
    client.connect()
    const ws = lastSocket()
    ws.open()
    await vi.advanceTimersByTimeAsync(0)
    ws.serverFrame(RELAY_META)
    ws.onmessage?.({ data: new Int16Array([1, 2]).buffer })
    ws.serverFrame({
      v: 1, type: 'audio', id: 'g_c_0001', language: 'ur',
      encoding: 'pcm_s16le', sample_rate: 22050, channels: 1,
      bytes: 4, duration_ms: 900, synthesis_ms: 40, provider: 'piper', speaker: 'guest',
    })
    ws.onmessage?.({ data: new Int16Array([3, 4]).buffer })
    expect(relays).toHaveLength(1)
    expect(audio).toHaveLength(1)
    expect((audio[0] as { speaker?: string }).speaker).toBe('guest')
  })
})
