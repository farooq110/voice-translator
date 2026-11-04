/**
 * Audio WebSocket protocol — version 1.
 *
 * TypeScript mirror of `backend/app/models/protocol.py`; the authoritative
 * spec lives in `docs/audio-protocol.md`. Inbound server frames are validated
 * at runtime (no blind casts, no `any`).
 */

export const PROTOCOL_VERSION = 1

export type SpeakerRole = 'host' | 'guest'

export interface AudioFormat {
  encoding: 'pcm_s16le'
  sample_rate: number
  channels: number
}

export interface HelloConfig {
  /** Phase 5 (ADR-0014): the host (translator user) configures the pair; a
   * guest sends neither — the room's pair (the host's) governs. */
  native_language?: string
  preferred_language?: string
  role?: SpeakerRole
  /** Invite code — a guest MUST present it; a host may re-send its own code
   * to rejoin after a network drop (the room lingers briefly). */
  room?: string
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
  /** Phase 5 (ADR-0014): present when the connection joined a room. */
  room?: RoomInfo
}

export interface RoomInfo {
  code: string
  role: SpeakerRole
  /** How many OTHER participants are in the room right now. */
  peers: number
  /** The room's configured pair (the host's — guests configure nothing). */
  native_language: string
  preferred_language: string
}

/** Room membership change (Phase 5): the other participant joined/left. */
export interface PeerEvent {
  v: number
  type: 'peer'
  event: 'joined' | 'left'
  role: SpeakerRole
  peers: number
}

export interface TranscriptionEvent {
  v: number
  type: 'transcription'
  id: string
  /** Phase 5 (ADR-0014): who spoke — present only in room mode. Clients key
   * transcript entries by (speaker, id): ids are unique per connection. */
  speaker?: SpeakerRole
  text: string
  language: string
  language_probability: number
  audio_duration_s: number
  processing_ms: number
  is_final: boolean
  low_confidence: boolean
  /** Phase 3 (ADR-0012): present only when the server has translation
   * enabled. `action="translate"` is followed by a `translation` event with
   * the same id; `passthrough` means NO translation will arrive. */
  decision?: TranslationDecision
}

// ---------------- translation decision (Phase 3, ADR-0012) ----------------

export type TranslationAction = 'translate' | 'passthrough'

export type TranslationReason =
  | 'native_language'
  | 'preferred_language'
  | 'third_language'
  | 'identical_config'
  | 'low_confidence'
  | 'undetected_language'
  | 'unsupported_pair'
  | 'text_too_long'

export const TRANSLATION_REASONS: readonly TranslationReason[] = [
  'native_language',
  'preferred_language',
  'third_language',
  'identical_config',
  'low_confidence',
  'undetected_language',
  'unsupported_pair',
  'text_too_long',
]

export type TranslationDetection = 'whisper' | 'cluster' | 'pinned' | 'fallback' | 'unknown'

const TRANSLATION_DETECTIONS: readonly TranslationDetection[] = [
  'whisper',
  'cluster',
  'pinned',
  'fallback',
  'unknown',
]

export interface TranslationDecision {
  action: TranslationAction
  reason: TranslationReason
  source_language: string
  /** Set only for `action="translate"`. */
  target_language?: string
  detection: TranslationDetection
}

export interface TranslationEvent {
  v: number
  type: 'translation'
  id: string
  /** Phase 5: who spoke the translated utterance (room mode). */
  speaker?: SpeakerRole
  text: string
  source_language: string
  target_language: string
  provider: string
  duration_ms: number
}

/**
 * Synthesized-speech metadata (Phase 4, ADR-0013).
 *
 * Arrives AFTER the `translation` event with the same id and is ALWAYS
 * followed by exactly one BINARY WebSocket frame containing `bytes` octets
 * of raw PCM16 little-endian mono audio at `sample_rate`. Only sent for
 * translate decisions — pass-through audio is never regenerated with TTS.
 * The wsClient pairs the metadata with the next binary frame and delivers
 * both via `onAudio` (never through `onEvent`).
 */
