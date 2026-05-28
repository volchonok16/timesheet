import type { Calendar } from '../types'
import { formatHours, quarterForMonth, quarterLabel } from '../types'
import MonthCalendar from './MonthCalendar'

type Props = {
  calendar: Calendar | null
  scope: 'month' | 'quarter' | 'year'
  onPickDay: (isoDate: string) => void
}

const SCOPE_LABELS: Record<Props['scope'], string> = {
  month: 'месяц',
  quarter: 'квартал',
  year: 'год',
}

export default function CalendarPanel({ calendar, scope, onPickDay }: Props) {
  if (!calendar) {
    return <div className="panel loading-panel">Загружаем календарь…</div>
  }

  const title =
    scope === 'year'
      ? `${calendar.year} год`
      : scope === 'quarter'
        ? quarterLabel(calendar.quarter ?? quarterForMonth(calendar.month ?? 1), calendar.year)
        : new Date(calendar.year, (calendar.month ?? 1) - 1, 1).toLocaleDateString('ru-RU', {
            month: 'long',
            year: 'numeric',
          })

  return (
    <section className={`calendar-panel panel scope-${scope}`}>
      <header className="calendar-head">
        <div>
          <p className="eyebrow">Календарь · {SCOPE_LABELS[scope]}</p>
          <h3>{title}</h3>
        </div>
        <div className="calendar-total">
          <span className="muted small">Списано</span>
          <strong className="accent">{formatHours(calendar.totalHours)} ч</strong>
        </div>
      </header>

      {scope === 'month' && calendar.months[0] ? (
        <MonthCalendar data={calendar.months[0]} onPickDay={onPickDay} />
      ) : (
        <div className={`calendar-multi scope-${scope}`}>
          {calendar.months.map((month) => (
            <MonthCalendar
              key={`${month.year}-${month.month}`}
              data={month}
              compact={scope === 'quarter'}
              mini={scope === 'year'}
              onPickDay={onPickDay}
            />
          ))}
        </div>
      )}
    </section>
  )
}
