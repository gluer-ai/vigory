import { Handle, Position, type NodeProps } from '@xyflow/react'
import { Maximize2 } from 'lucide-react'
import { classMeta } from '../../lib/entityClass'
import { rootKey, type ChangeStatus } from '../../lib/sandboxModel'

export interface SandboxNodeViewData {
  label: string
  entity_subclass: string
  status: ChangeStatus
  /** How many entities/links sit directly inside this one. */
  inside: number
  [key: string]: unknown
}

const BADGE: Record<ChangeStatus, { text: string; color: string } | null> = {
  original: null,
  edited: { text: 'edited', color: 'var(--color-confidence-medium)' },
  new: { text: 'new', color: 'var(--color-focus)' },
}

/** An entity on the sandbox canvas. Handles are visible (unlike the Scope
 * view) because dragging from one to another node is how you add a link. */
export function SandboxNode({ data, selected }: NodeProps & { data: SandboxNodeViewData }) {
  const { icon: Icon, colorVar } = classMeta(rootKey(data.entity_subclass), data.entity_subclass)
  const color = `var(${colorVar})`
  const badge = BADGE[data.status]
  const leaf = data.entity_subclass.split('.').slice(1).join(' › ') || data.entity_subclass

  return (
    <div
      className={`flex min-w-[170px] max-w-[240px] flex-col gap-1 rounded-lg border bg-[var(--color-surface-1)] px-3 py-2 shadow-sm ${
        selected ? 'ring-2 ring-[var(--color-focus)]' : ''
      } ${data.status === 'new' ? 'border-dashed' : 'border-solid'}`}
      style={{ borderColor: selected ? 'var(--color-focus)' : color }}
      role="group"
      aria-label={`${data.label}, ${leaf}${badge ? `, ${badge.text}` : ''}${data.inside > 0 ? `, ${data.inside} inside` : ''}`}
    >
      <Handle
        type="target"
        position={Position.Top}
        className="!h-3 !w-3 !border !border-[var(--color-border-strong)] !bg-[var(--color-surface-0)]"
      />
      <div className="flex items-center gap-2">
        <Icon size={14} style={{ color }} aria-hidden="true" />
        <span className="truncate text-sm font-medium text-[var(--color-text-primary)]">{data.label}</span>
      </div>
      <span className="truncate text-xs text-[var(--color-text-muted)]">{leaf}</span>
      {data.inside > 0 && (
        <span className="flex items-center gap-1 text-[10px] font-medium text-[var(--color-text-muted)]">
          <Maximize2 size={10} aria-hidden="true" /> {data.inside} inside · double-click to open
        </span>
      )}
      {badge && (
        <span className="text-[10px] font-semibold uppercase tracking-wide" style={{ color: badge.color }}>
          {badge.text}
        </span>
      )}
      <Handle
        type="source"
        position={Position.Bottom}
        className="!h-3 !w-3 !border !border-[var(--color-border-strong)] !bg-[var(--color-surface-0)]"
      />
    </div>
  )
}
