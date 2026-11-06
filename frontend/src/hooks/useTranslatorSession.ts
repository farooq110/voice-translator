/**
 * useTranslatorSession — all app state in one hook (Phase 1 → Phase 5).
 *
 * Owns: WS client lifecycle, microphone capture, language preferences
 * (persisted to localStorage), transcripts, errors, mic level, elapsed time,
 * and — Phase 5 (ADR-0014) — the two-participant room: invite code, peer
 * presence, speaker-attributed transcripts and the remote-playback guard
 * that prevents the device microphone from re-capturing the other
 * participant's audio (feedback-loop prevention).
 *
 * Business logic stays out of the UI components (master prompt: separate
 * business logic from UI); pure helpers live in services/ and are unit-tested.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { DEFAULT_NATIVE_LANGUAGE, DEFAULT_PREFERRED_LANGUAGE } from '../config/languages'
import { ROOMS_ENABLED } from '../config/flags'
import {
  MicCaptureError,
  TARGET_SAMPLE_RATE,
  startMicCapture,
  type CaptureHandle,
} from '../services/audioCapture'
import { TranslatorWsClient, type WsStatus } from '../services/wsClient'
import { FlushTracker } from '../services/flushTracker'
import { SpeechPlayer, type PlaybackState } from '../services/playback'
import { PlaybackGuard } from '../services/playbackGuard'
import { surfaceNoSpeechError } from '../services/noSpeechPolicy'
import { detectPlatform } from '../services/platform'
import { WakeLock } from '../services/wakeLock'
import {
  applyTranslationError,
  applyTranslationEvent,
  applyVoiceError,
  isTranslationErrorCode,
  isVoiceErrorCode,
  type TranslationCarrier,
  type TranslationErrorInfo,
  type TranslationInfo,
} from '../services/translations'
import type {
  HelloConfig,
  ServerEvent,
  SpeakerRole,
  SttInfo,
  TranslationDecision,
} from '../types/protocol'

export interface TranscriptEntry extends TranslationCarrier {
  id: string
  /** Phase 5: who spoke this line (absent in solo mode / Phase 1–4 shape). */
  speaker?: SpeakerRole
  /** Stable key: utterance ids are unique per CONNECTION, so playback and
   * deletion address lines by (speaker, id) — "host:c_0001_ab12cd". */
  key: string
  text: string
  language: string
  languageProbability: number
  processingMs: number
  audioDurationS: number
  lowConfidence: boolean
  at: number
  /** Phase 3 (ADR-0012): why this line will (not) be translated. */
  decision?: TranslationDecision
  translation?: TranslationInfo
  translationError?: TranslationErrorInfo
}

export interface AppError {
  code: string
  message: string
}

/** Phase 5: the room as this client sees it (from `ready` + `peer` events). */
export interface RoomState {
  code: string
  role: SpeakerRole
  nativeLanguage: string
  preferredLanguage: string
}

const MAX_TRANSCRIPTS = 50
const LS_NATIVE = 'vt.native_language'
const LS_PREFERRED = 'vt.preferred_language'
const LS_HANDS_FREE = 'vt.hands_free'
const LS_PLAYBACK = 'vt.playback'

/** Fatal room errors stop the reconnect loop: re-helloing with a dead or
 * expired room can never succeed — the user needs a fresh invite instead.
 * `seat_taken` (QA round BUG-002): this seat was reclaimed by a newer
 * connection of the same participant (the invite link was reopened after
 * this tab became a ghost) — re-helloing cannot win either.
 * `room_full` is deliberately NOT in this list: during a RECONNECT it means
 * the old seat's ghost has not been evicted yet, so the client retries
 * while the server reclaims the seat (12 s stale-seat threshold); a FIRST
 * join that answers room_full is a genuine third participant and fails
 * immediately (see handleEvent). */
const ROOM_FATAL_CODES: readonly string[] = ['room_not_found', 'room_required', 'seat_taken']
/** QA round (BUG-002): how long a reconnecting client keeps retrying after
 * room_full before giving up and asking for a fresh invite. The server
 * evicts a stale seat after 12 s of silence; 90 s covers many backoff
 * retry rounds plus a flaky-link margin. */
