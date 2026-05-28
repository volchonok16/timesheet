import type { WorkItem } from './types'

/** Выделяет смысловую часть из заголовка задачи / ЗНИ. */
export function extractFeature(title: string): string {
  let text = title.trim()
  const dotIdx = text.indexOf('. ')
  if (dotIdx > 0 && dotIdx <= 20) {
    text = text.slice(dotIdx + 2).trim()
  }
  text = text.replace(/\.\s*Техдолг\s+\S+.*$/i, '').trim()
  text = text.replace(/^На экране\s+/i, '').trim()
  if (text.length > 120) {
    text = `${text.slice(0, 117)}…`
  }
  return text || title.trim()
}

export function buildActivityComment(template: string, item: Pick<WorkItem, 'id' | 'title'>): string {
  if (!template.includes('{feature}') && !template.includes('{item')) {
    return template
  }
  const feature = extractFeature(item.title)
  return template
    .replaceAll('{feature}', feature)
    .replaceAll('{item_id}', String(item.id))
    .replaceAll('{item_title}', item.title)
}
