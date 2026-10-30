import { useMemo } from 'react'
import { ConnectionBadge } from './components/ConnectionBadge'
import { ErrorBanner } from './components/ErrorBanner'
import { LanguageSelector } from './components/LanguageSelector'
import { LevelMeter } from './components/LevelMeter'
import { MicButton } from './components/MicButton'
import { TranscriptPanel } from './components/TranscriptPanel'
import { getLanguage } from './config/languages'
import { useTranslatorSession } from './hooks/useTranslatorSession'
import type { WsStatus } from './services/wsClient'

function resolveWsUrl(): string {
  const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${protocol}://${window.location.host}/ws/audio`
}

const STATUS_HINTS: Record<WsStatus, string> = {
  idle: 'Preparing…',
  connecting: 'Connecting to the server…',
  connected: 'Connected — tap the microphone to start speaking.',
  reconnecting: 'Connection lost — reconnecting automatically…',
  closed: 'Disconnected.',
  error: 'Connection problem.',
}

export default function App() {
  const wsUrl = useMemo(resolveWsUrl, [])
  const session = useTranslatorSession(wsUrl)

  const native = getLanguage(session.nativeLanguage)
  const preferred = getLanguage(session.preferredLanguage)
  const micDisabled = session.wsStatus !== 'connected'

  const statusLine = session.recording
    ? `Listening… ${session.elapsedMs >= 1000 ? `${Math.floor(session.elapsedMs / 1000)} s` : 'just started'}`
    : session.startingRecording
      ? 'Starting microphone…'
      : STATUS_HINTS[session.wsStatus]

  return (
    <div className="app">
      <header className="app__header">
        <div>
          <h1 className="app__title">AI Voice Translator</h1>
          <p className="app__subtitle">
            Speak your language — the other side hears theirs.
          </p>
        </div>
        <ConnectionBadge status={session.wsStatus} />
      </header>

      <main className="app__main">
        <ErrorBanner error={session.error} onDismiss={session.dismissError} />

        <section className="panel" aria-label="Language settings">
          <h2 className="panel__title">Your languages</h2>
          <p className="panel__hint">
            Only you configure these — the other person never needs to install or set up
            anything.
          </p>
          <div className="fields">
            <LanguageSelector
              id="native-language"
              label="Native language"
              hint={native ? `You speak ${native.label}; its text shows here as detected.` : undefined}
              value={session.nativeLanguage}
              onChange={session.setNativeLanguage}
            />
            <LanguageSelector
              id="preferred-language"
              label="Preferred translation language"
              hint={preferred ? `Your ${native?.label ?? ''} speech will be delivered as ${preferred.label}.` : undefined}
              value={session.preferredLanguage}
              onChange={session.setPreferredLanguage}
            />
          </div>
        </section>

        <section className="panel capture-panel" aria-label="Speech capture">
          <div className="capture">
            <MicButton
              recording={session.recording}
              starting={session.startingRecording}
              disabled={micDisabled}
              onToggle={session.toggleRecording}
            />
            <LevelMeter level={session.micLevel} active={session.recording} />
          </div>
          <p className={`capture__status${session.recording ? ' capture__status--live' : ''}`} role="status">
            {statusLine}
          </p>
        </section>

        <TranscriptPanel entries={session.transcripts} />

        {session.sttInfo !== null ? (
          <p className="app__engine">
            Engine: {session.sttInfo.provider} · {session.sttInfo.model} ({session.sttInfo.device})
            {session.sessionId !== null ? ` · session ${session.sessionId}` : ''}
          </p>
        ) : null}
      </main>

      <footer className="app__footer">
        <p>
          Phase 1 MVP — live transcription only. Translation into your preferred language and
          spoken output arrive in later phases. Works best in Chrome, Edge and Safari over
          HTTPS.
        </p>
      </footer>
    </div>
  )
}