const REJOIN_GRACE_MS = 90_000

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

/** Phase 6 (KI-004): coarse platform tag sent once in the hello — lets the
 * server's /health show which device families actually connect. */
const CLIENT_PLATFORM = detectPlatform()

/** Invite code from the URL (`?room=CODE`) — a guest opens the shared link. */
function readInviteCode(): string | null {
  try {
    if (typeof window === 'undefined') return null
    const code = new URLSearchParams(window.location.search).get('room')
    return code !== null && /^[A-Za-z0-9]{4,10}$/.test(code) ? code.toUpperCase() : null
  } catch {
    return null
  }
}

function lineKey(speaker: SpeakerRole | undefined, id: string): string {
  return speaker === undefined ? id : `${speaker}:${id}`
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
  /** Phase 4: spoken playback of translated sentences (mute toggle). */
  playbackEnabled: boolean
  setPlaybackEnabled: (on: boolean) => void
  /** Id of the utterance whose translation is currently audible (or null). */
  speakingId: string | null
  /** Clips scheduled behind the speaking one. */
  queuedAudioCount: number
  /** Stops the current + queued audio (keeps future utterances audible). */
  stopPlayback: () => void
  /** Removes one sentence bubble from the transcript panel (by entry key). */
  deleteTranscript: (key: string) => void
  toggleRecording: () => void
  dismissError: () => void
  // --- Phase 5 (ADR-0014): two-participant room ---
  /** This connection's room role (null until the server confirms). */
  role: SpeakerRole | null
  /** True when the URL carried a ?room= invite (guest candidate). */
  isGuest: boolean
  /** The room as confirmed by the server (code + the host's language pair). */
  room: RoomState | null
  /** Whether the OTHER participant is currently connected. */
  peerConnected: boolean
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
  const [playbackEnabled, setPlaybackEnabledState] = useState(() =>
    loadStoredFlag(LS_PLAYBACK, true),
  )
  const [playback, setPlayback] = useState<PlaybackState>({ activeId: null, waitingCount: 0 })
  // --- Phase 5 room state ---
  const [role, setRole] = useState<SpeakerRole | null>(null)
  const [room, setRoom] = useState<RoomState | null>(null)
  const [peerConnected, setPeerConnected] = useState(false)
  const isGuest = ROOMS_ENABLED && readInviteCode() !== null

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
   * settles exactly this utterance (no shared-ref races). The Transcribing…
   * indicator is DERIVED from this map — a stuck state is structurally
   * impossible. Peer utterances (other speaker) never touch this tracker. */
  const inFlightRef = useRef<FlushTracker>(new FlushTracker())
  const flushSeqRef = useRef(0)
  /** Phase 4: TTS playback queue — created once, injected AudioContext only
   * after a user gesture (autoplay policy). Muting cancels scheduled audio. */
  const playerRef = useRef<SpeechPlayer | null>(null)
  if (playerRef.current === null) {
    playerRef.current = new SpeechPlayer(undefined, playbackEnabled, setPlayback)
  }
  /** Phase 5: feedback-loop guard — muted uplink while REMOTE audio plays. */
  const guardRef = useRef<PlaybackGuard>(new PlaybackGuard())
  /** Phase 5: this connection's role, readable inside stable callbacks. */
  const roleRef = useRef<SpeakerRole | null>(null)
  /** Phase 5: the host's current room code — re-sent on reconnect so the
   * server re-attaches this client to ITS room (linger window). */
  const lastRoomCodeRef = useRef<string | null>(null)
  /** QA round (BUG-002): true once this session has ever been `ready` —
   * distinguishes a RECONNECT's room_full (retry) from a first join's
   * room_full (a genuine third participant — fatal immediately). */
  const wasReadyRef = useRef(false)
  /** QA round (BUG-002): epoch-ms deadline of the room_full rejoin retry
   * window (null while not retrying). */
  const rejoinDeadlineRef = useRef<number | null>(null)
  const playbackEnabledRef = useRef(playbackEnabled)
  playbackEnabledRef.current = playbackEnabled
  /** Phase 6: keeps the screen awake while the mic is open (a locked phone
   * suspends capture within seconds). Best-effort — unsupported browsers
   * and denials degrade silently. */
  const wakeLockRef = useRef<WakeLock | null>(null)
  if (wakeLockRef.current === null) {
    wakeLockRef.current = WakeLock.fromNavigator(navigator)
  }

  const configRef = useRef<HelloConfig>(
    ROOMS_ENABLED && isGuest
      ? { role: 'guest', room: readInviteCode() ?? undefined, client: { platform: CLIENT_PLATFORM }, audio: AUDIO_FORMAT }
      : { native_language: nativeLanguage, preferred_language: preferredLanguage, client: { platform: CLIENT_PLATFORM }, audio: AUDIO_FORMAT },
  )
  configRef.current =
    ROOMS_ENABLED && isGuest
      ? { role: 'guest', room: readInviteCode() ?? undefined, client: { platform: CLIENT_PLATFORM }, audio: AUDIO_FORMAT }
      : {
          ...(ROOMS_ENABLED ? { role: 'host' as const } : {}),
          native_language: nativeLanguage,
          preferred_language: preferredLanguage,
          ...(ROOMS_ENABLED && lastRoomCodeRef.current !== null
            ? { room: lastRoomCodeRef.current }
            : {}),
          client: { platform: CLIENT_PLATFORM },
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
    void wakeLockRef.current?.release()
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

  const handleEvent = useCallback((event: ServerEvent) => {
    if (event.type === 'ready') {
      setSttInfo(event.stt)
      setSessionId(event.session_id)
      if (event.room) {
        roleRef.current = event.room.role
        setRole(event.room.role)
        setRoom({
          code: event.room.code,
          role: event.room.role,
          nativeLanguage: event.room.native_language,
          preferredLanguage: event.room.preferred_language,
        })
        setPeerConnected(event.room.peers > 0)
        if (event.room.role === 'host') {
          // Reconnect safety: re-hello re-attaches to THIS room.
          lastRoomCodeRef.current = event.room.code
        }
      }
      return
    }
    if (event.type === 'peer') {
      setPeerConnected(event.event === 'joined')
      return
    }
    if (event.type === 'transcription') {
      const mine = event.speaker === undefined || event.speaker === roleRef.current
      const entry: TranscriptEntry = {
        id: event.id,
        speaker: event.speaker,
        key: lineKey(event.speaker, event.id),
        text: event.text,
        language: event.language,
        languageProbability: event.language_probability,
        processingMs: event.processing_ms,
        audioDurationS: event.audio_duration_s,
        lowConfidence: event.low_confidence,
        at: Date.now(),
        decision: event.decision,
      }
      setTranscripts((prev) => [...prev.slice(-(MAX_TRANSCRIPTS - 1)), entry])
      if (!mine) return // peer utterance — never touches our in-flight state
      // BUG-001: settle exactly the utterance this result belongs to —
      // unknown ids (server auto-flush) settle nothing.
      setTranscribing(inFlightRef.current.settle(event.id))
      return
    }
    if (event.type === 'translation') {
      // Phase 3 (ADR-0012): attach the translated text to ITS utterance —
      // matched by id + speaker (ids collide across participants).
      setTranscripts((prev) => applyTranslationEvent(prev, event))
      return
    }
    if (event.type === 'error') {
      // QA round (BUG-002): room_full during a RECONNECT means this seat's
      // ghost has not been evicted yet — keep the wsClient reconnect
      // backoff running (each retry re-hellos; the server reclaims a seat
      // that has been silent >12 s) and give up after REJOIN_GRACE_MS.
      if (event.code === 'room_full') {
        if (!wasReadyRef.current) {
          // First join: the room genuinely has two participants.
          clientRef.current?.close()
          setError({ code: event.code, message: event.message })
          return
        }
        if (rejoinDeadlineRef.current === null) {
          rejoinDeadlineRef.current = Date.now() + REJOIN_GRACE_MS
        }
        if (Date.now() > rejoinDeadlineRef.current) {
          rejoinDeadlineRef.current = null
          clientRef.current?.close()
          setError({
            code: 'room_full',
            message: 'Could not rejoin the room — please open a fresh invite link.',
          })
          return
        }
        setError({
          code: 'room_full',
          message: 'Your seat is still being freed — reconnecting…',
        })
        return
      }
      // Phase 5: an unusable room can never succeed by retrying — stop the
      // reconnect loop and surface the reason (the user needs a new invite).
      if (ROOM_FATAL_CODES.includes(event.code)) {
        clientRef.current?.close()
        setError({ code: event.code, message: event.message })
        return
      }
      // Utterance-scoped translation AND voice failures never touch the
      // global banner nor the indicator — they are attached to the line,
      // original/translated text kept (Phase 3 + Phase 4 + Phase 5).
      if (isTranslationErrorCode(event.code)) {
        const id = event.utterance_id
        if (id !== undefined) {
          setTranscripts((prev) => applyTranslationError(prev, id, event.code, event.speaker))
        }
        return
      }
      if (isVoiceErrorCode(event.code)) {
        const id = event.utterance_id
        if (id !== undefined) {
          setTranscripts((prev) => applyVoiceError(prev, id, event.code, event.speaker))
        }
        return
      }
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
  }, [])

  // WS client lifecycle (connect on mount, clean close on unmount).
  useEffect(() => {
    const client = new TranslatorWsClient({
      url: wsUrl,
      config: () => configRef.current,
      handlers: {
        onStatus: (status) => {
          setWsStatus(status)
          // QA round (BUG-002): a successful (re)join clears the retry
          // bookkeeping — and marks that this session was ready at least
          // once (first-join room_full stays fatal).
          if (status === 'connected') {
            wasReadyRef.current = true
            if (rejoinDeadlineRef.current !== null) {
              rejoinDeadlineRef.current = null
              setError(null)
            }
          }
        },
        onEvent: handleEvent,
        onProtocolError: (protocolError) =>
          setError({ code: 'protocol_error', message: protocolError.message }),
        // Phase 4: synthesized speech arrives as metadata + binary pair.
        onAudio: (meta, pcm) => {
          const remote = meta.speaker !== undefined && meta.speaker !== roleRef.current
          const key = lineKey(meta.speaker, meta.id)
          const accepted = playerRef.current?.enqueue(
            { id: key, language: meta.language, sample_rate: meta.sample_rate },
            pcm,
          )
          // Feedback-loop guard: mute OUR uplink while the OTHER side's voice
          // is audible (ADR-0014, master prompt scenario 12). Own TTS (solo
          // playback) never arms the guard.
          if (remote && accepted === true && playbackEnabledRef.current) {
            guardRef.current.onRemoteClip(meta.duration_ms)
          }
        },
        // Phase 5: the peer's ORIGINAL third-language audio, relayed as
        // captured — always remote, always guards the uplink.
        onRelay: (meta, pcm) => {
          const key = lineKey(meta.speaker, meta.id)
          const accepted = playerRef.current?.enqueue(
            { id: key, language: meta.language, sample_rate: meta.sample_rate },
            pcm,
          )
          if (accepted === true && playbackEnabledRef.current) {
            guardRef.current.onRemoteClip(meta.duration_ms)
          }
        },
      },
    })
    clientRef.current = client
    client.connect()
    const guard = guardRef.current // stable for the effect's lifetime
    return () => {
      client.close()
      clientRef.current = null
      playerRef.current?.stopAll()
      guard.clear()
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
      // Phase 4: cancel outdated audio — nothing queued for a dead session
      // may keep playing once the connection is gone.
      playerRef.current?.stopAll()
      guardRef.current.clear()
      setError({
        code: 'connection_lost',
        message: 'Connection lost — recording stopped. Speak again once reconnected.',
      })
    }
  }, [wsStatus, finalizeRecording, transcribing])

  // Phase 6 (KI-004/KI-015): mobile OSes throttle or suspend microphone
  // capture and WS timers the moment the tab is backgrounded — keeping the
  // "recording" state alive would stream silence and trip the server's
  // stale-seat/idle logic. A hidden tab therefore finalizes the recording
  // the same honest way a connection loss does; returning re-arms playback
  // in case the autoplay policy suspended it while hidden.
  useEffect(() => {
    const onVisibilityChange = () => {
      if (document.visibilityState === 'hidden') {
        if (!recordingRef.current) return
        void finalizeRecording()
        inFlightRef.current.clear()
        setTranscribing(false)
        playerRef.current?.stopAll()
        guardRef.current.clear()
        setError({
          code: 'capture_paused_background',
          message:
            'Recording stopped — the browser suspended the tab in the background. Tap the mic to continue.',
        })
      } else {
        playerRef.current?.resumeIfCreated()
      }
    }
    const onPageHide = () => {
      // Navigation away / app switch: flush what we have, quietly — banners
      // are pointless when the page is already going away.
      if (recordingRef.current) void finalizeRecording()
    }
    document.addEventListener('visibilitychange', onVisibilityChange)
    window.addEventListener('pagehide', onPageHide)
    return () => {
      document.removeEventListener('visibilitychange', onVisibilityChange)
      window.removeEventListener('pagehide', onPageHide)
    }
  }, [finalizeRecording])

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
    // Unlock audio output with this user gesture (browser autoplay policy).
    playerRef.current?.resume()
    try {
      const handle = await startMicCapture({
        onStateLost: () => {
          // iOS/Android suspended the capture graph mid-recording (phone
          // call, Siri, OS audio routing). Finalize honestly — the mic may
          // come back dead even when the context resumes.
          if (!recordingRef.current) return
          void finalizeRecording()
          setError({
            code: 'capture_interrupted',
            message:
              'Recording stopped — the phone interrupted the microphone. Tap the mic to continue.',
          })
        },
        onChunk: (chunk) => {
          // Feedback-loop guard (ADR-0014): while the other participant's
          // audio is audible on THIS device, do not feed the loop — drop the
          // uplink chunk (the endpointing state below stays consistent
          // because onLevel skips accumulation for the same window).
          if (guardRef.current.isGuarded()) return
          clientRef.current?.sendAudioChunk(chunk)
        },
        onLevel: (rms) => {
          setMicLevel(rms)
          if (guardRef.current.isGuarded()) return
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
      void wakeLockRef.current?.acquire() // best-effort; no lock on denial
    } catch (err) {
      const micError = err instanceof MicCaptureError ? err : null
      setError({
        code: micError?.code ?? 'start_failed',
        message: micError?.message ?? 'Could not start the microphone.',
      })
    } finally {
      setStartingRecording(false)
    }
  }, [flushSegment, finalizeRecording])

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

  const setPlaybackEnabled = useCallback((on: boolean) => {
    setPlaybackEnabledState(on)
    playerRef.current?.setEnabled(on) // muting cancels scheduled audio
    if (!on) guardRef.current.clear() // nothing will play — unmute the uplink
    storeFlag(LS_PLAYBACK, on)
  }, [])

  const stopPlayback = useCallback(() => {
    playerRef.current?.stopAll()
    guardRef.current.clear() // user stopped the audio — uplink is safe again
  }, [])

  const dismissError = useCallback(() => setError(null), [])

  const deleteTranscript = useCallback((key: string) => {
    setTranscripts((prev) => prev.filter((entry) => entry.key !== key))
    // Cancellation of outdated audio: a deleted line stops being spoken.
    playerRef.current?.cancel(key)
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
    playbackEnabled,
    setPlaybackEnabled,
    speakingId: playback.activeId,
    queuedAudioCount: playback.waitingCount,
    stopPlayback,
    deleteTranscript,
    toggleRecording,
    dismissError,
    role,
    isGuest,
    room,
    peerConnected,
  }
}
