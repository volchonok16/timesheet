import type { MonthCalendar as MonthCalendarData } from '../types'
import { dayCellStatus, dayStatusClass, formatHours, toIsoDate } from '../types'

type Props = {
  data: MonthCalendarData
  compact?: boolean
  mini?: boolean
  onPickDay: (isoDate: string) => void
}

const WEEKDAYS = ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс']

const MONTH_NAMES = [
  'Январь',
  'Февраль',
  'Март',
  'Апрель',
  'Май',
  'Июнь',
  'Июль',
  'Август',
  'Сентябрь',
  'Октябрь',
  'Ноябрь',
  'Декабрь',
]

export default function MonthCalendar({ data, compact = false, mini = false, onPickDay }: Props) {
  const firstDay = new Date(data.year, data.month - 1, 1)
  const offset = (firstDay.getDay() + 6) % 7
  const daysInMonth = new Date(data.year, data.month, 0).getDate()
  const hoursByDay = new Map(data.days.map((day) => [day.date, day]))
  const today = toIsoDate(new Date())

  const cells: Array<{ label: string; iso?: string; hours?: number; count?: number }> = []
  for (let i = 0; i < offset; i += 1) cells.push({ label: '' })
  for (let day = 1; day <= daysInMonth; day += 1) {
    const key = String(day).padStart(2, '0')
    const iso = `${data.year}-${String(data.month).padStart(2, '0')}-${key}`
    const payload = hoursByDay.get(iso)
    cells.push({
      label: String(day),
      iso,
      hours: payload?.hours ?? 0,
      count: payload?.entriesCount ?? 0,
    })
  }

  const rootClass = ['month-calendar', compact ? 'compact' : '', mini ? 'mini' : ''].filter(Boolean).join(' ')

  return (
    <article className={rootClass}>
      <header className="month-calendar-head">
        <h4>
          {MONTH_NAMES[data.month - 1]}
          {!mini && <span className="muted"> {data.year}</span>}
        </h4>
        {!mini && (
          <span className={`month-total${data.totalHours ? ' accent' : ''}`}>
            {formatHours(data.totalHours)} ч
          </span>
        )}
      </header>

      <div className={`calendar-grid${mini ? ' mini' : ''}${compact ? ' compact' : ''}`}>
        {!mini &&
          WEEKDAYS.map((label, index) => (
            <div key={label} className={`calendar-weekday${index >= 5 ? ' weekend' : ''}`}>
              {label}
            </div>
          ))}
        {cells.map((cell, index) => {
          if (!cell.iso) {
            return <div key={`empty-${index}`} className="calendar-day empty" />
          }

          const date = new Date(data.year, data.month - 1, Number(cell.label))
          const hours = cell.hours ?? 0
          const status = dayCellStatus(date, hours)

          return (
            <button
              key={cell.iso}
              type="button"
              className={['calendar-day', dayStatusClass(status), cell.iso === today ? 'today' : '']
                .filter(Boolean)
                .join(' ')}
              onClick={() => onPickDay(cell.iso!)}
              title={cell.iso}
            >
              <span className="day-num">{cell.label}</span>
              {hours > 0 ? <strong>{formatHours(hours)}</strong> : null}
              {!mini && cell.count ? <em>{cell.count} зап.</em> : null}
            </button>
          )
        })}
      </div>
    </article>
  )
}
