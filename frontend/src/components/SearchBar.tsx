import { useEffect, useRef, useState, type RefObject } from 'react'
import { createPortal } from 'react-dom'
import { getJson } from '../api'
import type { WorkItem } from '../types'
import { kindLabel } from '../types'
import WorkItemBadge from './WorkItemBadge'

type Props = {
  onSelect: (item: WorkItem) => void
  onOpenChange?: (open: boolean) => void
}

type PopoverRect = {
  top: number
  left: number
  width: number
}

function usePopoverRect(open: boolean, anchorRef: RefObject<HTMLElement | null>) {
  const [rect, setRect] = useState<PopoverRect | null>(null)

  useEffect(() => {
    if (!open || !anchorRef.current) {
      setRect(null)
      return
    }

    const update = () => {
      if (!anchorRef.current) return
      const box = anchorRef.current.getBoundingClientRect()
      setRect({
        top: box.bottom + 8,
        left: box.left,
        width: box.width,
      })
    }

    update()
    window.addEventListener('resize', update)
    window.addEventListener('scroll', update, true)
    return () => {
      window.removeEventListener('resize', update)
      window.removeEventListener('scroll', update, true)
    }
  }, [open, anchorRef])

  return rect
}

export default function SearchBar({ onSelect, onOpenChange }: Props) {
  const [query, setQuery] = useState('')
  const [recent, setRecent] = useState<WorkItem[]>([])
  const [results, setResults] = useState<WorkItem[]>([])
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const shellRef = useRef<HTMLDivElement>(null)
  const popoverRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    void getJson<WorkItem[]>('/api/work-items/recent')
      .then(setRecent)
      .catch(() => undefined)
  }, [])

  useEffect(() => {
    const onDocClick = (event: MouseEvent) => {
      const target = event.target as Node
      if (shellRef.current?.contains(target) || popoverRef.current?.contains(target)) return
      setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [])

  useEffect(() => {
    const trimmed = query.trim()
    if (trimmed.length < 2) {
      setResults([])
      return
    }
    const timer = window.setTimeout(async () => {
      setLoading(true)
      try {
        const payload = await getJson<WorkItem[]>(`/api/work-items/search?q=${encodeURIComponent(trimmed)}`)
        setResults(payload)
        setOpen(true)
      } catch {
        setResults([])
      } finally {
        setLoading(false)
      }
    }, 250)
    return () => window.clearTimeout(timer)
  }, [query])

  const items = query.trim().length >= 2 ? results : recent
  const showDropdown = open && (items.length > 0 || loading)
  const popoverRect = usePopoverRect(showDropdown, shellRef)

  useEffect(() => {
    onOpenChange?.(showDropdown)
  }, [showDropdown, onOpenChange])

  return (
    <>
      <div className="search-shell" ref={shellRef}>
        <input
          className="search-input"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onFocus={() => setOpen(true)}
          placeholder="Номер или название ЗНИ / требования / задачи"
          aria-expanded={showDropdown}
          aria-controls="search-popover"
        />
      </div>

      {showDropdown &&
        popoverRect &&
        createPortal(
          <div
            id="search-popover"
            ref={popoverRef}
            className="search-popover"
            style={{
              top: popoverRect.top,
              left: popoverRect.left,
              width: popoverRect.width,
            }}
          >
            <p className="dropdown-kicker">{query.trim().length >= 2 ? 'Результаты' : 'Недавние'}</p>
            {loading && <p className="muted small">Ищем…</p>}
            {!loading && items.length === 0 && <p className="muted small activity-empty">Ничего не найдено</p>}
            <ul>
              {items.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    onClick={() => {
                      onSelect(item)
                      setOpen(false)
                      setQuery('')
                    }}
                  >
                    <span className="search-item-title">
                      <strong>{item.id}</strong> {item.title}
                    </span>
                    <span className="dropdown-meta">
                      <WorkItemBadge kind={item.kind} label={kindLabel(item.kind)} />
                      <WorkItemBadge kind="task" label={item.state} muted />
                      <span className="path">{item.areaPath}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>,
          document.body,
        )}
    </>
  )
}
