import { useEffect, useState } from 'react'
import { getJson } from '../api'
import type { RecentEntry, WorkItem } from '../types'
import { formatHours, kindLabel } from '../types'
import WorkItemBadge from './WorkItemBadge'

type Props = {
  refreshKey: number
  onRepeat: (item: WorkItem, date: string) => void
}

export default function RecentEntries({ refreshKey, onRepeat }: Props) {
  const [entries, setEntries] = useState<RecentEntry[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    void getJson<RecentEntry[]>('/api/time-entries/recent?limit=6')
      .then(setEntries)
      .catch(() => setEntries([]))
      .finally(() => setLoading(false))
  }, [refreshKey])

  if (loading) return null
  if (entries.length === 0) return null

  const repeat = async (entry: RecentEntry) => {
    try {
      const item = await getJson<WorkItem>(`/api/work-items/${entry.parentWorkItemId}`)
      onRepeat(item, entry.entryDate)
    } catch {
      /* ignore */
    }
  }

  return (
    <section className="recent-panel panel">
      <header className="section-head">
        <h3>Последние списания</h3>
        <span className="muted small">Повторить одним кликом</span>
      </header>
      <ul className="recent-list">
        {entries.map((entry) => (
          <li key={entry.id}>
            <button type="button" className="recent-item" onClick={() => void repeat(entry)}>
              <div className="recent-main">
                <strong>{formatHours(entry.hours)}</strong>
                <span>
                  {entry.parentWorkItemId} · {entry.role} — {entry.activity}
                </span>
              </div>
              <div className="recent-meta">
                <WorkItemBadge
                  kind={entry.parentKind as WorkItem['kind']}
                  label={kindLabel(entry.parentKind as WorkItem['kind'])}
                />
                <span className="muted">{entry.entryDate}</span>
              </div>
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}
