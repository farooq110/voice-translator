interface HandsFreeToggleProps {
  checked: boolean
  onChange: (checked: boolean) => void
}

/**
 * Capture-mode toggle (Phase 2, behind the VITE_FEATURE_HANDS_FREE flag).
 *
 * BUG-002 fix: the two modes now behave differently AND say so —
 * - ON (hands-free): the mic stays open; sentences transcribe automatically
 *   at every pause (ADR-0007/0010 endpointing).
 * - OFF (push-to-talk): no auto-flush while recording — everything you say
 *   in one press is transcribed when you tap stop (ADR-0011).
 */
export function HandsFreeToggle({ checked, onChange }: HandsFreeToggleProps) {
  return (
    <label className="handsfree">
      <input
        type="checkbox"
        role="switch"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span className="handsfree__text">
        {checked ? (
          <>
            <strong>Hands-free</strong> — the mic stays open and every sentence transcribes
            itself at the pauses. Tap the mic again to stop.
          </>
        ) : (
          <>
            <strong>Push-to-talk</strong> — tap the mic, speak, then tap again to transcribe
            everything you said in one go.
          </>
        )}
      </span>
    </label>
  )
}
