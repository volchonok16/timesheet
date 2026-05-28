import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { createPortal } from 'react-dom'
import { buildActivityComment } from '../activityComments'
import { apiFetch, getJson, readApiError } from '../api'
import { loadEntryPrefs, saveEntryPrefs, TIME_PRESETS } from '../entryPrefs'
import type { Activity, CostProjectOptions, Role, WorkItem } from '../types'
import { formatHours, kindLabel } from '../types'
import WorkItemBadge from './WorkItemBadge'

type Props = {
  item: WorkItem
  date: string
  roles: Role[]
  activities: Activity[]
  initialRole?: string
  initialActivity?: string
  keepOpen: boolean
  onKeepOpenChange: (value: boolean) => void
  onClose: () => void
  onSaved: () => void
}

const HOUR_OPTIONS = [0, 1, 2, 3, 4, 5, 6, 7, 8]
const MINUTE_OPTIONS = [0, 15, 30, 45]

function formatDuration(hours: number, minutes: number): string {
  return formatHours(hours + minutes / 60)
}

export default function TimeEntryModal({
  item,
  date,
  roles,
  activities,
  initialRole,
  initialActivity,
  keepOpen,
  onKeepOpenChange,
  onClose,
  onSaved,
}: Props) {
  const [role, setRole] = useState(
    () => initialRole ?? loadEntryPrefs()?.role ?? roles[0]?.label ?? 'Аналитик',
  )
  const [activity, setActivity] = useState(
    () => initialActivity ?? loadEntryPrefs()?.activity ?? '',
  )
  const [entryDate, setEntryDate] = useState(date)
  const [hours, setHours] = useState(0)
  const [minutes, setMinutes] = useState(0)
  const [subtract, setSubtract] = useState(false)
  const [comment, setComment] = useState('')
  const [activityFilter, setActivityFilter] = useState('')
  const [costProject, setCostProject] = useState(() => loadEntryPrefs()?.costProject ?? '')
  const [costProjectFilter, setCostProjectFilter] = useState('')
  const [costProjects, setCostProjects] = useState<CostProjectOptions | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const roleRow = useMemo(
    () => roles.find((row) => row.label === role || row.id === role) ?? roles[0],
    [role, roles],
  )

  const filteredActivities = useMemo(() => {
    if (!roleRow) return activities
    return activities.filter((row) => row.roleId === roleRow.id)
  }, [activities, roleRow])

  const visibleActivities = useMemo(() => {
    const needle = activityFilter.trim().toLowerCase()
    if (!needle) return filteredActivities
    return filteredActivities.filter((row) => row.label.toLowerCase().includes(needle))
  }, [filteredActivities, activityFilter])

  const selectedActivity = useMemo(
    () => filteredActivities.find((row) => row.label === activity),
    [filteredActivities, activity],
  )

  const visibleCostProjects = useMemo(() => {
    const options = costProjects?.options ?? []
    const needle = costProjectFilter.trim().toLowerCase()
    if (!needle) return options
    return options.filter((value) => value.toLowerCase().includes(needle))
  }, [costProjects, costProjectFilter])

  const costProjectReady = Boolean(costProjects?.options.length)

  useEffect(() => {
    setEntryDate(date)
  }, [date])

  useEffect(() => {
    setRole(initialRole ?? loadEntryPrefs()?.role ?? roles[0]?.label ?? 'Аналитик')
    setActivity(initialActivity ?? loadEntryPrefs()?.activity ?? '')
  }, [item.id, date, initialRole, initialActivity, roles])

  useEffect(() => {
    void getJson<CostProjectOptions>(`/api/work-items/${item.id}/cost-projects`)
      .then((payload) => {
        setCostProjects(payload)
        const saved = loadEntryPrefs()?.costProject
        const candidate =
          (saved && payload.options.includes(saved) ? saved : null) ||
          (payload.defaultValue && payload.options.includes(payload.defaultValue) ? payload.defaultValue : null) ||
          ''
        setCostProject(candidate)
      })
      .catch(() => setCostProjects(null))
  }, [item.id])

  useEffect(() => {
    if (selectedActivity?.commentTemplate) {
      setComment(buildActivityComment(selectedActivity.commentTemplate, item))
    }
  }, [selectedActivity, item])

  useEffect(() => {
    setActivityFilter('')
  }, [role])

  useEffect(() => {
    if (visibleActivities.length === 0) return
    if (!visibleActivities.some((row) => row.label === activity)) {
      setActivity(visibleActivities[0].label)
    }
  }, [visibleActivities, activity])

  useEffect(() => {
    if (filteredActivities.length > 0 && !filteredActivities.some((row) => row.label === activity)) {
      setActivity(filteredActivities[0].label)
    }
  }, [filteredActivities, activity])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setLoading(true)
    setError(null)
    try {
      const response = await apiFetch('/api/time-entries', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          parentWorkItemId: item.id,
          role,
          activity,
          entryDate,
          hours,
          minutes,
          subtract,
          comment: comment.trim() || null,
          costProject: costProject || null,
        }),
      })
      if (!response.ok) throw new Error(await readApiError(response))
      saveEntryPrefs({ role, activity, costProject: costProject || undefined })
      onSaved()
      setHours(0)
      setMinutes(0)
      setSubtract(false)
      if (!keepOpen) {
        setComment('')
      } else if (selectedActivity?.commentTemplate) {
        setComment(buildActivityComment(selectedActivity.commentTemplate, item))
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось сохранить')
    } finally {
      setLoading(false)
    }
  }

  return createPortal(
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-card entry-modal" onClick={(e) => e.stopPropagation()}>
        <header className="entry-header">
          <div className="entry-task">
            <p className="eyebrow">Внести время</p>
            <div className="entry-task-title-row">
              <span className="entry-task-id">#{item.id}</span>
              <h2 className="entry-task-title">{item.title}</h2>
            </div>
            <div className="item-meta">
              <WorkItemBadge kind={item.kind} label={kindLabel(item.kind)} />
              <WorkItemBadge kind="task" label={item.state} muted />
            </div>
            <span className="path entry-task-path">{item.areaPath}</span>
          </div>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Закрыть">
            ×
          </button>
        </header>

        <form className="entry-form" onSubmit={submit}>
          <div className="entry-grid">
            <section className="entry-panel">
              <div className="field">
                <span>Роль</span>
                <div className="role-chips">
                  {roles.map((row) => (
                    <button
                      key={row.id}
                      type="button"
                      className={`role-chip${role === row.label ? ' active' : ''}`}
                      onClick={() => setRole(row.label)}
                    >
                      {row.label}
                    </button>
                  ))}
                </div>
              </div>

              <label className="field">
                <span>Проект учёта затрат</span>
                {costProjectReady ? (
                  <>
                    <input
                      className="activity-filter"
                      type="search"
                      placeholder="Найти в списке…"
                      value={costProjectFilter}
                      onChange={(e) => setCostProjectFilter(e.target.value)}
                    />
                    <select
                      className="cost-project-select"
                      value={costProject}
                      onChange={(e) => setCostProject(e.target.value)}
                      required
                      size={Math.min(Math.max(visibleCostProjects.length, 1), 6)}
                    >
                      {!costProject && <option value="">Выберите проект…</option>}
                      {visibleCostProjects.map((value) => (
                        <option key={value} value={value}>
                          {value}
                        </option>
                      ))}
                    </select>
                    {costProjectFilter.trim() && visibleCostProjects.length === 0 && (
                      <p className="muted small">Ничего не найдено</p>
                    )}
                  </>
                ) : (
                  <p className="muted small">
                    {costProjects === null ? 'Загружаем список из TFS…' : 'Не удалось загрузить список проектов'}
                  </p>
                )}
              </label>

              <div className="field">
                <span>Активность</span>
                <input
                  className="activity-filter"
                  type="search"
                  placeholder="Найти активность…"
                  value={activityFilter}
                  onChange={(e) => setActivityFilter(e.target.value)}
                />
                <div className="activity-list" role="listbox" aria-label="Активность">
                  {visibleActivities.length === 0 ? (
                    <p className="muted small activity-empty">Ничего не найдено</p>
                  ) : (
                    visibleActivities.map((row) => (
                      <button
                        key={row.id}
                        type="button"
                        role="option"
                        aria-selected={activity === row.label}
                        className={`activity-option${activity === row.label ? ' active' : ''}`}
                        onClick={() => setActivity(row.label)}
                      >
                        {row.label}
                      </button>
                    ))
                  )}
                </div>
              </div>
            </section>

            <section className="entry-panel entry-panel-side">
              <div className="field-row entry-meta-row">
                <label className="field">
                  <span>Дата</span>
                  <input type="date" value={entryDate} onChange={(e) => setEntryDate(e.target.value)} required />
                </label>
                <div className="field">
                  <span>Действие</span>
                  <div className="segmented">
                    <button type="button" className={!subtract ? 'active' : ''} onClick={() => setSubtract(false)}>
                      Добавить
                    </button>
                    <button type="button" className={subtract ? 'active' : ''} onClick={() => setSubtract(true)}>
                      Вычесть
                    </button>
                  </div>
                </div>
              </div>

              <section className="time-panel">
                <div className="time-panel-head">
                  <span>Длительность</span>
                  <strong className={subtract ? 'time-negative' : ''}>
                    {subtract ? '−' : ''}
                    {formatDuration(hours, minutes)}
                  </strong>
                </div>

                <div className="time-group">
                  <span className="time-group-label">Быстро</span>
                  <div className="chip-row preset-row">
                    {TIME_PRESETS.map((preset) => (
                      <button
                        key={preset.label}
                        type="button"
                        className={`chip preset${hours === preset.hours && minutes === preset.minutes ? ' active' : ''}`}
                        onClick={() => {
                          setHours(preset.hours)
                          setMinutes(preset.minutes)
                        }}
                      >
                        {preset.label}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="time-group">
                  <span className="time-group-label">Часы</span>
                  <div className="chip-row">
                    {HOUR_OPTIONS.map((value) => (
                      <button
                        key={value}
                        type="button"
                        className={`chip${hours === value ? ' active' : ''}`}
                        onClick={() => setHours(value)}
                      >
                        {value}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="time-group">
                  <span className="time-group-label">Минуты</span>
                  <div className="chip-row">
                    {MINUTE_OPTIONS.map((value) => (
                      <button
                        key={value}
                        type="button"
                        className={`chip${minutes === value ? ' active' : ''}`}
                        onClick={() => setMinutes(value)}
                      >
                        {value}
                      </button>
                    ))}
                  </div>
                </div>
              </section>

              <label className="field">
                <span>Комментарий</span>
                <textarea
                  value={comment}
                  onChange={(e) => setComment(e.target.value)}
                  rows={3}
                  placeholder="Подставляется из активности и задачи, можно изменить"
                />
              </label>

              {error && <p className="error-banner">{error}</p>}

              <div className="entry-actions">
                <button
                  type="submit"
                  className="btn primary wide"
                  disabled={loading || (hours === 0 && minutes === 0) || !costProject || !costProjectReady}
                >
                  {loading ? 'Сохраняем в TFS…' : 'Внести время'}
                </button>
                <label className="checkbox-row">
                  <input
                    type="checkbox"
                    checked={keepOpen}
                    onChange={(e) => onKeepOpenChange(e.target.checked)}
                  />
                  Не закрывать окно после сохранения
                </label>
              </div>
            </section>
          </div>
        </form>
      </div>
    </div>,
    document.body,
  )
}
