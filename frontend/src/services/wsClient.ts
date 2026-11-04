/**
 * Translator WebSocket client — protocol v1.
 *
 * Responsibilities:
 * - connect + `hello` handshake (config read at connect time via callback);
 * - auto-reconnect with capped exponential backoff (network resilience);
 * - heartbeat ping/pong with timeout → dead connection detection;
 * - typed event delivery (parseServerEvent, runtime-validated);
 * - binary PCM16 upload (only when the session is ready).
 *
 * The WebSocket constructor is injectable for tests.
 */

import {
  flushMessage,
  helloMessage,
  parseServerEvent,
  pingMessage,
  ProtocolError,
} from '../types/protocol'
import type { AudioEvent, ClientMessage, HelloConfig, RelayEvent, ServerEvent } from '../types/protocol'

export type WsStatus = 'idle' | 'connecting' | 'connected' | 'reconnecting' | 'closed' | 'error'

export interface WsClientHandlers {
  onStatus: (status: WsStatus) => void
  onEvent: (event: ServerEvent) => void
  onProtocolError: (error: ProtocolError) => void
  /** Phase 4 (ADR-0013): the `audio` metadata event is NOT delivered via
   * `onEvent` — it is paired with the NEXT binary frame (WebSocket ordering
   * guarantees it follows) and delivered here as one playable unit. */
  onAudio?: (meta: AudioEvent, pcm: ArrayBuffer) => void
  /** Phase 5 (ADR-0014): same pairing contract for the pass-through
   * `relay` event (original audio for the OTHER participant). */
  onRelay?: (meta: RelayEvent, pcm: ArrayBuffer) => void
}

export interface WsClientOptions {
  url: string
  /** Read at (re)connect time so the latest user config is always sent. */
  config: () => HelloConfig
  handlers: WsClientHandlers
  webSocketFactory?: (url: string) => WebSocket
  pingIntervalMs?: number
  pongTimeoutMs?: number
  maxBackoffMs?: number
}

const DEFAULT_PING_MS = 20_000
const DEFAULT_PONG_TIMEOUT_MS = 10_000
const DEFAULT_MAX_BACKOFF_MS = 10_000
const BASE_BACKOFF_MS = 600

/** Backpressure (Phase 2): if the uplink stalls and the socket's send buffer
 * grows past this, DROP new audio chunks instead of queueing without bound
 * (~32 s of audio at the 32 kB/s stream rate). Audio loss under genuine
 * congestion is preferable to unbounded memory growth and ever-staler
 * transcripts; the heartbeat detects a truly dead connection either way. */
const MAX_WS_BUFFERED_BYTES = 1_048_576

export class TranslatorWsClient {
  private ws: WebSocket | null = null
  private status: WsStatus = 'idle'
  private attempt = 0
  private manualClose = false
  private ready = false
  private pingTimer: ReturnType<typeof setTimeout> | null = null
  private pongTimer: ReturnType<typeof setTimeout> | null = null
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private droppedChunks = 0
  /** Phase 4/5: metadata of the most recent `audio` OR `relay` event,
   * waiting for its binary frame (overwritten with a warning if a protocol
   * violation ever interleaves). */
  private pendingMedia: { kind: 'audio' | 'relay'; meta: AudioEvent | RelayEvent } | null = null
  private strayBinaryFrames = 0

  constructor(private readonly options: WsClientOptions) {}

  // ------------------------------------------------------------- lifecycle

  connect(): void {
    this.manualClose = false
    this.openSocket()
  }

  close(): void {
    this.manualClose = true
    this.clearTimers()
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    this.ws?.close()
    this.ws = null
    this.ready = false
    this.setStatus('closed')
  }

  isReady(): boolean {
    return this.ready && this.ws !== null && this.ws.readyState === WebSocket.OPEN
  }

  // ---------------------------------------------------------------- upload

  sendAudioChunk(buffer: ArrayBuffer): boolean {
    const ws = this.ws
    if (!this.isReady() || ws === null) return false
    if (ws.bufferedAmount > MAX_WS_BUFFERED_BYTES) {
      this.droppedChunks += 1
      if (this.droppedChunks === 1 || this.droppedChunks % 50 === 0) {
        console.warn(
          `[ws] slow uplink (buffered ${ws.bufferedAmount} B) — dropped ${this.droppedChunks} audio chunk(s)`,
        )
      }
      return false
    }
    ws.send(buffer)
    return true
  }

  /** Chunks dropped by the backpressure policy since connect (diagnostics). */
  get droppedChunkCount(): number {
    return this.droppedChunks
  }

  /** Binary frames ignored because no `audio` metadata preceded them. */
  get strayBinaryFrameCount(): number {
    return this.strayBinaryFrames
  }

