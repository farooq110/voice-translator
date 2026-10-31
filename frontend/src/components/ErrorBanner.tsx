import type { AppError } from '../hooks/useTranslatorSession'

export function ErrorBanner({ error, onDismiss }: { error: AppError | null; onDismiss: () => void }) {
  if (error === null) return null
  return (
    <div className="banner" role="alert">
      <div className="banner__body">
        <strong className="banner__code">{error.code.replace(/_/g, ' ')}</strong>
        <span>{error.message}</span>
      </div>
      <button type="button" className="banner__dismiss" onClick={onDismiss} aria-label="Dismiss error">
        ×
      </button>
    </div>
  )
}
