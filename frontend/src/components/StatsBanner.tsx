import type { StatsSummary } from '../types'
import { formatHours } from '../types'

type Props = {
  stats: StatsSummary | null
  loading?: boolean
}

function ProgressBar({ value, goal, label }: { value: number; goal: number; label: string }) {
  const ratio = goal > 0 ? Math.min(value / goal, 1.2) : 0
  const pct = Math.round(ratio * 100)
  const tone = value >= goal ? 'done' : value >= goal * 0.75 ? 'warn' : 'low'

  return (
    <div className="progress-block">
      <div className="progress-head">
        <span>{label}</span>
        <strong>
          {formatHours(value)} / {formatHours(goal)}
        </strong>
      </div>
      <div className="progress-track">
        <div className={`progress-fill ${tone}`} style={{ width: `${Math.min(pct, 100)}%` }} />
      </div>
    </div>
  )
}

function StatsSkeleton() {
  return (
    <section className="stats-banner panel stats-banner-loading">
      <div className="stats-grid">
        {[0, 1].map((key) => (
          <div key={key} className="progress-block skeleton-block">
            <div className="skeleton-line short" />
            <div className="skeleton-line" />
          </div>
        ))}
      </div>
    </section>
  )
}

export default function StatsBanner({ stats, loading = false }: Props) {
  if (loading && !stats) return <StatsSkeleton />
  if (!stats) return null

  const todayLeft = Math.max(stats.todayGoal - stats.todayHours, 0)
  const weekLeft = Math.max(stats.weekGoal - stats.weekHours, 0)

  return (
    <section className="stats-banner panel">
      <div className="stats-grid">
        <ProgressBar value={stats.todayHours} goal={stats.todayGoal} label="Сегодня" />
        <ProgressBar value={stats.weekHours} goal={stats.weekGoal} label="Текущая неделя" />
      </div>
      <div className="stats-hints">
        {todayLeft > 0 ? (
          <span className="hint-chip">До нормы сегодня: {formatHours(todayLeft)}</span>
        ) : (
          <span className="hint-chip done">Норма на сегодня закрыта</span>
        )}
        {weekLeft > 0 ? (
          <span className="hint-chip muted">До нормы за неделю: {formatHours(weekLeft)}</span>
        ) : (
          <span className="hint-chip done">Норма недели выполнена</span>
        )}
      </div>
    </section>
  )
}
