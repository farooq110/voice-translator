import { useEffect, useRef } from 'react'
import { isRtlLanguage } from '../config/languages'
import type { TranscriptEntry } from '../hooks/useTranslatorSession'

export function TranscriptPanel({ entries }: { entries: TranscriptEntry[] }) {
  const endRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [entries.length])

  return (
    <section className="panel transcript-panel" aria-label="Transcript">
      <h2 className="panel__title">Transcript</h2>
      {entries.length === 0 ? (
        <p className="transcript-empty">
          Press the microphone, speak, then press it again — your words appear here with
          the detected language.
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
