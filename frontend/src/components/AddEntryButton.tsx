import { useState, type FormEvent } from 'react'
import { createPortal } from 'react-dom'
import { getJson } from '../api'
import type { WorkItem } from '../types'
import { kindLabel } from '../types'
import WorkItemBadge from './WorkItemBadge'

type Props = {
  onSelect: (item: WorkItem) => void
  onOpenChange?: (open: boolean) => void
}

export default function AddEntryButton({ onSelect, onOpenChange }: Props) {
  const [open, setOpen] = useState(false)
  const [value, setValue] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [preview, setPreview] = useState<WorkItem | null>(null)

  const close = () => {
    setOpen(false)
    onOpenChange?.(false)
    setValue('')
    setError(null)
    setPreview(null)
    setLoading(false)
  }

  const openModal = () => {
    setOpen(true)
    onOpenChange?.(true)
  }

  const lookup = async (raw: string) => {
    const trimmed = raw.trim()
    if (!/^\d+$/.test(trimmed)) {
      setError('Введите числовой номер ЗНИ, требования или задачи.')
      setPreview(null)
      return
    }
    setLoading(true)
    setError(null)
    try {
      const item = await getJson<WorkItem>(`/api/work-items/${trimmed}`)
      setPreview(item)
    } catch (err) {
      setPreview(null)
      setError(err instanceof Error ? err.message : 'Элемент не найден в TFS')
    } finally {
      setLoading(false)
    }
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (preview) {
      onSelect(preview)
      close()
      return
    }
    await lookup(value)
  }

  return (
    <>
      <button type="button" className="btn primary add-entry-btn" onClick={openModal}>
        + Добавить списание
      </button>

      {open &&
        createPortal(
          <div className="modal-backdrop" onClick={close}>
            <div className="modal-card add-entry-card" onClick={(e) => e.stopPropagation()}>
            <header className="modal-header">
              <div>
                <p className="eyebrow">Новое списание</p>
                <h2>Укажите ЗНИ</h2>
              </div>
              <button type="button" className="icon-btn" onClick={close} aria-label="Закрыть">
                ×
              </button>
            </header>

            <form className="stack" onSubmit={submit}>
              <label className="field">
                <span>Номер ЗНИ</span>
                <input
                  value={value}
                  onChange={(e) => {
                    setValue(e.target.value)
                    setPreview(null)
                    setError(null)
                  }}
                  placeholder="Например, 587676"
                  inputMode="numeric"
                  autoFocus
                  required
                />
              </label>
              <p className="muted small">
                Можно также указать номер требования, ошибки или задачи — откроется форма списания времени.
              </p>

              {preview && (
                <div className="preview-card">
                  <strong>
                    {preview.id} {preview.title}
                  </strong>
                  <div className="item-meta">
                    <WorkItemBadge kind={preview.kind} label={kindLabel(preview.kind)} />
                    <WorkItemBadge kind="task" label={preview.state} muted />
                    <span className="path">{preview.areaPath}</span>
                  </div>
                </div>
              )}

              {error && <p className="error-banner">{error}</p>}

              <div className="field-row actions-row">
                <button type="button" className="btn ghost" onClick={close}>
                  Отмена
                </button>
                <button type="submit" className="btn primary" disabled={loading}>
                  {loading ? 'Ищем в TFS…' : preview ? 'Внести время' : 'Найти'}
                </button>
              </div>
            </form>
          </div>
        </div>,
          document.body,
        )}
    </>
  )
}
