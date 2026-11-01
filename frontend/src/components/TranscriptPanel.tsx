import { useMemo } from 'react'
import { isRtlLanguage } from '../config/languages'
import { TRANSLATION_ENABLED } from '../config/flags'
import { decisionBadgeText, translationErrorText } from '../services/translations'
import type { TranscriptEntry } from '../hooks/useTranslatorSession'

interface TranscriptPanelProps {
  entries: TranscriptEntry[]
  /** Removes one sentence bubble (the × button next to each line). */
  onDelete: (id: string) => void
}

/**
 * Transcript sentences are stored oldest-first, but shown NEWEST-FIRST
 * (KI-009 user request): the latest result is always the first line, so a
 * growing conversation never pushes fresh output out of view and nothing has
 * to be scrolled to be read.
 *
 * Phase 3 (ADR-0012): each line also shows its translation decision — a
 * direction badge (`UR → EN`) with the translated text underneath, or an
 * honest pass-through chip (`third language`, `language uncertain`, …).
 * Pass-through lines never show a translation (the server never produces
 * one), and a failed translation keeps the original text with a warning.
 */
export function TranscriptPanel({ entries, onDelete }: TranscriptPanelProps) {
  const newestFirst = useMemo(() => [...entries].reverse(), [entries])

  return (
    <section className="panel transcript-panel" aria-label="Transcript">
      <h2 className="panel__title">Transcript</h2>
      {entries.length === 0 ? (
        <p className="transcript-empty">
          Press the microphone and speak — sentences appear here live as you pause, newest
          first. Tap ✕ next to a line to remove it.
        </p>
      ) : (
        <ul className="transcript-list">
          {newestFirst.map((entry) => (
            <li key={entry.id} className="transcript-item">
              <div className="transcript-meta">
                <span className="chip">{entry.language.toUpperCase()}</span>
                <span className="transcript-meta__muted">
                  {Math.round(entry.languageProbability * 100)}% · {entry.processingMs} ms ·{' '}
                  {entry.audioDurationS.toFixed(1)} s
                </span>
                {entry.lowConfidence ? <span className="chip chip--warn">uncertain</span> : null}
                {TRANSLATION_ENABLED && entry.decision ? (
                  <span
                    className={`chip${entry.decision.action === 'translate' ? '' : ' chip--muted'}`}
                    title={
                      entry.decision.action === 'translate'
                        ? 'Translated for the other side / for you'
                        : 'Not translated by rule — the original is kept'
                    }
                  >
                    {decisionBadgeText(entry.decision)}
                  </span>
                ) : null}
                {TRANSLATION_ENABLED && entry.translationError ? (
                  <span className="chip chip--warn">
                    {translationErrorText(entry.translationError.code)}
                  </span>
                ) : null}
                <span className="transcript-meta__spacer" />
                <button
                  type="button"
                  className="transcript-delete"
                  aria-label="Delete this sentence"
                  title="Delete this sentence"
                  onClick={() => onDelete(entry.id)}
                >
                  <svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true">
                    <path
                      d="M6 6l12 12M18 6L6 18"
                      stroke="currentColor"
                      strokeWidth="2.6"
                      strokeLinecap="round"
                      fill="none"
                    />
                  </svg>
                </button>
              </div>
              <p className={`transcript-text${isRtlLanguage(entry.language) ? ' transcript-text--rtl' : ''}`}>
                {entry.text}
              </p>
              {TRANSLATION_ENABLED && entry.translation ? (
                <div className="transcript-translation">
                  <p
                    className={`transcript-translation__text${
                      isRtlLanguage(entry.translation.targetLanguage)
                        ? ' transcript-translation__text--rtl'
                        : ''
                    }`}
                    lang={entry.translation.targetLanguage}
                  >
                    {entry.translation.text}
                  </p>
                  <span className="transcript-translation__meta">
                    via {entry.translation.provider} · {entry.translation.durationMs} ms
                  </span>
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
