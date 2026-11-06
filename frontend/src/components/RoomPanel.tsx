import { useCallback, useState } from 'react'
import { getLanguage } from '../config/languages'
import type { RoomState } from '../hooks/useTranslatorSession'

interface RoomPanelProps {
  room: RoomState | null
  isGuest: boolean
  peerConnected: boolean
}

function languageLabel(code: string): string {
  return getLanguage(code)?.label ?? code.toUpperCase()
}

/**
 * The Phase 5 room panel (ADR-0014): the host shares the invite, the guest
 * sees what they joined. Neither side configures anything new here — only
 * the translator user (host) ever sets languages, in the panel above.
 */
export function RoomPanel({ room, isGuest, peerConnected }: RoomPanelProps) {
  const [copied, setCopied] = useState(false)

  const copyInvite = useCallback(() => {
    if (room === null) return
    const link = `${window.location.origin}${window.location.pathname}?room=${room.code}`
    void navigator.clipboard
      ?.writeText(link)
      .then(() => {
        setCopied(true)
        window.setTimeout(() => setCopied(false), 2000)
      })
      .catch(() => setCopied(false))
  }, [room])

  const peerLine = peerConnected
    ? 'Both participants connected — speak freely.'
    : 'Waiting for the other participant to join…'

  if (isGuest) {
    return (
      <section className="panel room-panel room-panel--guest" aria-label="Session">
        <h2 className="panel__title">Joined session</h2>
        <p className="room-panel__hint">
          You do not need to configure anything — the translator set the languages:
          {room !== null ? (
            <strong>
              {' '}
              {languageLabel(room.nativeLanguage)} ↔ {languageLabel(room.preferredLanguage)}
            </strong>
          ) : (
            ' …'
          )}
        </p>
        <p className={`room-panel__peer${peerConnected ? ' room-panel__peer--live' : ''}`} role="status">
          {peerLine}
        </p>
      </section>
    )
  }

  return (
    <section className="panel room-panel" aria-label="Invite the other participant">
      <h2 className="panel__title">Invite the other person</h2>
      <p className="room-panel__hint">
        Share this link — the other person opens it, allows the microphone and speaks. No
        install, no settings on their side.
      </p>
      <div className="room-panel__row">
        <code className="room-panel__code" aria-label="Invite code">
          {room?.code ?? '······'}
        </code>
        <button
          type="button"
          className="room-panel__copy"
          onClick={copyInvite}
          disabled={room === null}
        >
          {copied ? 'copied ✓' : 'copy invite link'}
        </button>
      </div>
      <p className={`room-panel__peer${peerConnected ? ' room-panel__peer--live' : ''}`} role="status">
        {peerLine}
      </p>
    </section>
  )
}
