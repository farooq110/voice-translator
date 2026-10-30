/**
 * useTranslatorSession — all app state for Phase 1 in one hook.
 *
 * Owns: WS client lifecycle, microphone capture, language preferences
 * (persisted to localStorage), transcripts, errors, mic level, elapsed time.
 * Business logic stays out of the UI components (master prompt: separate
 * business logic from UI).
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { DEFAULT_NATIVE_LANGUAGE, DEFAULT_PREFERRED_LANGUAGE } from '../config/languages'
import {
  MicCaptureError,
  TARGET_SAMPLE_RATE,
  startMicCapture,
  type CaptureHandle,
} from '../services/audioCapture'
import { TranslatorWsClient, type WsStatus } from '../services/wsClient'
import type { HelloConfig, ServerEvent, SttInfo } from '../types/protocol'

export interface TranscriptEntry {
  id: string
  text: string
  language: string
  languageProbability: number
  processingMs: number
  audioDurationS: number
  lowConfidence: boolean
  at: number
}

export interface AppError {
  code: string
  message: string
}

const MAX_TRANSCRIPTS = 50
const LS_NATIVE = 'vt.native_language'
const LS_PREFERRED = 'vt.preferred_language'

// --- Sentence endpointing (client-side energy gate, ADR-0007) ---------------
//
// While the mic is open, a pause is detected from the per-chunk RMS and the
// server buffer is flushed — so every sentence is transcribed as a complete,
// pause-bounded unit while the user keeps speaking. This is what makes the
// STT "understand sentences": Whisper receives clean sentence-sized audio
// instead of one long blob that mixes sentences, breaths and silence (or is
// cut mid-word by the 30 s cap). It also makes results appear live.
const CHUNK_MS = 100 // worklet chunk cadence — onLevel fires once per chunk
const ENDPOINT_SPEECH_RMS = 0.01 // RMS ≥ this counts as speech (mic has AGC)
const ENDPOINT_SILENCE_MS = 800 // pause length that closes a sentence
const ENDPOINT_MIN_SEGMENT_MS = 1_200 // never auto-flush less audio than this
const ENDPOINT_MAX_SEGMENT_MS = 20_000 // hard cap — flush even without a pause

interface EndpointState {
  /** Audio buffered (client-side) since the last flush. */
  bufferedMs: number
  /** Consecutive silence at the end of the current segment. */
  silentMs: number
  /** Whether actual speech was seen in the current segment. */
  speechSeen: boolean
}

function freshEndpointState(): EndpointState {
  return { bufferedMs: 0, silentMs: 0, speechSeen: false }
}

const AUDIO_FORMAT: HelloConfig['audio'] = {
  encoding: 'pcm_s16le',
  sample_rate: TARGET_SAMPLE_RATE,
  channels: 1,
}

function loadStoredLanguage(key: string, fallback: string): string {
  try {
    const value = window.localStorage.getItem(key)
    return typeof value === 'string' && value.length > 0 ? value : fallback
  } catch {
    return fallback // private mode / storage disabled
  }
}

function storeLanguage(key: string, code: string): void {
  try {
    window.localStorage.setItem(key, code)
  } catch {
    // ignore — preference simply won't persist
  }
}

export interface SessionState {
  wsStatus: WsStatus
  sttInfo: SttInfo | null
  sessionId: string | null
  recording: boolean
  startingRecording: boolean
  /** True between a flush and the arrival of its transcription (or error). */
  transcribing: boolean
  elapsedMs: number
  micLevel: number
  transcripts: TranscriptEntry[]
  error: AppError | null
  nativeLanguage: string
  preferredLanguage: string
  setNativeLanguage: (code: string) => void
  setPreferredLanguage: (code: string) => void
  toggleRecording: () => void
  dismissError: () => void
}