  /** Flush the server buffer. `utteranceId` (BUG-001) is the client-generated
   * correlation id echoed on the transcription/error for this flush, so the
   * session hook can settle exactly that utterance's pending state. */
  flush(utteranceId?: string): void {
    if (!this.isReady() || this.ws === null) return
    this.sendJson(flushMessage(utteranceId))
  }

  // ------------------------------------------------------------- internals

  private handleTextFrame(raw: string): void {
    let event_: ServerEvent
    try {
      event_ = parseServerEvent(raw)
    } catch (error) {
      if (error instanceof ProtocolError) {
        this.options.handlers.onProtocolError(error)
        return
      }
      throw error
    }
    if (event_.type === 'ready') {
      this.ready = true
      this.attempt = 0
      this.clearPongTimer()
      this.setStatus('connected')
    }
    if (event_.type === 'pong') {
      this.clearPongTimer()
    }
    if (event_.type === 'audio' || event_.type === 'relay') {
      // Pair with the following binary frame (delivered via onAudio/onRelay).
      if (this.pendingMedia !== null) {
        console.warn('[ws] media metadata arrived without its binary frame — replaced')
      }
      this.pendingMedia = { kind: event_.type, meta: event_ }
      return
    }
    this.options.handlers.onEvent(event_)
  }

  private handleBinaryFrame(data: ArrayBuffer): void {
    const pending = this.pendingMedia
    this.pendingMedia = null
    if (pending === null) {
      // Not part of the protocol today — ignore without crashing.
      this.strayBinaryFrames += 1
      return
    }
    if (pending.kind === 'audio') {
      this.options.handlers.onAudio?.(pending.meta as AudioEvent, data)
    } else {
      this.options.handlers.onRelay?.(pending.meta as RelayEvent, data)
    }
  }

  private sendJson(message: ClientMessage): void {
    if (this.ws === null || this.ws.readyState !== WebSocket.OPEN) return
    this.ws.send(JSON.stringify(message))
  }

  private setStatus(status: WsStatus): void {
    if (this.status === status) return
    this.status = status
    this.options.handlers.onStatus(status)
  }

  private openSocket(): void {
    this.clearTimers()
    this.ready = false
    this.setStatus(this.attempt === 0 ? 'connecting' : 'reconnecting')

    const ws = this.options.webSocketFactory
      ? this.options.webSocketFactory(this.options.url)
      : new WebSocket(this.options.url)
    ws.binaryType = 'arraybuffer'
    this.ws = ws

    ws.onopen = () => {
      this.sendJson(helloMessage(this.options.config()))
      this.startPingLoop()
    }
    ws.onmessage = (event: MessageEvent) => {
      if (typeof event.data === 'string') {
        this.handleTextFrame(event.data)
      } else {
        this.handleBinaryFrame(event.data)
      }
    }

    ws.onerror = () => {
      // onclose always follows onerror; reconnect logic lives there.
    }

    ws.onclose = () => {
      this.clearTimers()
      this.ready = false
      this.ws = null
      this.pendingMedia = null
      if (this.manualClose) {
        this.setStatus('closed')
        return
      }
      this.scheduleReconnect()
    }
  }

  private scheduleReconnect(): void {
    this.attempt += 1
    const max = this.options.maxBackoffMs ?? DEFAULT_MAX_BACKOFF_MS
    const delay = Math.min(max, BASE_BACKOFF_MS * 2 ** (this.attempt - 1))
    this.setStatus('reconnecting')
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null
      this.openSocket()
    }, delay)
  }

  private startPingLoop(): void {
    const interval = this.options.pingIntervalMs ?? DEFAULT_PING_MS
    this.pingTimer = setInterval(() => {
      if (this.ws === null || this.ws.readyState !== WebSocket.OPEN) return
      const ts = Date.now()
      this.sendJson(pingMessage(ts))
      const timeout = this.options.pongTimeoutMs ?? DEFAULT_PONG_TIMEOUT_MS
      this.clearPongTimer()
      this.pongTimer = setTimeout(() => {
        // No pong in time → treat the connection as dead; onclose will
        // trigger the reconnect path.
        if (this.ws !== null && this.ws.readyState === WebSocket.OPEN) {
          this.ws.close()
        }
      }, timeout)
    }, interval)
  }

  private clearPongTimer(): void {
    if (this.pongTimer !== null) {
      clearTimeout(this.pongTimer)
      this.pongTimer = null
    }
  }

  private clearTimers(): void {
    if (this.pingTimer !== null) {
      clearInterval(this.pingTimer)
      this.pingTimer = null
    }
    this.clearPongTimer()
  }
}
