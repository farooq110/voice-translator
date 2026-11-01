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
import { FlushTracker } from '../services/flushTracker'
import { surfaceNoSpeechError } from '../services/noSpeechPolicy'
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
const LS_HANDS_FREE = 'vt.hands_free'

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
// Live segmentation caps (KI-009, ADR-0010). Without them one long stretch of
// continuous speech produced nothing until the 20 s cap — and the whole blob
// then needed ~6 s to decode. A segment is now also closed at the FIRST
// natural micro-pause after 4.5 s of audio, and no buffer grows past 9 s
// (worst-case decode ≈ 3 s at the measured ur RTF of 0.31 — results keep
// flowing while the user keeps talking).
const ENDPOINT_SOFT_CAP_MS = 4_500 // from here on, a micro-pause closes the segment
const ENDPOINT_MICRO_PAUSE_MS = 300 // micro-pause that may close a ≥ soft-cap segment
const ENDPOINT_MAX_SEGMENT_MS = 9_000 // hands-free hard cap — flush even without a pause
// Push-to-talk (hands-free OFF, BUG-002): NO auto-flush at pauses — the whole
// press becomes one utterance that is transcribed when the user taps stop.
// Only a generous safety cap bounds the buffer (below the server's 30 s cap,
// so the client always cuts cleanly with trailing silence, never the server
// mid-stream).
const PTT_MAX_SEGMENT_MS = 20_000
// Tail-capture window (KI-008): when stop is tapped the mic stays open this
// much longer so the final words are padded with REAL trailing silence —
// tapping stop the instant speech ends hard-cuts the last word against the
// buffer edge, which is the main cause of garbage transcripts.
const TAIL_CAPTURE_MS = 700

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

function loadStoredFlag(key: string, fallback: boolean): boolean {
  try {
    const value = window.localStorage.getItem(key)
    if (value === null) return fallback
    return value === 'true'
  } catch {
    return fallback
  }
}

