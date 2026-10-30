import { LANGUAGES } from '../config/languages'

interface LanguageSelectorProps {
  id: string
  label: string
  hint?: string
  value: string
  disabled?: boolean
  onChange: (code: string) => void
}

export function LanguageSelector({
  id,
  label,
  hint,
  value,
  disabled,
  onChange,
}: LanguageSelectorProps) {
  return (
    <div className="field">
      <label className="field__label" htmlFor={id}>
        {label}
      </label>
      <select
        id={id}
        className="field__select"
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
      >
        {LANGUAGES.map((lang) => (
          <option key={lang.code} value={lang.code}>
            {lang.label} · {lang.native}
          </option>
        ))}
      </select>
      {hint ? <p className="field__hint">{hint}</p> : null}
    </div>
  )
}
