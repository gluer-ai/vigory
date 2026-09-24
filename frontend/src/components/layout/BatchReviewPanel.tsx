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

type ClassesStatus = 'loading' | 'ready' | 'error'

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
  const [classesStatus, setClassesStatus] = useState<ClassesStatus>('loading')
  const [subclassByRow, setSubclassByRow] = useState<Record<number, string>>({})
  const [includedRows, setIncludedRows] = useState<Record<number, boolean>>({})
  const [rowErrors, setRowErrors] = useState<Record<number, string>>({})
  const [bulkCreating, setBulkCreating] = useState(false)
  const [classifying, setClassifying] = useState(false)
  const [entitySuccessMessage, setEntitySuccessMessage] = useState('')

  const [linkIncludedRows, setLinkIncludedRows] = useState<Record<number, boolean>>({})
  const [linkRowErrors, setLinkRowErrors] = useState<Record<number, string>>({})
  const [bulkLinkCreating, setBulkLinkCreating] = useState(false)
  const [linkSuccessMessage, setLinkSuccessMessage] = useState('')

  function loadClasses() {
    setClassesStatus('loading')
    api
      .getClasses()
      .then((data) => {
        setClasses(data)
        setClassesStatus('ready')
      })
      .catch(() => setClassesStatus('error'))
  }

  useEffect(() => {
    loadClasses()
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

  // Included, not "included AND already picked" — a row without a manual
  // pick still gets created: handleBulkCreate auto-classifies anything
  // left blank as part of the same click, so this reflects what one click
  // on "Create N entities" will actually attempt.
  const includedEntityCount = rejectedEntities.filter(({ idx }) => includedRows[idx] ?? true).length
  const allEntitiesIncluded = rejectedEntities.length > 0 && rejectedEntities.every(({ idx }) => includedRows[idx] ?? true)

  const readyToCreateLinkCount = rejectedLinks.filter(({ idx }) => linkIncludedRows[idx] ?? true).length
  const allLinksIncluded = rejectedLinks.length > 0 && rejectedLinks.every(({ idx }) => linkIncludedRows[idx] ?? true)

  function toggleAllEntities(checked: boolean) {
    setIncludedRows((prev) => {
      const next = { ...prev }
      for (const { idx } of rejectedEntities) next[idx] = checked
      return next
    })
  }

  function toggleAllLinks(checked: boolean) {
    setLinkIncludedRows((prev) => {
      const next = { ...prev }
      for (const { idx } of rejectedLinks) next[idx] = checked
      return next
    })
  }

  async function handleBulkCreate() {
    // Auto-classify anything the reviewer didn't pick manually, in one LLM
    // call for the whole set, so "Create N entities" is a single click
    // rather than requiring a subclass pick per row first. A manual pick
    // (subclassByRow already set) is always respected over the guess.
    let picks = subclassByRow
    const needsClassification = rejectedEntities.some(
      ({ idx }) => (includedRows[idx] ?? true) && !subclassByRow[idx],
    )
    if (needsClassification) {
      setClassifying(true)
      try {
        const { classifications } = await api.classifyRejectedEntities(batch.batch_id)
        const guessByIdx = new Map(classifications.map((c) => [c.idx, c.entity_subclass]))
        const merged = { ...subclassByRow }
        for (const { idx } of rejectedEntities) {
          const guess = guessByIdx.get(idx)
          if (guess && !merged[idx]) merged[idx] = guess
        }
        picks = merged
        setSubclassByRow(merged)
      } catch {
        // Fail open — rows that still have no pick (manual or guessed) are
        // simply skipped below and stay in the Rejected list, same as if
        // classification had never been attempted.
      }
      setClassifying(false)
    }

    setBulkCreating(true)
    setEntitySuccessMessage('')
    const newErrors: Record<number, string> = {}
    let attempted = 0
    let succeeded = 0
    for (const { row, idx } of rejectedEntities) {
      if (!(includedRows[idx] ?? true)) continue
      const subclass = picks[idx]
      if (!subclass) continue
      attempted += 1

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
        succeeded += 1
      } catch (err) {
        newErrors[idx] = err instanceof ApiError ? err.message : 'Failed to reach the backend'
      }
    }
    setRowErrors(newErrors)
    setBulkCreating(false)
    if (attempted > 0) {
      setEntitySuccessMessage(
        succeeded === attempted
          ? `✓ Created ${succeeded} ${succeeded === 1 ? 'entity' : 'entities'} successfully.`
          : `Created ${succeeded} of ${attempted} entities — ${attempted - succeeded} failed, see details below.`,
      )
    } else {
      setEntitySuccessMessage("Nothing to create — check a row and pick (or wait for auto-classify to find) a subclass.")
    }
  }

  // Resubmits a rejected link row as-is (no field editing) — the common
  // case is the link_type/endpoints were always fine and the row was only
  // rejected because its entities didn't exist yet, which bulk-creating
  // entities above just fixed. A row that fails here (e.g. link_type
  // itself was invalid) still has the per-row "Create…" button to edit it.
  async function handleBulkCreateLinks() {
    setBulkLinkCreating(true)
    setLinkSuccessMessage('')
    const newErrors: Record<number, string> = {}
    let attempted = 0
    let succeeded = 0
    for (const { row, idx } of rejectedLinks) {
      if (!(linkIncludedRows[idx] ?? true)) continue
      attempted += 1
      const payload: LinkCreateInput = {
        link_id: str(row.link_id) || `L-${idx}-${Date.now()}`,
        link_type: str(row.link_type),
        source_entity: str(row.source_entity),
        target_entity: str(row.target_entity),
        direction: row.direction === 'symmetric' ? 'symmetric' : 'directed',
        assertion_status: 'reported',
        confidence: str(row.confidence) || 'C3',
        source_ref: str(row.source_ref),
        attrs: {},
      }
      try {
        const updated = await api.addBatchLink(batch.batch_id, payload)
        onBatchUpdated(updated)
        succeeded += 1
      } catch (err) {
        newErrors[idx] = err instanceof ApiError ? err.message : 'Failed to reach the backend'
      }
    }
    setLinkRowErrors(newErrors)
    setBulkLinkCreating(false)
    if (attempted > 0) {
      setLinkSuccessMessage(
        succeeded === attempted
          ? `✓ Created ${succeeded} ${succeeded === 1 ? 'link' : 'links'} successfully.`
          : `Created ${succeeded} of ${attempted} links — ${attempted - succeeded} failed, see details below.`,
      )
    }
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
            <div className="flex items-center gap-3">
              <label className="flex items-center gap-1.5 text-xs text-[var(--color-text-muted)]">
                <input
                  type="checkbox"
                  checked={allEntitiesIncluded}
                  onChange={(e) => toggleAllEntities(e.target.checked)}
                />
                Select all
              </label>
              <Button
                variant="primary"
                onClick={handleBulkCreate}
                disabled={bulkCreating || classifying || includedEntityCount === 0}
              >
                {classifying
                  ? 'Classifying…'
                  : bulkCreating
                    ? 'Creating…'
                    : `Create ${includedEntityCount} ${includedEntityCount === 1 ? 'entity' : 'entities'}`}
              </Button>
            </div>
          </div>

          {classesStatus === 'error' && (
            <p role="alert" className="mb-2 flex items-center gap-2 text-xs text-[var(--color-status-destroyed)]">
              Couldn't load the entity ontology, so subclasses can't be picked.
              <Button onClick={loadClasses}>Retry</Button>
            </p>
          )}

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
                        placeholder={
                          classesStatus === 'loading'
                            ? 'Loading…'
                            : classesStatus === 'error'
                              ? 'Unavailable'
                              : 'Choose a subclass…'
                        }
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
          <div className="mb-2 flex items-center justify-between gap-2">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
              Rejected links ({rejectedLinks.length})
            </h3>
            <div className="flex items-center gap-3">
              <label className="flex items-center gap-1.5 text-xs text-[var(--color-text-muted)]">
                <input
                  type="checkbox"
                  checked={allLinksIncluded}
                  onChange={(e) => toggleAllLinks(e.target.checked)}
                />
                Select all
              </label>
              <Button
                variant="primary"
                onClick={handleBulkCreateLinks}
                disabled={bulkLinkCreating || readyToCreateLinkCount === 0}
              >
                {bulkLinkCreating
                  ? 'Creating…'
                  : `Create ${readyToCreateLinkCount} ${readyToCreateLinkCount === 1 ? 'link' : 'links'}`}
              </Button>
            </div>
          </div>
          <table className="w-full border-collapse text-sm">
            <tbody>
              {rejectedLinks.map(({ row, reason, idx }) => (
                <tr key={idx} className="border-b border-[var(--color-border)] align-top">
                  <td className="w-6 py-1.5 pe-2">
                    <input
                      type="checkbox"
                      checked={linkIncludedRows[idx] ?? true}
                      onChange={(e) => setLinkIncludedRows((prev) => ({ ...prev, [idx]: e.target.checked }))}
                      aria-label={`Include link row ${idx + 1}`}
                    />
                  </td>
                  <td className="py-1.5 pe-3">
                    <div className="text-xs text-[var(--color-text-muted)]">{reason}</div>
                    {linkRowErrors[idx] && (
                      <div className="text-xs text-[var(--color-status-destroyed)]">{linkRowErrors[idx]}</div>
                    )}
                  </td>
                  <td className="w-24 py-1.5 text-end">
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
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {entitySuccessMessage && (
        <p className="text-sm text-[var(--color-text-primary)]">{entitySuccessMessage}</p>
      )}

      {linkSuccessMessage && (
        <p className="text-sm text-[var(--color-text-primary)]">{linkSuccessMessage}</p>
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
