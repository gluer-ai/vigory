import type { Entity, Link, RelevanceAnnotation } from '../../lib/types'
import { ConfidenceChip, StatusChip } from '../ui/Chip'

interface ScopeListViewProps {
  triggerEntityId: string
  nodes: Entity[]
  edges: Link[]
  onSelect: (entityId: string) => void
  annotations?: Record<string, RelevanceAnnotation>
}

/** WCAG 2.2 AA keyboard-operable fallback: the same scoped nodes+edges as a
 * table, since canvas drag/zoom is not reliably keyboard-operable. */
export function ScopeListView({
  triggerEntityId,
  nodes,
  edges,
  onSelect,
  annotations,
}: ScopeListViewProps) {
  return (
    <div className="flex h-full flex-col gap-6 overflow-y-auto p-4">
      <section aria-labelledby="nodes-heading">
        <h2 id="nodes-heading" className="mb-2 text-sm font-semibold text-[var(--color-text-primary)]">
          Entities ({nodes.length})
        </h2>
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-[var(--color-border)] text-left text-[var(--color-text-muted)]">
              <th className="py-1.5 pe-3 font-medium">Label</th>
              <th className="py-1.5 pe-3 font-medium">Class</th>
              <th className="py-1.5 pe-3 font-medium">Status</th>
              <th className="py-1.5 pe-3 font-medium">Confidence</th>
              {annotations && <th className="py-1.5 pe-3 font-medium">Agent relevance</th>}
            </tr>
          </thead>
          <tbody>
            {nodes.map((n) => (
              <tr key={n.entity_id} className="border-b border-[var(--color-border)]">
                <td className="py-1.5 pe-3">
                  <button
                    type="button"
                    onClick={() => onSelect(n.entity_id)}
                    className="rounded text-start text-[var(--color-text-primary)] underline-offset-2 hover:underline"
                  >
                    {n.label}
                    {n.entity_id === triggerEntityId ? ' (trigger)' : ''}
                  </button>
                </td>
                <td className="py-1.5 pe-3 font-mono text-xs text-[var(--color-text-muted)]">
                  {n.entity_class}
                </td>
                <td className="py-1.5 pe-3">
                  <StatusChip status={n.status} />
                </td>
                <td className="py-1.5 pe-3">
                  <ConfidenceChip code={n.confidence} />
                </td>
                {annotations && (
                  <td className="py-1.5 pe-3 text-xs text-[var(--color-text-muted)]">
                    {annotations[n.entity_id]?.relevance ?? '—'}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section aria-labelledby="edges-heading">
        <h2 id="edges-heading" className="mb-2 text-sm font-semibold text-[var(--color-text-primary)]">
          Links ({edges.length})
        </h2>
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-[var(--color-border)] text-left text-[var(--color-text-muted)]">
              <th className="py-1.5 pe-3 font-medium">Type</th>
              <th className="py-1.5 pe-3 font-medium">Source</th>
              <th className="py-1.5 pe-3 font-medium">Target</th>
              <th className="py-1.5 pe-3 font-medium">Confidence</th>
            </tr>
          </thead>
          <tbody>
            {edges.map((e) => (
              <tr key={e.link_id} className="border-b border-[var(--color-border)]">
                <td className="py-1.5 pe-3 font-mono text-xs">{e.link_type}</td>
                <td className="py-1.5 pe-3">
                  <button
                    type="button"
                    onClick={() => onSelect(e.source_entity)}
                    className="text-[var(--color-text-primary)] underline-offset-2 hover:underline"
                  >
                    {e.source_entity}
                  </button>
                </td>
                <td className="py-1.5 pe-3">
                  <button
                    type="button"
                    onClick={() => onSelect(e.target_entity)}
                    className="text-[var(--color-text-primary)] underline-offset-2 hover:underline"
                  >
                    {e.target_entity}
                  </button>
                </td>
                <td className="py-1.5 pe-3">
                  <ConfidenceChip code={e.confidence} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  )
}
