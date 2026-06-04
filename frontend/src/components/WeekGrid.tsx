import { Fragment } from 'react'
import type { Timesheet, WorkItem } from '../types'
import {
  dayCellStatus,
  dayStatusClass,
  eachDay,
  formatHours,
  kindLabel,
  tfsEditUrl,
  toIsoDate,
} from '../types'
import WorkItemBadge from './WorkItemBadge'

type Props = {
  timesheet: Timesheet
  periodStart: Date
  periodEnd: Date
  onAddTime: (item: WorkItem, date: string, role?: string, activity?: string) => void
}

function TaskIdLink({ id, tfsUrl }: { id: number; tfsUrl: string }) {
  const href = tfsEditUrl(tfsUrl, id)
  if (!href) {
    return <strong className="task-id-link">#{id}</strong>
  }
  return (
    <a className="task-id-link" href={href} target="_blank" rel="noreferrer">
      #{id}
    </a>
  )
}

function GroupSection({
  title,
  groups,
  days,
  dayTotals,
  onAddTime,
  scrollable = false,
}: {
  title: string
  groups: Timesheet['groups']
  days: Date[]
  dayTotals: Map<string, number>
  onAddTime: (item: WorkItem, date: string, role?: string, activity?: string) => void
  /** Внутренний скролл тела таблицы (для длинного списка активных задач). */
  scrollable?: boolean
}) {
  if (groups.length === 0) return null

  return (
    <section
      className={['timesheet-section', scrollable ? 'timesheet-section--scrollable' : '']
        .filter(Boolean)
        .join(' ')}
    >
      <header className="section-head">
        <h3>{title}</h3>
      </header>
      <div className="grid-scroll">
        <table className="timesheet-grid">
          <thead>
            <tr>
              <th className="sticky-col">Задача</th>
              {days.map((day) => {
                const key = toIsoDate(day)
                const status = dayCellStatus(day, dayTotals.get(key) ?? 0)
                return (
                  <th key={day.toISOString()} className={dayStatusClass(status)}>
                    <span>{day.toLocaleDateString('ru-RU', { weekday: 'short' })}</span>
                    <strong>{day.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' })}</strong>
                  </th>
                )
              })}
              <th>Итого</th>
            </tr>
          </thead>
          <tbody>
            {groups.map((group) => (
              <Fragment key={`group-${group.parent.id}`}>
                <tr key={`parent-${group.parent.id}`} className="parent-row">
                  <td className="sticky-col">
                    <div className="item-title">
                      <TaskIdLink id={group.parent.id} tfsUrl={group.parent.tfsUrl} />
                      <span className="item-title-text">{group.parent.title}</span>
                    </div>
                    <div className="item-meta">
                      <WorkItemBadge kind={group.parent.kind} label={kindLabel(group.parent.kind)} />
                      <WorkItemBadge kind="task" label={group.parent.state} muted />
                      <span className="path">{group.parent.areaPath}</span>
                    </div>
                  </td>
                  {days.map((day) => {
                    const key = toIsoDate(day)
                    const hours = group.trackingRows.reduce(
                      (sum, row) => sum + (row.dailyHours[key] ?? 0),
                      0,
                    )
                    const status = dayCellStatus(day, dayTotals.get(key) ?? 0)
                    return (
                      <td key={key} className={dayStatusClass(status)}>
                        <button
                          type="button"
                          className={['cell-btn', hours ? 'filled' : '', dayStatusClass(status)]
                            .filter(Boolean)
                            .join(' ')}
                          onClick={() => onAddTime(group.parent, key)}
                        >
                          {hours ? formatHours(hours) : '+'}
                        </button>
                      </td>
                    )
                  })}
                  <td className="total-cell">{formatHours(group.totalHours)}</td>
                </tr>
                {group.trackingRows.map((row) => (
                  <tr
                    key={`${group.parent.id}-${row.trackingWorkItemId ?? row.title}`}
                    className="child-row"
                  >
                    <td className="sticky-col indent">
                      <div className="child-task">
                        {row.trackingWorkItemId ? (
                          <TaskIdLink
                            id={row.trackingWorkItemId}
                            tfsUrl={row.tfsUrl || group.parent.tfsUrl}
                          />
                        ) : null}
                        <span className="child-label">{row.title}</span>
                      </div>
                    </td>
                    {days.map((day) => {
                      const key = toIsoDate(day)
                      const hours = row.dailyHours[key] ?? 0
                      const status = dayCellStatus(day, dayTotals.get(key) ?? 0)
                      return (
                        <td key={key} className={dayStatusClass(status)}>
                          <div className={`cell-split${hours ? ' has-hours' : ''}`}>
                            <span
                              className={[
                                'cell-hours',
                                hours ? 'hours-positive' : 'hours-zero',
                                dayStatusClass(status),
                              ]
                                .filter(Boolean)
                                .join(' ')}
                            >
                              {hours ? formatHours(hours) : '0:00'}
                            </span>
                            <button
                              type="button"
                              className={['cell-add-btn', dayStatusClass(status)].filter(Boolean).join(' ')}
                              aria-label="Досписать время"
                              onClick={() => onAddTime(group.parent, key, row.role, row.activity)}
                            >
                              +
                            </button>
                          </div>
                        </td>
                      )
                    })}
                    <td className="total-cell">{formatHours(row.totalHours)}</td>
                  </tr>
                ))}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

export default function WeekGrid({ timesheet, periodStart, periodEnd, onAddTime }: Props) {
  const days = eachDay(periodStart, periodEnd)
  const dayTotals = new Map(timesheet.dayTotals.map((item) => [item.date, item.hours]))

  return (
    <div className="timesheet-stack">
      <div className="summary-bar panel">
        <div className="summary-head">
          <h3>
            Списания за неделю — <span className="accent">{formatHours(timesheet.totalHours)} ч</span>
          </h3>
        </div>
        <div className="day-totals-scroll">
          <div className="day-totals">
            {days.map((day) => {
              const key = toIsoDate(day)
              const hours = dayTotals.get(key) ?? 0
              const isToday = key === toIsoDate(new Date())
              const status = dayCellStatus(day, hours)
              return (
                <div
                  key={key}
                  className={['day-total', isToday ? 'today' : '', dayStatusClass(status)]
                    .filter(Boolean)
                    .join(' ')}
                >
                  <span>{day.toLocaleDateString('ru-RU', { weekday: 'short', day: '2-digit', month: '2-digit' })}</span>
                  <strong>{formatHours(hours)}</strong>
                </div>
              )
            })}
          </div>
        </div>
      </div>

      <GroupSection
        title="Активные задачи"
        groups={timesheet.groups}
        days={days}
        dayTotals={dayTotals}
        onAddTime={onAddTime}
        scrollable
      />
      <GroupSection
        title="Закрытые задачи"
        groups={timesheet.closedGroups}
        days={days}
        dayTotals={dayTotals}
        onAddTime={onAddTime}
      />

      {timesheet.groups.length === 0 && timesheet.closedGroups.length === 0 && (
        <div className="panel empty-panel">
          <p>Найдите требование или задачу через поиск и внесите первое списание.</p>
        </div>
      )}
    </div>
  )
}
