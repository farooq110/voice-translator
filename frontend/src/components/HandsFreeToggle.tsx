interface HandsFreeToggleProps {
  checked: boolean
  onChange: (checked: boolean) => void
}

/**
 * Hands-free mode toggle (Phase 2 — continuous capture behind a feature
 * flag). Rendered only when the build enables the flag
 * (VITE_FEATURE_HANDS_FREE, see src/config/flags.ts).
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
        <strong>Hands-free</strong> — keep the microphone open and let sentences transcribe
        themselves at every pause. Tap the mic again to stop.
      </span>
    </label>
  )
}