export interface AudioEvent {
  v: number
  type: 'audio'
  id: string
  language: string
  encoding: 'pcm_s16le'
  sample_rate: number
  channels: number
  bytes: number
  /** Duration of the synthesized audio (not the synthesis time). */
  duration_ms: number
  synthesis_ms: number
  provider: string
  /** Phase 5: who spoke the utterance this voice belongs to (room mode) —
   * audio is routed to the OTHER participant, so a present speaker marks the
   * clip as remote (feedback-loop playback guard). */
  speaker?: SpeakerRole
}

/**
 * Original-audio pass-through relay (Phase 5, ADR-0014).
 *
 * For a passthrough decision the ORIGINAL captured audio (VAD-trimmed, 16 kHz)
 * is relayed to the OTHER participant — never regenerated with TTS, never
 * replayed to the speaker. Same pairing contract as `audio`: the metadata is
 * ALWAYS followed by exactly one binary PCM16 frame.
 */
export interface RelayEvent {
  v: number
  type: 'relay'
  id: string
  speaker: SpeakerRole
  language: string
  encoding: 'pcm_s16le'
  sample_rate: number
  channels: number
  bytes: number
  duration_ms: number
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
  /** Phase 5: whose utterance the error belongs to, when delivered to a
   * room member OTHER than the speaker (shared translation/TTS outcomes). */
  speaker?: SpeakerRole
}

export interface PongEvent {
  v: number
  type: 'pong'
  ts: number
}

export type ServerEvent =
  | ReadyEvent
  | TranscriptionEvent
  | TranslationEvent
  | ErrorEvent
  | PongEvent
  | AudioEvent
  | PeerEvent
  | RelayEvent

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

function parseSpeaker(value: unknown, ctx: string): SpeakerRole | undefined {
  if (value === undefined || value === null) return undefined
  if (value !== 'host' && value !== 'guest') {
    throw new ProtocolError(`${ctx}: invalid speaker "${String(value)}"`)
  }
  return value
}

function parseRequiredSpeaker(value: unknown, ctx: string): SpeakerRole {
  const speaker = parseSpeaker(value, ctx)
  if (speaker === undefined) throw new ProtocolError(`${ctx}: "speaker" is required`)
  return speaker
}

function parseRoomInfo(value: unknown, ctx: string): RoomInfo | undefined {
  if (value === undefined || value === null) return undefined
  if (!isRecord(value)) throw new ProtocolError(`${ctx}: room must be an object`)
  const role = value['role']
  if (role !== 'host' && role !== 'guest') {
    throw new ProtocolError(`${ctx}: invalid room role`)
  }
  return {
    code: str(value, 'code', ctx),
    role,
    peers: num(value, 'peers', ctx),
    native_language: typeof value['native_language'] === 'string' ? value['native_language'] : '',
    preferred_language:
      typeof value['preferred_language'] === 'string' ? value['preferred_language'] : '',
  }
}

