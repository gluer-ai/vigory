import { useState } from 'react'
import type { ReactNode } from 'react'
import type { IngestBatch } from '../../lib/types'
import { AddToBatchDialog } from './AddToBatchDialog'
import { Button } from '../ui/Button'
import { ConfidenceChip, ProposedChip } from '../ui/Chip'

interface BatchReviewPanelProps {
  batch: IngestBatch
  onCommit: () => void
  committing: boolean
  commitError: string
  /** Called with the server's updated batch after a manual entity/link add
   * so the caller's batch state (and this panel's tables) stay in sync. */
  onBatchUpdated: (batch: IngestBatch) => void
  /** Extra button(s) rendered before Commit, e.g. IngestDialog's "Start
   * over" — DocumentReviewDialog has no equivalent and omits this. */
  extraActions?: ReactNode
}

/** Entities/links/rejected review table + commit button — shared between
 * IngestDialog (paste-text flow, batch held in local state right after
 * extraction) and DocumentReviewDialog (upload flow, batch fetched by id
 * from a background-produced IngestBatch). This component only renders
 * and reports commit/add intent via callbacks; it never fetches or
 * resets on its own. */
export function BatchReviewPanel({
  batch,
  onCommit,
  committing,
  commitError,
  onBatchUpdated,
  extraActions,
}: BatchReviewPanelProps) {
  const [addTab, setAddTab] = useState<'entity' | 'link' | null>(null)

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-[var(--color-text-muted)]">
        Proposed from this text — review before committing to the graph.
      </p>

      <ResultTable
        title={`Entities (${batch.entities.length})`}
        empty="No entities extracted."
        action={<Button onClick={() => setAddTab('entity')}>+ Add entity</Button>}
      >
        {batch.entities.map((e) => (
          <tr key={e.entity_id} className="border-b border-[var(--color-border)]">
            <td className="py-1.5 pe-3">{e.label}</td>
            <td className="py-1.5 pe-3 font-mono text-xs text-[var(--color-text-muted)]">
              {e.entity_subclass}
            </td>
            <td className="py-1.5 pe-3">
              <ConfidenceChip code={e.confidence} />
            </td>
            <td className="py-1.5 pe-3">
              <ProposedChip />
            </td>
          </tr>
        ))}
      </ResultTable>

      <ResultTable
        title={`Links (${batch.links.length})`}
        empty="No links extracted."
        action={<Button onClick={() => setAddTab('link')}>+ Add link</Button>}
      >
        {batch.links.map((l) => (
          <tr key={l.link_id} className="border-b border-[var(--color-border)]">
            <td className="py-1.5 pe-3 font-mono text-xs">{l.link_type}</td>
            <td className="py-1.5 pe-3 font-mono text-xs">{l.source_entity}</td>
            <td className="py-1.5 pe-3 font-mono text-xs">{l.target_entity}</td>
            <td className="py-1.5 pe-3">
              <ConfidenceChip code={l.confidence} />
            </td>
          </tr>
        ))}
      </ResultTable>

      {(batch.rejected_entities.length > 0 || batch.rejected_links.length > 0) && (
        <div className="rounded-md border border-[var(--color-border)] p-3">
          <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
            Rejected ({batch.rejected_entities.length + batch.rejected_links.length})
          </h3>
          <ul className="flex flex-col gap-1 text-xs text-[var(--color-text-muted)]">
            {[...batch.rejected_entities, ...batch.rejected_links].map((r, i) => (
              <li key={i}>{r.reason}</li>
            ))}
          </ul>
        </div>
      )}

      {commitError && (
        <p role="alert" className="text-sm text-[var(--color-status-destroyed)]">
          {commitError}
        </p>
      )}

      <div className="flex justify-end gap-2">
        {extraActions}
        <Button
          variant="primary"
          onClick={onCommit}
          disabled={(batch.entities.length === 0 && batch.links.length === 0) || committing}
        >
          {committing ? 'Committing…' : 'Commit to graph'}
        </Button>
      </div>

      {addTab && (
        <AddToBatchDialog
          open={addTab !== null}
          onOpenChange={(open) => !open && setAddTab(null)}
          batch={batch}
          defaultTab={addTab}
          onBatchUpdated={onBatchUpdated}
        />
      )}
    </div>
  )
}

function ResultTable({
  title,
  empty,
  action,
  children,
}: {
  title: string
  empty: string
  action?: ReactNode
  children: ReactNode
}) {
  const hasRows = Array.isArray(children) ? children.length > 0 : Boolean(children)
  return (
    <div>
      <div className="mb-1 flex items-center justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
          {title}
        </h3>
        {action}
      </div>
      {hasRows ? (
        <table className="w-full border-collapse text-sm">
          <tbody>{children}</tbody>
        </table>
      ) : (
        <p className="text-xs text-[var(--color-text-muted)]">{empty}</p>
      )}
    </div>
  )
}
