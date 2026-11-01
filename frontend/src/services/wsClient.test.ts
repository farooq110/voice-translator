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
})