function parseDecision(value: unknown, ctx: string): TranslationDecision {
  if (!isRecord(value)) throw new ProtocolError(`${ctx}: decision must be an object`)
  const action = str(value, 'action', ctx)
  if (action !== 'translate' && action !== 'passthrough') {
    throw new ProtocolError(`${ctx}: invalid decision action "${action}"`)
  }
  const reason = str(value, 'reason', ctx)
  if (!TRANSLATION_REASONS.includes(reason as TranslationReason)) {
    throw new ProtocolError(`${ctx}: invalid decision reason "${reason}"`)
  }
  const detection = str(value, 'detection', ctx)
  if (!TRANSLATION_DETECTIONS.includes(detection as TranslationDetection)) {
    throw new ProtocolError(`${ctx}: invalid decision detection "${detection}"`)
  }
  const target = value['target_language']
  return {
    action,
    reason: reason as TranslationReason,
    source_language: str(value, 'source_language', ctx),
    target_language: typeof target === 'string' ? target : undefined,
    detection: detection as TranslationDetection,
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
        room: parseRoomInfo(data['room'], 'ready.room'),
      }
    }
    case 'peer': {
      const eventName = str(data, 'event', 'peer')
      if (eventName !== 'joined' && eventName !== 'left') {
        throw new ProtocolError(`peer: invalid event "${eventName}"`)
      }
      const role = data['role']
      if (role !== 'host' && role !== 'guest') {
        throw new ProtocolError('peer: invalid role')
      }
      return { v, type, event: eventName, role, peers: num(data, 'peers', 'peer') }
    }
    case 'transcription': {
      const rawDecision = data['decision']
      return {
        v,
        type,
        id: str(data, 'id', 'transcription'),
        speaker: parseSpeaker(data['speaker'], 'transcription'),
        text: str(data, 'text', 'transcription'),
        language: str(data, 'language', 'transcription'),
        language_probability: num(data, 'language_probability', 'transcription'),
        audio_duration_s: num(data, 'audio_duration_s', 'transcription'),
        processing_ms: num(data, 'processing_ms', 'transcription'),
        is_final: bool(data, 'is_final', 'transcription'),
        low_confidence: bool(data, 'low_confidence', 'transcription'),
        // null (translation disabled server-side) and absent both mean "no
        // decision" — tolerate both shapes.
        decision:
          rawDecision === undefined || rawDecision === null
            ? undefined
            : parseDecision(rawDecision, 'transcription.decision'),
      }
    }
    case 'translation':
      return {
        v,
        type,
        id: str(data, 'id', 'translation'),
        speaker: parseSpeaker(data['speaker'], 'translation'),
        text: str(data, 'text', 'translation'),
        source_language: str(data, 'source_language', 'translation'),
        target_language: str(data, 'target_language', 'translation'),
        provider: str(data, 'provider', 'translation'),
        duration_ms: num(data, 'duration_ms', 'translation'),
      }
    case 'audio': {
      const encoding = str(data, 'encoding', 'audio')
      if (encoding !== 'pcm_s16le') {
        throw new ProtocolError(`audio: unsupported encoding "${encoding}"`)
      }
      const channels = num(data, 'channels', 'audio')
      if (channels !== 1) throw new ProtocolError('audio: only mono is supported')
      return {
        v,
        type,
        id: str(data, 'id', 'audio'),
        language: str(data, 'language', 'audio'),
        encoding,
        sample_rate: num(data, 'sample_rate', 'audio'),
        channels,
        bytes: num(data, 'bytes', 'audio'),
        duration_ms: num(data, 'duration_ms', 'audio'),
        synthesis_ms: num(data, 'synthesis_ms', 'audio'),
        provider: str(data, 'provider', 'audio'),
        speaker: parseSpeaker(data['speaker'], 'audio'),
      }
    }
    case 'relay': {
      const encoding = str(data, 'encoding', 'relay')
      if (encoding !== 'pcm_s16le') {
        throw new ProtocolError(`relay: unsupported encoding "${encoding}"`)
      }
      const channels = num(data, 'channels', 'relay')
      if (channels !== 1) throw new ProtocolError('relay: only mono is supported')
      return {
        v,
        type,
        id: str(data, 'id', 'relay'),
        speaker: parseRequiredSpeaker(data['speaker'], 'relay'),
        language: str(data, 'language', 'relay'),
        encoding,
        sample_rate: num(data, 'sample_rate', 'relay'),
        channels,
        bytes: num(data, 'bytes', 'relay'),
        duration_ms: num(data, 'duration_ms', 'relay'),
      }
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
        speaker: parseSpeaker(data['speaker'], 'error'),
      }
    }
    case 'pong':
      return { v, type, ts: num(data, 'ts', 'pong') }
    default:
      throw new ProtocolError(`unknown server event type: ${type}`)
  }
}
