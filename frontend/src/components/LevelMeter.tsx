export function LevelMeter({ level, active }: { level: number; active: boolean }) {
  const pct = active ? Math.min(100, Math.round(Math.sqrt(level) * 260)) : 0
  return (
    <div className="level" aria-hidden="true">
      <div className="level__bar" style={{ transform: `scaleX(${pct / 100})` }} />
    </div>
  )
}
