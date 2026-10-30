import type { WsStatus } from '../services/wsClient'

const LABELS: Record<WsStatus, { text: string; tone: 'ok' | 'pending' | 'bad' | 'muted' }> = {
  idle: { text: 'Idle', tone: 'muted' },
  connecting: { text: 'Connecting…', tone: 'pending' },
  connected: { text: 'Connected', tone: 'ok' },
  reconnecting: { text: 'Reconnecting…', tone: 'pending' },
  closed: { text: 'Disconnected', tone: 'bad' },
  error: { text: 'Connection error', tone: 'bad' },
}

export function ConnectionBadge({ status }: { status: WsStatus }) {
  const label = LABELS[status]
  return (
    <span className={`badge badge--${label.tone}`} role="status">
      <span className="badge__dot" aria-hidden="true" />
      {label.text}
    </span>
  )
}