export function useTranslatorSession(wsUrl: string): SessionState {
  const [wsStatus, setWsStatus] = useState<WsStatus>('idle')
  const [sttInfo, setSttInfo] = useState<SttInfo | null>(null)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [recording, setRecording] = useState(false)
  const [startingRecording, setStartingRecording] = useState(false)
  const [transcribing, setTranscribing] = useState(false)
  const [elapsedMs, setElapsedMs] = useState(0)
  const [micLevel, setMicLevel] = useState(0)
  const [transcripts, setTranscripts] = useState<TranscriptEntry[]>([])
  const [error, setError] = useState<AppError | null>(null)
  const [nativeLanguage, setNativeLanguageState] = useState(() =>
    loadStoredLanguage(LS_NATIVE, DEFAULT_NATIVE_LANGUAGE),
  )
  const [preferredLanguage, setPreferredLanguageState] = useState(() =>
    loadStoredLanguage(LS_PREFERRED, DEFAULT_PREFERRED_LANGUAGE),
  )

  const clientRef = useRef<TranslatorWsClient | null>(null)
  const captureRef = useRef<CaptureHandle | null>(null)
  const recordingRef = useRef(false)
  const endpointRef = useRef<EndpointState>(freshEndpointState())
  const configRef = useRef<HelloConfig>({
    native_language: nativeLanguage,
    preferred_language: preferredLanguage,
    audio: AUDIO_FORMAT,
  })
  configRef.current = {
    native_language: nativeLanguage,
    preferred_language: preferredLanguage,
    audio: AUDIO_FORMAT,
  }

  const stopRecording = useCallback(async () => {
    recordingRef.current = false
    setRecording(false)
    setMicLevel(0)
    const handle = captureRef.current
    captureRef.current = null
    if (handle) await handle.stop()
    // Final flush only when audio accumulated since the last (auto-)flush —
    // otherwise the server would answer `nothing_to_transcribe` for a
    // pause the endpointing gate already closed.
    if (endpointRef.current.bufferedMs > 0) {
      clientRef.current?.flush()
      // The utterance is now on the server — show progress until the result
      // (or an error) arrives instead of a silent dead wait.
      setTranscribing(true)
    }
    endpointRef.current = freshEndpointState()
  }, [])

  const handleEvent = useCallback(
    (event: ServerEvent) => {
      if (event.type === 'ready') {
        setSttInfo(event.stt)
        setSessionId(event.session_id)
        return
      }
      if (event.type === 'transcription') {
        const entry: TranscriptEntry = {
          id: event.id,
          text: event.text,
          language: event.language,
          languageProbability: event.language_probability,
          processingMs: event.processing_ms,
          audioDurationS: event.audio_duration_s,
          lowConfidence: event.low_confidence,
          at: Date.now(),
        }
        setTranscripts((prev) => [...prev.slice(-(MAX_TRANSCRIPTS - 1)), entry])
        setTranscribing(false)
        return
      }
      if (event.type === 'error') {
        // Includes no_speech_detected / stt_timeout — never leave the
        // "Transcribing…" indicator stuck on screen.
        setTranscribing(false)
        setError({ code: event.code, message: event.message })
      }
    },
    [],
  )

  // WS client lifecycle (connect on mount, clean close on unmount).
  useEffect(() => {
    const client = new TranslatorWsClient({
      url: wsUrl,
      config: () => configRef.current,
      handlers: {
        onStatus: (status) => setWsStatus(status),
        onEvent: handleEvent,
        onProtocolError: (protocolError) =>
          setError({ code: 'protocol_error', message: protocolError.message }),
      },
    })
    clientRef.current = client
    client.connect()
    return () => {
      client.close()
      clientRef.current = null
    }
  }, [wsUrl, handleEvent])

  // If the connection drops while recording or awaiting a transcription,
  // stop the capture immediately.
  useEffect(() => {
    if (
      (wsStatus === 'reconnecting' || wsStatus === 'closed' || wsStatus === 'error') &&
      (recordingRef.current || transcribing)
    ) {
      if (recordingRef.current) void stopRecording()
      setTranscribing(false)
      setError({
        code: 'connection_lost',
        message: 'Connection lost — recording stopped. Speak again once reconnected.',
      })
    }
  }, [wsStatus, stopRecording, transcribing])

  // Elapsed timer while recording.
  useEffect(() => {
    if (!recording) return
    const startedAt = Date.now()
    const timer = window.setInterval(() => setElapsedMs(Date.now() - startedAt), 200)
    return () => window.clearInterval(timer)
  }, [recording])

  const startRecording = useCallback(async () => {
    const client = clientRef.current
    if (client === null || !client.isReady()) {
      setError({
        code: 'not_connected',
        message: 'Not connected yet — the microphone unlocks once the connection is ready.',
      })
      return
    }
    setStartingRecording(true)
    endpointRef.current = freshEndpointState()
    try {
      const handle = await startMicCapture({
        onChunk: (chunk) => {
          clientRef.current?.sendAudioChunk(chunk)
        },
        onLevel: (rms) => {
          setMicLevel(rms)
          // Sentence endpointing: track speech/pause from the chunk RMS and
          // flush the server buffer at every natural pause (ADR-0007).
          const ep = endpointRef.current
          ep.bufferedMs += CHUNK_MS
          if (rms >= ENDPOINT_SPEECH_RMS) {
            ep.silentMs = 0
            ep.speechSeen = true
          } else {
            ep.silentMs += CHUNK_MS
          }
          const pauseClosedSentence =
            ep.speechSeen &&
            ep.bufferedMs >= ENDPOINT_MIN_SEGMENT_MS &&
            ep.silentMs >= ENDPOINT_SILENCE_MS
          const capReached = ep.bufferedMs >= ENDPOINT_MAX_SEGMENT_MS
          if (pauseClosedSentence || capReached) {
            clientRef.current?.flush()
            endpointRef.current = freshEndpointState()
            setTranscribing(true)
          }
        },
      })
      captureRef.current = handle
      recordingRef.current = true
      setRecording(true)
      setElapsedMs(0)
      setTranscribing(false)
      setError(null)
    } catch (err) {
      const micError = err instanceof MicCaptureError ? err : null
      setError({
        code: micError?.code ?? 'start_failed',
        message: micError?.message ?? 'Could not start the microphone.',
      })
    } finally {
      setStartingRecording(false)
    }
  }, [])

  const toggleRecording = useCallback(() => {
    if (recordingRef.current) void stopRecording()
    else void startRecording()
  }, [startRecording, stopRecording])

  const setNativeLanguage = useCallback((code: string) => {
    setNativeLanguageState(code)
    storeLanguage(LS_NATIVE, code)
  }, [])

  const setPreferredLanguage = useCallback((code: string) => {
    setPreferredLanguageState(code)
    storeLanguage(LS_PREFERRED, code)
  }, [])

  const dismissError = useCallback(() => setError(null), [])

  return {
    wsStatus,
    sttInfo,
    sessionId,
    recording,
    startingRecording,
    transcribing,
    elapsedMs,
    micLevel,
    transcripts,
    error,
    nativeLanguage,
    preferredLanguage,
    setNativeLanguage,
    setPreferredLanguage,
    toggleRecording,
    dismissError,
  }
}
