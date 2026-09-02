import { Handle, Position, type NodeProps } from '@xyflow/react'
import { classMeta } from '../../lib/entityClass'
import { ConfidenceChip } from '../ui/Chip'

export interface EntityNodeData {
  label: string
  entity_class: string
  entity_subclass?: string
  entity_id: string
  confidence: string
  proposed?: boolean
  isTrigger?: boolean
  relevance?: 'high' | 'medium' | 'low'
  [key: string]: unknown
}

export function EntityNode({ data, selected }: NodeProps & { data: EntityNodeData }) {
  const { icon: Icon, colorVar } = classMeta(data.entity_class, data.entity_subclass)
  const color = `var(${colorVar})`

  return (
    <div
      className={`flex min-w-[160px] flex-col gap-1 rounded-lg border bg-[var(--color-surface-1)] px-3 py-2 shadow-sm transition-[border-color,box-shadow] duration-[var(--duration-fast)] ${
        selected ? 'ring-2 ring-[var(--color-focus)]' : ''
      } ${data.proposed ? 'border-dashed' : 'border-solid'}`}
      style={{ borderColor: selected ? 'var(--color-focus)' : color }}
      role="group"
      aria-label={`${data.entity_class} entity ${data.label}, confidence ${data.confidence}${data.isTrigger ? ', trigger entity' : ''}`}
    >
      <Handle type="target" position={Position.Top} className="opacity-0" />
      <div className="flex items-center gap-2">
        <Icon size={14} style={{ color }} aria-hidden="true" />
        <span className="truncate text-sm font-medium text-[var(--color-text-primary)]">{data.label}</span>
      </div>
      <div className="flex items-center justify-between">
        <span className="font-mono text-xs text-[var(--color-text-muted)]">{data.entity_id}</span>
        <ConfidenceChip code={data.confidence} />
      </div>
      {data.isTrigger && (
        <span className="text-[10px] font-semibold uppercase tracking-wide" style={{ color }}>
          trigger
        </span>
      )}
      {data.relevance && (
        <span className="text-[10px] font-medium" style={{ color: `var(--color-confidence-${data.relevance})` }}>
          agent: {data.relevance} relevance
        </span>
      )}
      <Handle type="source" position={Position.Bottom} className="opacity-0" />
    </div>
  )
}
