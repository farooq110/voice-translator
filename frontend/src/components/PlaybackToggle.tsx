interface PlaybackToggleProps {
  checked: boolean
  onChange: (checked: boolean) => void
}

/**
 * Spoken-output toggle (Phase 4, ADR-0013).
 *
 * - ON: every translated sentence is spoken aloud in its target language
 *   (offline Piper voices server-side; pass-through lines are never spoken).
 * - OFF: nothing is played and anything already queued is cancelled —
 *   translations remain visible as text only.
 */
export function PlaybackToggle({ checked, onChange }: PlaybackToggleProps) {
  return (
    <label className="handsfree playback-toggle">
      <input
        type="checkbox"
        role="switch"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span className="handsfree__text">
        {checked ? (
          <>
            <strong>Spoken translations</strong> — translated sentences are read aloud;
            original pass-through speech is never repeated.
          </>
        ) : (
          <>
            <strong>Spoken translations off</strong> — you will see translations as text
            only; nothing is played.
          </>
        )}
      </span>
    </label>
  )
}
