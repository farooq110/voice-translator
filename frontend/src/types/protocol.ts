/**
 * Audio WebSocket protocol — version 1.
 *
 * TypeScript mirror of `backend/app/models/protocol.py`; the authoritative
 * spec lives in `docs/audio-protocol.md`. Inbound server frames are validated
 * at runtime (no blind casts, no `any`).
 */

export const PROTOCOL_VERSION = 1

export interface AudioFormat {
  encoding: 'pcm_s16le'
  sample_rate: number
  channels: number
}

export interface HelloConfig {
  native_language: string
  preferred_language: string
  audio: AudioFormat
}

// ---------------- client → server ----------------

export interface HelloMessage {
  v: typeof PROTOCOL_VERSION
  type: 'hello'
  config: HelloConfig
}

export interface PingMessage {
  v: typeof PROTOCOL_VERSION
  type: 'ping'
  ts?: number
}

export interface FlushMessage {
  v: typeof PROTOCOL_VERSION
  type: 'flush'
  /** BUG-001: client-generated correlation id, echoed on every event/error
   * for that flush so the client can settle exactly that utterance. */
  utterance_id?: string
}

export type ClientMessage = HelloMessage | PingMessage | FlushMessage

export function helloMessage(config: HelloConfig): HelloMessage {
  return { v: PROTOCOL_VERSION, type: 'hello', config }
}

export function pingMessage(ts?: number): PingMessage {
  return ts === undefined
    ? { v: PROTOCOL_VERSION, type: 'ping' }
    : { v: PROTOCOL_VERSION, type: 'ping', ts }
}

export function flushMessage(utteranceId?: string): FlushMessage {
  return utteranceId === undefined
    ? { v: PROTOCOL_VERSION, type: 'flush' }
    : { v: PROTOCOL_VERSION, type: 'flush', utterance_id: utteranceId }
}

// ---------------- server → client ----------------

export interface SttInfo {
  provider: string
  model: string
  device: string
  loaded: boolean
}

export interface ReadyEvent {
  v: number
  type: 'ready'
  session_id: string
  audio: AudioFormat
  stt: SttInfo
}

export interface TranscriptionEvent {
  v: number
  type: 'transcription'
  id: string
  text: string
  language: string
  language_probability: number
  audio_duration_s: number
  processing_ms: number
  is_final: boolean
  low_confidence: boolean
}

export interface ErrorEvent {
  v: number
  type: 'error'
  code: string
  message: string
  fatal: boolean
  /** BUG-001: the flush this error belongs to (echoed utterance_id); absent
   * for connection-level errors and server-initiated auto-flushes. */
  utterance_id?: string
}

export interface PongEvent {
  v: number
  type: 'pong'
  ts: number
}

export type ServerEvent = ReadyEvent | TranscriptionEvent | ErrorEvent | PongEvent

export class ProtocolError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ProtocolError'
  }
}

// ---------------- runtime validation ----------------

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function str(obj: Record<string, unknown>, key: string, ctx: string): string {
  const v = obj[key]
  if (typeof v !== 'string') throw new ProtocolError(`${ctx}: "${key}" must be a string`)
  return v
}

function num(obj: Record<string, unknown>, key: string, ctx: string): number {
  const v = obj[key]
  if (typeof v !== 'number' || Number.isNaN(v)) throw new ProtocolError(`${ctx}: "${key}" must be a number`)
  return v
}

function bool(obj: Record<string, unknown>, key: string, ctx: string): boolean {
  const v = obj[key]
  if (typeof v !== 'boolean') throw new ProtocolError(`${ctx}: "${key}" must be a boolean`)
  return v
}

function parseAudioFormat(value: unknown, ctx: string): AudioFormat {
  if (!isRecord(value)) throw new ProtocolError(`${ctx}: audio must be an object`)
  const encoding = value['encoding']
  if (encoding !== 'pcm_s16le') throw new ProtocolError(`${ctx}: unsupported audio encoding`)
  return {
    encoding,
    sample_rate: num(value, 'sample_rate', ctx),
    channels: num(value, 'channels', ctx),
  }
}

/** Validate and narrow one inbound server frame (a JSON text message). */
export function parseServerEvent(raw: string): ServerEvent {
  let data: unknown
  try {
    data = JSON.parse(raw)
  } catch {
    throw new ProtocolError('server frame is not valid JSON')
  }
  if (!isRecord(data)) throw new ProtocolError('server frame is not an object')
  const v = num(data, 'v', 'event')
  if (v !== PROTOCOL_VERSION) throw new ProtocolError(`unsupported protocol version: ${v}`)
  const type = str(data, 'type', 'event')

  switch (type) {
    case 'ready': {
      const stt = data['stt']
      if (!isRecord(stt)) throw new ProtocolError('ready: stt must be an object')
      return {
        v,
        type,
        session_id: str(data, 'session_id', 'ready'),
        audio: parseAudioFormat(data['audio'], 'ready'),
        stt: {
          provider: str(stt, 'provider', 'ready.stt'),
          model: str(stt, 'model', 'ready.stt'),
          device: str(stt, 'device', 'ready.stt'),
          loaded: bool(stt, 'loaded', 'ready.stt'),
        },
      }
    }
    case 'transcription':
      return {
        v,
        type,
        id: str(data, 'id', 'transcription'),
        text: str(data, 'text', 'transcription'),
        language: str(data, 'language', 'transcription'),
        language_probability: num(data, 'language_probability', 'transcription'),
        audio_duration_s: num(data, 'audio_duration_s', 'transcription'),
        processing_ms: num(data, 'processing_ms', 'transcription'),
        is_final: bool(data, 'is_final', 'transcription'),
        low_confidence: bool(data, 'low_confidence', 'transcription'),
      }
    case 'error': {
      const rawUtteranceId = data['utterance_id']
      return {
        v,
        type,
        code: str(data, 'code', 'error'),
        message: str(data, 'message', 'error'),
        fatal: bool(data, 'fatal', 'error'),
        utterance_id: typeof rawUtteranceId === 'string' ? rawUtteranceId : undefined,
      }
    }
    case 'pong':
      return { v, type, ts: num(data, 'ts', 'pong') }
    default:
      throw new ProtocolError(`unknown server event type: ${type}`)
  }
}
