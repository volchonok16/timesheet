import { useCallback, useEffect, useMemo, useState } from 'react'
import { apiFetch, clearSessionId, getJson } from '../api'
import type { Activity, Calendar, Role, StatsSummary, Timesheet, WorkItem } from '../types'
import {
  addDays,
  isCalendarView,
  parseIsoDate,
  periodLabel,
  quarterForMonth,
  shiftAnchor,
  toIsoDate,
  type PeriodAnchor,
  type ViewMode,
  weekStart,
} from '../types'
import AddEntryButton from './AddEntryButton'
import CalendarPanel from './CalendarPanel'
import RecentEntries from './RecentEntries'
import SearchBar from './SearchBar'
import StatsBanner from './StatsBanner'
import TimeEntryModal from './TimeEntryModal'
import WeekGrid from './WeekGrid'

const BACKGROUND_SYNC_TTL_MS = 10 * 60 * 1000
const DATA_REPAIR_KEY = 'timesheet-data-repair-v2'

type Props = {
  onLogout: () => void
}

function syncStorageKey(start: string, view: string) {
  return `timesheet-sync:${start}:${view}`
}

export default function TimesheetApp({ onLogout }: Props) {
  const [version, setVersion] = useState('0.1.0')
  const [view, setView] = useState<ViewMode>('week')
  const [periodStart, setPeriodStart] = useState(() => weekStart(new Date()))
  const [periodAnchor, setPeriodAnchor] = useState<PeriodAnchor>(() => {
    const now = new Date()
    return { year: now.getFullYear(), month: now.getMonth() + 1 }
  })
  const [timesheet, setTimesheet] = useState<Timesheet | null>(null)
  const [calendar, setCalendar] = useState<Calendar | null>(null)
  const [roles, setRoles] = useState<Role[]>([])
  const [activities, setActivities] = useState<Activity[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [modalItem, setModalItem] = useState<WorkItem | null>(null)
  const [modalDate, setModalDate] = useState<string>(() => toIsoDate(new Date()))
  const [modalRole, setModalRole] = useState<string | undefined>()
  const [modalActivity, setModalActivity] = useState<string | undefined>()
  const [keepOpen, setKeepOpen] = useState(false)
  const [stats, setStats] = useState<StatsSummary | null>(null)
  const [statsLoading, setStatsLoading] = useState(true)
  const [refreshKey, setRefreshKey] = useState(0)
  const [syncing, setSyncing] = useState(false)
  const [searchOpen, setSearchOpen] = useState(false)
  const [addEntryOpen, setAddEntryOpen] = useState(false)
  const periodEnd = useMemo(() => addDays(periodStart, 6), [periodStart])

  const loadMeta = async () => {
    const [rolesPayload, activitiesPayload, defaults] = await Promise.all([
      getJson<Role[]>('/api/meta/roles'),
      getJson<Activity[]>('/api/meta/activities'),
      getJson<{ version: string }>('/api/auth/defaults'),
    ])
    setRoles(rolesPayload)
    setActivities(activitiesPayload)
    setVersion(defaults.version)
  }

  const loadStats = async () => {
    setStatsLoading(true)
    const delays = [0, 800, 2000]
    for (const delay of delays) {
      if (delay > 0) {
        await new Promise((resolve) => window.setTimeout(resolve, delay))
      }
      try {
        const payload = await getJson<StatsSummary>('/api/stats/summary')
        setStats(payload)
        setStatsLoading(false)
        return
      } catch {
        /* retry while backend warms up */
      }
    }
    setStats(null)
    setStatsLoading(false)
  }

  const loadTimesheet = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const start = toIsoDate(periodStart)
      const payload = await getJson<Timesheet>(`/api/timesheet?start=${start}&view=week&sync=false`)
      setTimesheet(payload)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось загрузить табель')
      setTimesheet(null)
    } finally {
      setLoading(false)
    }
  }, [periodStart])

  const loadCalendar = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams({
        scope: view,
        year: String(periodAnchor.year),
      })
      if (view !== 'year') {
        params.set('month', String(periodAnchor.month))
      }
      if (view === 'quarter') {
        params.set('quarter', String(quarterForMonth(periodAnchor.month)))
      }
      const payload = await getJson<Calendar>(`/api/calendar?${params}`)
      setCalendar(payload)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось загрузить календарь')
      setCalendar(null)
    } finally {
      setLoading(false)
    }
  }, [periodAnchor, view])

  const syncFromTfs = useCallback(
    async (force = false) => {
      setSyncing(true)
      setError(null)
      try {
        const start = isCalendarView(view)
          ? `${periodAnchor.year}-${String(periodAnchor.month).padStart(2, '0')}-01`
          : toIsoDate(periodStart)
        const syncView = isCalendarView(view) ? 'month' : 'week'
        const forceParam = force ? '&force=true' : ''
        const response = await apiFetch(
          `/api/timesheet/sync?start=${start}&view=${syncView}${forceParam}`,
          { method: 'POST' },
        )
        if (!response.ok) {
          throw new Error(await response.text())
        }
        const result = (await response.json()) as { imported?: number; cached?: boolean }
        sessionStorage.setItem(syncStorageKey(start, syncView), String(Date.now()))
        if (!result.cached && (result.imported ?? 0) > 0) {
          setRefreshKey((value) => value + 1)
        } else if (force) {
          setRefreshKey((value) => value + 1)
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Не удалось подтянуть списания из TFS')
      } finally {
        setSyncing(false)
      }
    },
    [periodAnchor, periodStart, view],
  )

  useEffect(() => {
    void loadMeta()
    void loadStats()
  }, [])

  useEffect(() => {
    if (sessionStorage.getItem(DATA_REPAIR_KEY)) {
      return
    }
    let cancelled = false
    void (async () => {
      try {
        const response = await apiFetch('/api/timesheet/repair', { method: 'POST' })
        if (!response.ok || cancelled) {
          return
        }
        sessionStorage.setItem(DATA_REPAIR_KEY, '1')
        setRefreshKey((value) => value + 1)
      } catch {
        /* одноразовый repair не должен ломать UI */
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    void loadStats()
  }, [refreshKey])

  useEffect(() => {
    if (isCalendarView(view)) {
      void loadCalendar()
      return
    }
    void loadTimesheet()
  }, [loadCalendar, loadTimesheet, view, refreshKey])

  useEffect(() => {
    if (isCalendarView(view)) {
      return
    }
    const start = toIsoDate(periodStart)
    const key = syncStorageKey(start, 'week')
    const last = Number(sessionStorage.getItem(key) || '0')
    if (Date.now() - last < BACKGROUND_SYNC_TTL_MS) {
      return
    }
    let cancelled = false
    void (async () => {
      try {
        const response = await apiFetch(`/api/timesheet/sync?start=${start}&view=week`, {
          method: 'POST',
        })
        if (!response.ok || cancelled) {
          return
        }
        sessionStorage.setItem(key, String(Date.now()))
        const result = (await response.json()) as { imported?: number; cached?: boolean }
        if (!cancelled && !result.cached && (result.imported ?? 0) > 0) {
          setRefreshKey((value) => value + 1)
        }
      } catch {
        /* фоновая синхронизация не должна ронять UI */
      }
    })()
    return () => {
      cancelled = true
    }
  }, [periodStart, view])

  const shiftPeriod = (delta: number) => {
    if (isCalendarView(view)) {
      setPeriodAnchor((current) => shiftAnchor(current, view, delta))
      return
    }
    setPeriodStart((current) => addDays(current, delta * 7))
  }

  const openEntry = (item: WorkItem, date?: string, role?: string, activity?: string) => {
    setModalItem(item)
    setModalDate(date ?? toIsoDate(new Date()))
    setModalRole(role)
    setModalActivity(activity)
  }

  const closeEntry = () => {
    setModalItem(null)
    setModalRole(undefined)
    setModalActivity(undefined)
  }

  const goToday = () => {
    const now = new Date()
    if (isCalendarView(view)) {
      setPeriodAnchor({ year: now.getFullYear(), month: now.getMonth() + 1 })
      return
    }
    setPeriodStart(weekStart(now))
  }

  const pickCalendarDay = (isoDate: string) => {
    setPeriodStart(weekStart(parseIsoDate(isoDate)))
    setView('week')
  }

  const bumpRefresh = () => setRefreshKey((value) => value + 1)

  const logout = async () => {
    await apiFetch('/api/auth/logout', { method: 'POST' })
    clearSessionId()
    onLogout()
  }

  const label = periodLabel(view, periodStart, periodAnchor)

  return (
    <div className="app-layout">
      <header className="topbar">
        <div className="topbar-left">
          <div className="brand-block">
            <span className="brand-mark sm">TS</span>
            <span className="brand-title">TFS Timesheet</span>
          </div>
          <nav className="view-switch" aria-label="Режим просмотра">
            {(
              [
                ['week', 'Неделя'],
                ['month', 'Месяц'],
                ['quarter', 'Квартал'],
                ['year', 'Год'],
              ] as const
            ).map(([mode, title]) => (
              <button
                key={mode}
                type="button"
                className={view === mode ? 'active' : ''}
                onClick={() => setView(mode)}
              >
                {title}
              </button>
            ))}
          </nav>
        </div>
        <div className="topbar-right">
          <span className="muted version-tag">v{version}</span>
          <button type="button" className="btn ghost logout-btn" onClick={() => void logout()}>
            Выйти
          </button>
        </div>
      </header>

      <main
        className={`content${searchOpen ? ' search-focused' : ''}${addEntryOpen || modalItem ? ' overlay-open' : ''}`}
      >
        <section className={`toolbar panel${searchOpen ? ' toolbar-open' : ''}`}>
          <div className="search-actions">
            <SearchBar onSelect={(item) => openEntry(item)} onOpenChange={setSearchOpen} />
            <AddEntryButton onSelect={(item) => openEntry(item)} onOpenChange={setAddEntryOpen} />
          </div>
        </section>

        <section className="period-bar panel">
          <button type="button" className="btn ghost period-nav" onClick={() => shiftPeriod(-1)}>
            ← Назад
          </button>
          <div className="period-center">
            <h2>{label}</h2>
            <button type="button" className="btn ghost today-btn" onClick={goToday}>
              Сегодня
            </button>
            <button
              type="button"
              className="btn ghost today-btn"
              disabled={syncing}
              title="Полная подтяжка из TFS (может занять до минуты)"
              onClick={() => void syncFromTfs(true)}
            >
              {syncing ? 'Подтягиваем…' : 'Из TFS'}
            </button>
          </div>
          <button type="button" className="btn ghost period-nav" onClick={() => shiftPeriod(1)}>
            Вперёд →
          </button>
        </section>

        <div className="dashboard-row">
          <StatsBanner stats={stats} loading={statsLoading} />
          <RecentEntries refreshKey={refreshKey} onRepeat={(item, date) => openEntry(item, date)} />
        </div>

        {error && <p className="error-banner">{error}</p>}

        {isCalendarView(view) ? (
          <CalendarPanel
            calendar={calendar}
            scope={view as 'month' | 'quarter' | 'year'}
            onPickDay={pickCalendarDay}
          />
        ) : loading && !timesheet ? (
          <div className="panel loading-panel">Загружаем табель…</div>
        ) : timesheet ? (
          <WeekGrid
            timesheet={timesheet}
            periodStart={periodStart}
            periodEnd={periodEnd}
            onAddTime={openEntry}
          />
        ) : (
          <div className="panel loading-panel">Нет данных табеля</div>
        )}
      </main>

      {modalItem && (
        <TimeEntryModal
          item={modalItem}
          date={modalDate}
          roles={roles}
          activities={activities}
          initialRole={modalRole}
          initialActivity={modalActivity}
          keepOpen={keepOpen}
          onKeepOpenChange={setKeepOpen}
          onClose={closeEntry}
          onSaved={() => {
            bumpRefresh()
            if (isCalendarView(view)) void loadCalendar()
            else void loadTimesheet()
            if (!keepOpen) closeEntry()
          }}
        />
      )}
    </div>
  )
}
