interface MicButtonProps {
  recording: boolean
  starting: boolean
  /** A transcription is in flight — show progress in the button. */
  busy: boolean
  /** Tail-capture window after tapping stop (KI-008) — briefly untouchable. */
  finishing: boolean
  disabled: boolean
  onToggle: () => void
}

export function MicButton({ recording, starting, busy, finishing, disabled, onToggle }: MicButtonProps) {
  const pending = (busy && !recording) || starting || finishing
  return (
    <button
      type="button"
      className={`mic-button${recording ? ' mic-button--recording' : ''}${pending ? ' mic-button--busy' : ''}`}
      onClick={onToggle}
      disabled={disabled || starting || finishing}
      aria-pressed={recording}
      aria-label={recording || finishing ? 'Stop speaking' : 'Start speaking'}
    >
      {pending ? (
        <span className="mic-button__spinner" aria-hidden="true" />
      ) : recording ? (
        <svg viewBox="0 0 24 24" width="30" height="30" aria-hidden="true" fill="currentColor">
          <rect x="6.5" y="6.5" width="11" height="11" rx="2" />
        </svg>
      ) : (
        <svg viewBox="0 0 24 24" width="34" height="34" aria-hidden="true" fill="currentColor">
          <path d="M12 4a3 3 0 0 1 3 3v5a3 3 0 0 1-6 0V7a3 3 0 0 1 3-3Z" />
          <path d="M6.5 11.5a.9.9 0 0 1 1.8 0 3.7 3.7 0 0 0 7.4 0 .9.9 0 0 1 1.8 0 5.5 5.5 0 0 1-4.6 5.42V19h1.9a.9.9 0 0 1 0 1.8h-5.6a.9.9 0 0 1 0-1.8h1.9v-2.08a5.5 5.5 0 0 1-4.6-5.42Z" />
        </svg>
      )}
    </button>
  )
}