function storeFlag(key: string, value: boolean): void {
  try {
    window.localStorage.setItem(key, value ? 'true' : 'false')
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
  /** True during the tail-capture window after tapping stop (KI-008). */
  finishing: boolean
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
  /** Hands-free mode (Phase 2): mic stays open, sentences flow at pauses. */
  handsFree: boolean
  setHandsFree: (on: boolean) => void
  /** Removes one sentence bubble from the transcript panel. */
  deleteTranscript: (id: string) => void
  toggleRecording: () => void
  dismissError: () => void
}

export function useTranslatorSession(wsUrl: string): SessionState {
  const [wsStatus, setWsStatus] = useState<WsStatus>('idle')
  const [sttInfo, setSttInfo] = useState<SttInfo | null>(null)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [recording, setRecording] = useState(false)
  const [startingRecording, setStartingRecording] = useState(false)
  const [finishing, setFinishing] = useState(false)
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
  const [handsFree, setHandsFreeState] = useState(() => loadStoredFlag(LS_HANDS_FREE, true))

  const clientRef = useRef<TranslatorWsClient | null>(null)
  const captureRef = useRef<CaptureHandle | null>(null)
  const recordingRef = useRef(false)
  const finishingRef = useRef(false)
  const endpointRef = useRef<EndpointState>(freshEndpointState())
  /** BUG-002: the endpointing callbacks are created once — read the mode
   * through a ref so toggling hands-free takes effect mid-session. */
  const handsFreeRef = useRef(handsFree)
  handsFreeRef.current = handsFree
  /** BUG-001: per-utterance attribution of in-flight decodes. Each flush is
   * registered with a client-generated id + whether it contained speech; the
   * server echoes the id on every transcription/error, so each outcome
   * settles exactly its own utterance (no shared-ref races). The
   * Transcribing… indicator is DERIVED from this map — a stuck state is
   * structurally impossible. */
  const inFlightRef = useRef<FlushTracker>(new FlushTracker())
  const flushSeqRef = useRef(0)
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

  /** Flush the current segment with a fresh correlation id (BUG-001). The
   * Transcribing… indicator turns on only for flushes that carried speech —
   * silence-only drains (hands-free idle) never show it. */
  const flushSegment = useCallback((hadSpeech: boolean) => {
    flushSeqRef.current += 1
    const id = `c_${String(flushSeqRef.current).padStart(4, '0')}_${Math.random()
      .toString(36)
      .slice(2, 8)}`
    inFlightRef.current.register(id, hadSpeech)
    clientRef.current?.flush(id)
    if (hadSpeech) setTranscribing(true)
  }, [])

  const finalizeRecording = useCallback(async () => {
    recordingRef.current = false
    finishingRef.current = false
    setRecording(false)
    setFinishing(false)
    setMicLevel(0)
    const handle = captureRef.current
    captureRef.current = null
    if (handle) await handle.stop()
    // Final flush only when audio accumulated since the last (auto-)flush —
    // otherwise the server would answer `nothing_to_transcribe` for a
    // pause the endpointing gate already closed. In push-to-talk mode this
    // IS the transcription trigger (BUG-002: flush on stop).
    if (endpointRef.current.bufferedMs > 0) {
      flushSegment(endpointRef.current.speechSeen)
    }
    endpointRef.current = freshEndpointState()
  }, [flushSegment])

  const stopRecording = useCallback(async () => {
    if (finishingRef.current) return // tail window already running — ignore re-taps
    finishingRef.current = true
    setFinishing(true)
    // Tail-capture window (KI-008): keep streaming ~0.7 s of real silence so
    // the last word is never hard-cut against the buffer edge. Capture keeps
    // running (chunks + endpointing stay live) until finalize stops it.
    await new Promise((resolve) => setTimeout(resolve, TAIL_CAPTURE_MS))
    await finalizeRecording()
  }, [finalizeRecording])

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
        // BUG-001: settle exactly the utterance this result belongs to —
        // unknown ids (server auto-flush) settle nothing.
        setTranscribing(inFlightRef.current.settle(event.id))
        return
      }
      if (event.type === 'error') {
        // BUG-001: attribute the error to ITS flush via the echoed
        // utterance_id — never to whichever flush happened to be last.
        const errorId = event.utterance_id
        const hadSpeech = errorId !== undefined ? inFlightRef.current.hadSpeech(errorId) : undefined
        // Outcomes for silence-only flushes (VAD gate rejections, drains,
        // backpressure drops) are expected — settle quietly, no banner.
        if (hadSpeech === false) {
          if (errorId !== undefined) setTranscribing(inFlightRef.current.settle(errorId))
          return
        }
        // A no_speech_detected is only noteworthy when its own flush carried
        // real speech the server did not recognize (see noSpeechPolicy.ts).
        if (event.code === 'no_speech_detected' && !surfaceNoSpeechError(hadSpeech === true)) {
          return
        }
        if (errorId !== undefined) setTranscribing(inFlightRef.current.settle(errorId))
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
  // stop the capture immediately (no tail window — the socket is gone).
  useEffect(() => {
    if (
      (wsStatus === 'reconnecting' || wsStatus === 'closed' || wsStatus === 'error') &&
      (recordingRef.current || transcribing)
    ) {
      if (recordingRef.current) void finalizeRecording()
      // In-flight results are dropped server-side for closed sessions
      // (ADR-0009) — forget them so the indicator cannot get stuck.
      inFlightRef.current.clear()
      setTranscribing(false)
      setError({
        code: 'connection_lost',
        message: 'Connection lost — recording stopped. Speak again once reconnected.',
      })
    }
  }, [wsStatus, finalizeRecording, transcribing])

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
          // BUG-002: in push-to-talk mode (hands-free OFF) pause-based
          // auto-flushes are SUPPRESSED — the whole press is transcribed on
          // stop; only a generous safety cap bounds the buffer.
          const handsFree = handsFreeRef.current
          const ep = endpointRef.current
          ep.bufferedMs += CHUNK_MS
          if (rms >= ENDPOINT_SPEECH_RMS) {
            ep.silentMs = 0
            ep.speechSeen = true
          } else {
            ep.silentMs += CHUNK_MS
          }
          const pauseClosedSentence =
            handsFree &&
            ep.speechSeen &&
            ep.bufferedMs >= ENDPOINT_MIN_SEGMENT_MS &&
            ep.silentMs >= ENDPOINT_SILENCE_MS
          // KI-009: past the soft cap the next natural micro-pause also
          // closes the segment — continuous speech no longer waits for a
          // full 800 ms sentence pause (or the old 20 s cap) to produce text.
          const softCapReached =
            handsFree &&
            ep.speechSeen &&
            ep.bufferedMs >= ENDPOINT_SOFT_CAP_MS &&
            ep.silentMs >= ENDPOINT_MICRO_PAUSE_MS
          const capReached =
            ep.bufferedMs >= (handsFree ? ENDPOINT_MAX_SEGMENT_MS : PTT_MAX_SEGMENT_MS)
          if (pauseClosedSentence || softCapReached || capReached) {
            // Silence-only drains (cap reached, nobody spoke — hands-free
            // idle) still flush to bound the server buffer, but must not
            // show the Transcribing… indicator nor a no_speech banner.
            flushSegment(ep.speechSeen)
            endpointRef.current = freshEndpointState()
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
  }, [flushSegment])

  const toggleRecording = useCallback(() => {
    if (recordingRef.current || finishingRef.current) void stopRecording()
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

  const setHandsFree = useCallback((on: boolean) => {
    setHandsFreeState(on)
    storeFlag(LS_HANDS_FREE, on)
  }, [])

  const dismissError = useCallback(() => setError(null), [])

  const deleteTranscript = useCallback((id: string) => {
    setTranscripts((prev) => prev.filter((entry) => entry.id !== id))
  }, [])

  return {
    wsStatus,
    sttInfo,
    sessionId,
    recording,
    startingRecording,
    finishing,
    transcribing,
    elapsedMs,
    micLevel,
    transcripts,
    error,
    nativeLanguage,
    preferredLanguage,
    setNativeLanguage,
    setPreferredLanguage,
    handsFree,
    setHandsFree,
    deleteTranscript,
    toggleRecording,
    dismissError,
  }
}
