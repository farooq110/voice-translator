import { describe, expect, it } from 'vitest'
import {
  PROTOCOL_VERSION,
  ProtocolError,
  flushMessage,
  helloMessage,
  parseServerEvent,
  pingMessage,
} from './protocol'

const READY = {
  v: 1,
  type: 'ready',
  session_id: 's_ab12cd34',
  audio: { encoding: 'pcm_s16le', sample_rate: 16000, channels: 1 },
  stt: { provider: 'faster-whisper', model: 'base', device: 'cpu', loaded: true },
}

const TRANSCRIPTION = {
  v: 1,
  type: 'transcription',
  id: 'u_0001_abc123',
  text: 'Hello there',
  language: 'en',
  language_probability: 0.98,
  audio_duration_s: 1.4,
  processing_ms: 320,
  is_final: true,
  low_confidence: false,
}

describe('client message builders', () => {
  it('builds a versioned hello', () => {
    const msg = helloMessage({
      native_language: 'ur',
      preferred_language: 'en',
      audio: { encoding: 'pcm_s16le', sample_rate: 16000, channels: 1 },
    })
    expect(msg).toEqual({
      v: 1,
      type: 'hello',
      config: {
        native_language: 'ur',
        preferred_language: 'en',
        audio: { encoding: 'pcm_s16le', sample_rate: 16000, channels: 1 },
      },
    })
  })

  it('builds ping with and without ts', () => {
    expect(pingMessage(5)).toEqual({ v: 1, type: 'ping', ts: 5 })
    expect(pingMessage()).toEqual({ v: 1, type: 'ping' })
  })

  it('builds flush', () => {
    expect(flushMessage()).toEqual({ v: PROTOCOL_VERSION, type: 'flush' })
  })

  it('builds flush with a correlation id (BUG-001)', () => {
    expect(flushMessage('c_0001_ab12cd')).toEqual({
      v: PROTOCOL_VERSION,
      type: 'flush',
      utterance_id: 'c_0001_ab12cd',
    })
  })
})

describe('parseServerEvent', () => {
  it('parses ready', () => {
    const event = parseServerEvent(JSON.stringify(READY))
    expect(event.type).toBe('ready')
    if (event.type === 'ready') {
      expect(event.session_id).toBe('s_ab12cd34')
      expect(event.stt.model).toBe('base')
      expect(event.audio.sample_rate).toBe(16000)
    }
  })

  it('parses transcription', () => {
    const event = parseServerEvent(JSON.stringify(TRANSCRIPTION))
    expect(event.type).toBe('transcription')
    if (event.type === 'transcription') {
      expect(event.text).toBe('Hello there')
      expect(event.low_confidence).toBe(false)
    }
  })

  it('parses error and pong', () => {
    const err = parseServerEvent(JSON.stringify({ v: 1, type: 'error', code: 'x', message: 'y', fatal: false }))
    expect(err).toMatchObject({ type: 'error', code: 'x', fatal: false })
    const pong = parseServerEvent(JSON.stringify({ v: 1, type: 'pong', ts: 7 }))
    expect(pong).toMatchObject({ type: 'pong', ts: 7 })
  })

  it('parses error utterance_id when present (BUG-001) and omits it when absent', () => {
    const attributed = parseServerEvent(
      JSON.stringify({ v: 1, type: 'error', code: 'server_busy', message: 'm', fatal: false, utterance_id: 'c_0002_feed01' }),
    )
    if (attributed.type !== 'error') throw new Error('expected error event')
    expect(attributed.utterance_id).toBe('c_0002_feed01')
    const plain = parseServerEvent(JSON.stringify({ v: 1, type: 'error', code: 'x', message: 'y', fatal: false }))
    if (plain.type !== 'error') throw new Error('expected error event')
    expect(plain.utterance_id).toBeUndefined()
    // a null id (server-generated) also parses as absent
    const nulled = parseServerEvent(
      JSON.stringify({ v: 1, type: 'error', code: 'x', message: 'y', fatal: false, utterance_id: null }),
    )
    if (nulled.type !== 'error') throw new Error('expected error event')
    expect(nulled.utterance_id).toBeUndefined()
  })

  it('rejects invalid JSON', () => {
    expect(() => parseServerEvent('not-json')).toThrow(ProtocolError)
  })

  it('rejects unknown types and wrong versions', () => {
    expect(() => parseServerEvent(JSON.stringify({ v: 1, type: 'boom' }))).toThrow(ProtocolError)
    expect(() => parseServerEvent(JSON.stringify({ ...TRANSCRIPTION, v: 2 }))).toThrow(ProtocolError)
  })

  it('rejects missing/incorrectly typed fields', () => {
    const missing = { ...TRANSCRIPTION }
    delete (missing as Record<string, unknown>)['text']
    expect(() => parseServerEvent(JSON.stringify(missing))).toThrow(ProtocolError)
    expect(() =>
      parseServerEvent(JSON.stringify({ ...READY, audio: { ...READY.audio, sample_rate: '16000' } })),
    ).toThrow(ProtocolError)
  })

  it('rejects unsupported audio encodings', () => {
    expect(() =>
      parseServerEvent(
        JSON.stringify({ ...READY, audio: { ...READY.audio, encoding: 'mp3' } }),
      ),
    ).toThrow(ProtocolError)
  })
})
