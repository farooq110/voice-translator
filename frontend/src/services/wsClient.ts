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
import type { ClientMessage, HelloConfig, ServerEvent } from '../types/protocol'

export type WsStatus = 'idle' | 'connecting' | 'connected' | 'reconnecting' | 'closed' | 'error'

export interface WsClientHandlers {
  onStatus: (status: WsStatus) => void
  onEvent: (event: ServerEvent) => void
  onProtocolError: (error: ProtocolError) => void
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

export class TranslatorWsClient {
  private ws: WebSocket | null = null
  private status: WsStatus = 'idle'
  private attempt = 0
  private manualClose = false
  private ready = false
  private pingTimer: ReturnType<typeof setTimeout> | null = null
  private pongTimer: ReturnType<typeof setTimeout> | null = null
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null

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
    if (!this.isReady() || this.ws === null) return false
    this.ws.send(buffer)
    return true
  }

  flush(): void {
    if (!this.isReady() || this.ws === null) return
    this.sendJson(flushMessage())
  }

  // ------------------------------------------------------------- internals

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
      if (typeof event.data !== 'string') return // protocol v1 is text-only inbound
      let event_: ServerEvent
      try {
        event_ = parseServerEvent(event.data)
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
      this.options.handlers.onEvent(event_)
    }

    ws.onerror = () => {
      // onclose always follows onerror; reconnect logic lives there.
    }

    ws.onclose = () => {
      this.clearTimers()
      this.ready = false
      this.ws = null
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
