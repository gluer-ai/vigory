import { BaseEdge, EdgeLabelRenderer, getBezierPath, type EdgeProps } from '@xyflow/react'

export interface RelationEdgeData {
  link_type: string
  proposed?: boolean
  [key: string]: unknown
}

export function RelationEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  data,
  selected,
}: EdgeProps & { data: RelationEdgeData }) {
  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  })

  const color = selected ? 'var(--color-focus)' : 'var(--color-border-strong)'

  return (
    <>
      <BaseEdge
        id={id}
        path={edgePath}
        style={{
          stroke: color,
          strokeWidth: selected ? 2 : 1.5,
          strokeDasharray: data.proposed ? '4 3' : undefined,
        }}
      />
      <EdgeLabelRenderer>
        <div
          className="pointer-events-none absolute rounded bg-[var(--color-surface-1)] px-1.5 py-0.5 font-mono text-[10px] text-[var(--color-text-muted)]"
          style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
        >
          {data.link_type}
        </div>
      </EdgeLabelRenderer>
    </>
  )
}
