import type { WorkItemKind } from '../types'

type Props = {
  kind: WorkItemKind | 'task' | 'change_request'
  label: string
  muted?: boolean
}

export default function WorkItemBadge({ kind, label, muted }: Props) {
  return <span className={`badge badge-${kind}${muted ? ' muted' : ''}`}>{label}</span>
}
