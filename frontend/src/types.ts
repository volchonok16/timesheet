export type WorkItemKind = 'change_request' | 'requirement' | 'error' | 'task' | 'other'

export type WorkItem = {
  id: number
  title: string
  workItemType: string
  state: string
  areaPath: string
  kind: WorkItemKind
  tfsUrl: string
  completedWork?: number
}

export type Role = { id: string; label: string }
export type Activity = { id: string; label: string; roleId: string; commentTemplate: string }

export type TrackingRow = {
  trackingWorkItemId: number | null
  role: string
  activity: string
  title: string
  tfsUrl?: string
  dailyHours: Record<string, number>
  totalHours: number
}

export type TimesheetGroup = {
  parent: WorkItem
  children: WorkItem[]
  trackingRows: TrackingRow[]
  totalHours: number
}

export type TimesheetSyncResult = {
  imported?: number
  skipped?: number
  tasksScanned?: number
  cached?: boolean
  message?: string
  tsapiErrors?: string[] | null
}

export type Timesheet = {
  periodStart: string
  periodEnd: string
  view: 'week' | 'month'
  dayTotals: { date: string; hours: number }[]
  totalHours: number
  closedDayTotals?: { date: string; hours: number }[]
  closedTotalHours?: number
  groups: TimesheetGroup[]
  closedGroups: TimesheetGroup[]
}

export type CalendarDay = {
  date: string
  hours: number
  entriesCount: number
}

export type MonthCalendar = {
  year: number
  month: number
  days: CalendarDay[]
  totalHours: number
}

export type CalendarScope = 'month' | 'quarter' | 'year'

export type Calendar = {
  scope: CalendarScope
  month?: number | null
  year: number
  quarter?: number | null
  days: CalendarDay[]
  months: MonthCalendar[]
  totalHours: number
}

export type StatsSummary = {
  todayHours: number
  todayGoal: number
  weekHours: number
  weekGoal: number
  weekStart: string
  weekEnd: string
}

export type RecentEntry = {
  id: number
  parentWorkItemId: number
  parentTitle: string
  parentKind: WorkItemKind | string
  role: string
  activity: string
  entryDate: string
  hours: number
  costProject?: string | null
  createdAt: string
}

export type CostProjectOptions = {
  fieldName: string
  options: string[]
  defaultValue: string | null
  parentValue: string | null
}

export type TimeEntryPayload = {
  parentWorkItemId: number
  role: string
  activity: string
  entryDate: string
  hours: number
  minutes: number
  subtract: boolean
  comment?: string | null
  costProject?: string | null
}

export function kindLabel(kind: WorkItemKind): string {
  if (kind === 'change_request') return 'ЗНИ'
  if (kind === 'requirement') return 'Требование'
  if (kind === 'error') return 'Ошибка'
  if (kind === 'task') return 'Задача'
  return 'Элемент'
}

export function formatHours(value: number): string {
  const sign = value < 0 ? '-' : ''
  const abs = Math.abs(value)
  const hours = Math.floor(abs)
  const minutes = Math.round((abs - hours) * 60)
  if (minutes === 0) return `${sign}${hours}:00`
  return `${sign}${hours}:${String(minutes).padStart(2, '0')}`
}

export function parseIsoDate(value: string): Date {
  const [y, m, d] = value.split('-').map(Number)
  return new Date(y, m - 1, d)
}

export function toIsoDate(value: Date): string {
  const y = value.getFullYear()
  const m = String(value.getMonth() + 1).padStart(2, '0')
  const d = String(value.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

export function weekStart(value: Date): Date {
  const copy = new Date(value)
  const day = (copy.getDay() + 6) % 7
  copy.setDate(copy.getDate() - day)
  copy.setHours(0, 0, 0, 0)
  return copy
}

export function addDays(value: Date, days: number): Date {
  const copy = new Date(value)
  copy.setDate(copy.getDate() + days)
  return copy
}

export function eachDay(start: Date, end: Date): Date[] {
  const days: Date[] = []
  const cursor = new Date(start)
  while (cursor <= end) {
    days.push(new Date(cursor))
    cursor.setDate(cursor.getDate() + 1)
  }
  return days
}

export type ViewMode = 'week' | 'month' | 'quarter' | 'year'

export type PeriodAnchor = { year: number; month: number }

export function quarterForMonth(month: number): number {
  return Math.floor((month - 1) / 3) + 1
}

export function quarterLabel(quarter: number, year: number): string {
  const labels = ['I', 'II', 'III', 'IV']
  return `${labels[quarter - 1]} кв. ${year}`
}

export function shiftAnchor(anchor: PeriodAnchor, view: ViewMode, delta: number): PeriodAnchor {
  if (view === 'year') {
    return { ...anchor, year: anchor.year + delta }
  }
  const step = view === 'quarter' ? 3 : 1
  const next = new Date(anchor.year, anchor.month - 1 + delta * step, 1)
  return { year: next.getFullYear(), month: next.getMonth() + 1 }
}

export function periodLabel(view: ViewMode, periodStart: Date, anchor: PeriodAnchor): string {
  if (view === 'week') {
    const end = addDays(periodStart, 6)
    const sameMonth = periodStart.getMonth() === end.getMonth()
    const startPart = periodStart.toLocaleDateString('ru-RU', {
      day: 'numeric',
      month: sameMonth ? undefined : 'short',
    })
    const endPart = end.toLocaleDateString('ru-RU', {
      day: 'numeric',
      month: 'short',
      year: 'numeric',
    })
    return `${startPart} – ${endPart}`
  }
  if (view === 'month') {
    return new Date(anchor.year, anchor.month - 1, 1).toLocaleDateString('ru-RU', {
      month: 'long',
      year: 'numeric',
    })
  }
  if (view === 'quarter') {
    return quarterLabel(quarterForMonth(anchor.month), anchor.year)
  }
  return `${anchor.year} год`
}

export function isCalendarView(view: ViewMode): boolean {
  return view === 'month' || view === 'quarter' || view === 'year'
}

export const DAILY_HOURS_GOAL = 8

export function isWeekday(date: Date): boolean {
  const day = date.getDay()
  return day >= 1 && day <= 5
}

export function isWeekend(date: Date): boolean {
  const day = date.getDay()
  return day === 0 || day === 6
}

export type DayCellStatus = 'future' | 'weekend' | 'empty' | 'under' | 'met' | 'over'

export function dayCellStatus(date: Date, hours: number): DayCellStatus {
  if (isWeekend(date)) return 'weekend'

  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const probe = new Date(date)
  probe.setHours(0, 0, 0, 0)
  if (probe > today) return 'future'
  if (hours <= 0) return 'empty'
  if (hours > DAILY_HOURS_GOAL) return 'over'
  if (hours >= DAILY_HOURS_GOAL) return 'met'
  return 'under'
}

export function dayStatusClass(status: DayCellStatus): string {
  if (status === 'weekend') return 'day-weekend'
  if (status === 'empty') return 'day-empty'
  if (status === 'under') return 'day-under'
  if (status === 'met') return 'day-met'
  if (status === 'over') return 'day-over'
  return ''
}

export function tfsEditUrl(tfsUrl: string, id: number): string {
  if (!tfsUrl) return ''
  if (tfsUrl.includes('/edit/')) {
    return tfsUrl.replace(/\/edit\/\d+/, `/edit/${id}`)
  }
  return tfsUrl
}
