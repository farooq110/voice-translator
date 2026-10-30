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
    clientRef.current?.flush()
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
        return
      }
      if (event.type === 'error') {
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

  // If the connection drops while recording, stop the capture immediately.
  useEffect(() => {
    if (
      (wsStatus === 'reconnecting' || wsStatus === 'closed' || wsStatus === 'error') &&
      recordingRef.current
    ) {
      void stopRecording()
      setError({
        code: 'connection_lost',
        message: 'Connection lost — recording stopped. Speak again once reconnected.',
      })
    }
  }, [wsStatus, stopRecording])

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
    try {
      const handle = await startMicCapture({
        onChunk: (chunk) => {
          clientRef.current?.sendAudioChunk(chunk)
        },
        onLevel: (rms) => setMicLevel(rms),
      })
      captureRef.current = handle
      recordingRef.current = true
      setRecording(true)
      setElapsedMs(0)
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
