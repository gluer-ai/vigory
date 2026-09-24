import { useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { api, ApiError } from '../../lib/api'
import type { ClassDef, EntityCreateInput, IngestBatch, LinkCreateInput } from '../../lib/types'
import { AddToBatchDialog } from './AddToBatchDialog'
import { Button } from '../ui/Button'
import { ConfidenceChip, ProposedChip } from '../ui/Chip'
import { Select } from '../ui/Select'

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

function str(value: unknown, fallback = ''): string {
  return typeof value === 'string' && value.trim() ? value : fallback
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
  const [prefillLink, setPrefillLink] = useState<Partial<LinkCreateInput> | null>(null)

  const [classes, setClasses] = useState<ClassDef[]>([])
  const [subclassByRow, setSubclassByRow] = useState<Record<number, string>>({})
  const [includedRows, setIncludedRows] = useState<Record<number, boolean>>({})
  const [rowErrors, setRowErrors] = useState<Record<number, string>>({})
  const [bulkCreating, setBulkCreating] = useState(false)

  useEffect(() => {
    api.getClasses().then(setClasses).catch(() => setClasses([]))
  }, [])

  // Leaf classes only — same "no other ClassDef subclasses it" rule the
  // extraction prompt itself uses, so the picker only ever offers a real,
  // directly-assignable key (not a category heading like "PERSON").
  const leafClassOptions = useMemo(() => {
    const parentKeys = new Set(classes.map((c) => c.parent_key).filter((k): k is string => k !== null))
    return classes.filter((c) => !parentKeys.has(c.key)).map((c) => ({ value: c.key, label: c.key }))
  }, [classes])

  // A rejected row's own id already existing in the (now-updated) batch
  // means it was already fixed — via bulk-create below or a prior attempt
  // — so drop it from view instead of showing a stale rejection forever.
  const rejectedEntities = batch.rejected_entities
    .map((r, idx) => ({ ...r, idx }))
    .filter(({ row }) => !batch.entities.some((e) => e.entity_id === str(row.entity_id)))
  const rejectedLinks = batch.rejected_links
    .map((r, idx) => ({ ...r, idx }))
    .filter(({ row }) => !batch.links.some((l) => l.link_id === str(row.link_id)))

  const readyToCreateCount = rejectedEntities.filter(
    ({ idx }) => (includedRows[idx] ?? true) && subclassByRow[idx],
  ).length

  async function handleBulkCreate() {
    setBulkCreating(true)
    const newErrors: Record<number, string> = {}
    for (const { row, idx } of rejectedEntities) {
      if (!(includedRows[idx] ?? true)) continue
      const subclass = subclassByRow[idx]
      if (!subclass) continue

      const rawAliases = row.aliases
      const aliases = Array.isArray(rawAliases)
        ? rawAliases.filter((a): a is string => typeof a === 'string')
        : []
      const payload: EntityCreateInput = {
        entity_id: str(row.entity_id) || `E-${idx}-${Date.now()}`,
        entity_class: subclass.split('.')[0],
        entity_subclass: subclass,
        label: str(row.label) || str(row.entity_id) || 'Unnamed',
        aliases,
        status: 'active',
        confidence: str(row.confidence) || 'C3',
        source_ref: str(row.source_ref),
        attrs: {},
      }
      try {
        const updated = await api.addBatchEntity(batch.batch_id, payload)
        onBatchUpdated(updated)
      } catch (err) {
        newErrors[idx] = err instanceof ApiError ? err.message : 'Failed to reach the backend'
      }
    }
    setRowErrors(newErrors)
    setBulkCreating(false)
  }

  function openAddLink(prefill: Partial<LinkCreateInput> | null) {
    setPrefillLink(prefill)
    setAddTab('link')
  }

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
        action={<Button onClick={() => openAddLink(null)}>+ Add link</Button>}
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

      {rejectedEntities.length > 0 && (
        <div className="rounded-md border border-[var(--color-border)] p-3">
          <div className="mb-2 flex items-center justify-between gap-2">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
              Rejected entities ({rejectedEntities.length})
            </h3>
            <Button
              variant="primary"
              onClick={handleBulkCreate}
              disabled={bulkCreating || readyToCreateCount === 0}
            >
              {bulkCreating ? 'Creating…' : `Create ${readyToCreateCount} ${readyToCreateCount === 1 ? 'entity' : 'entities'}`}
            </Button>
          </div>
          <table className="w-full border-collapse text-sm">
            <tbody>
              {rejectedEntities.map(({ row, reason, idx }) => {
                const rowLabel = str(row.label) || str(row.entity_id) || `row ${idx + 1}`
                return (
                  <tr key={idx} className="border-b border-[var(--color-border)] align-top">
                    <td className="w-6 py-1.5 pe-2">
                      <input
                        type="checkbox"
                        checked={includedRows[idx] ?? true}
                        onChange={(e) => setIncludedRows((prev) => ({ ...prev, [idx]: e.target.checked }))}
                        aria-label={`Include ${rowLabel}`}
                      />
                    </td>
                    <td className="py-1.5 pe-3">
                      <div className="text-[var(--color-text-primary)]">{rowLabel}</div>
                      <div className="text-xs text-[var(--color-text-muted)]">{reason}</div>
                      {rowErrors[idx] && (
                        <div className="text-xs text-[var(--color-status-destroyed)]">{rowErrors[idx]}</div>
                      )}
                    </td>
                    <td className="w-56 py-1.5">
                      <Select
                        value={subclassByRow[idx] ?? ''}
                        onValueChange={(v) => setSubclassByRow((prev) => ({ ...prev, [idx]: v }))}
                        options={leafClassOptions}
                        placeholder={leafClassOptions.length ? 'Choose a subclass…' : 'Loading…'}
                        aria-label={`Subclass for ${rowLabel}`}
                      />
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {rejectedLinks.length > 0 && (
        <div className="rounded-md border border-[var(--color-border)] p-3">
          <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
            Rejected links ({rejectedLinks.length})
          </h3>
          <ul className="flex flex-col gap-1 text-xs text-[var(--color-text-muted)]">
            {rejectedLinks.map(({ row, reason, idx }) => (
              <li key={idx} className="flex items-center justify-between gap-2">
                <span>{reason}</span>
                <Button
                  onClick={() =>
                    openAddLink({
                      link_id: str(row.link_id) || undefined,
                      link_type: str(row.link_type) || undefined,
                      source_entity: str(row.source_entity) || undefined,
                      target_entity: str(row.target_entity) || undefined,
                      confidence: str(row.confidence) || undefined,
                      source_ref: str(row.source_ref) || undefined,
                    })
                  }
                >
                  Create…
                </Button>
              </li>
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
          initialLink={addTab === 'link' ? (prefillLink ?? undefined) : undefined}
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
