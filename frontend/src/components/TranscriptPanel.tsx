import { useEffect, useRef } from 'react'
import { isRtlLanguage } from '../config/languages'
import type { TranscriptEntry } from '../hooks/useTranslatorSession'

interface TranscriptPanelProps {
  entries: TranscriptEntry[]
  /** Removes one sentence bubble (the × button next to each line). */
  onDelete: (id: string) => void
}

export function TranscriptPanel({ entries, onDelete }: TranscriptPanelProps) {
  const endRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [entries.length])

  return (
    <section className="panel transcript-panel" aria-label="Transcript">
      <h2 className="panel__title">Transcript</h2>
      {entries.length === 0 ? (
        <p className="transcript-empty">
          Press the microphone and speak — sentences appear here live as you pause. Tap ✕ next
          to a line to remove it.
        </p>
      ) : (
        <ul className="transcript-list">
          {entries.map((entry) => (
            <li key={entry.id} className="transcript-item">
              <div className="transcript-meta">
                <span className="chip">{entry.language.toUpperCase()}</span>
                <span className="transcript-meta__muted">
                  {Math.round(entry.languageProbability * 100)}% · {entry.processingMs} ms ·{' '}
                  {entry.audioDurationS.toFixed(1)} s
                </span>
                {entry.lowConfidence ? <span className="chip chip--warn">uncertain</span> : null}
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
            </li>
          ))}
        </ul>
      )}
      <div ref={endRef} />
    </section>
  )
}
