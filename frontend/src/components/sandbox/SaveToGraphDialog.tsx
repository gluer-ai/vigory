import * as Dialog from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { api, ApiError } from '../../lib/api'
import type { SandboxProposal } from '../../lib/types'
import { proposalToGraph } from '../../lib/sphereGraph'
import { BatchReviewPanel } from '../layout/BatchReviewPanel'
import { Button } from '../ui/Button'
import { SphereView } from './SphereView'

interface SaveToGraphDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  sandboxId: string
  sandboxName: string
}

type Phase = 'loading' | 'error' | 'review' | 'committing' | 'committed'
type View = 'graph' | 'list'

/** Review what this sandbox would add to the REAL knowledge graph, then commit it.
 * The server builds a proposal from the saved sandbox (new entities and new links only,
 * with fresh ids, never changing existing records); nothing is written until the
 * user commits here. */
export function SaveToGraphDialog({ open, onOpenChange, sandboxId, sandboxName }: SaveToGraphDialogProps) {
  const [phase, setPhase] = useState<Phase>('loading')
  const [proposal, setProposal] = useState<SandboxProposal | null>(null)
  const [error, setError] = useState('')
  const [commitError, setCommitError] = useState('')
  const [committedCounts, setCommittedCounts] = useState({ entities: 0, links: 0 })
  const [view, setView] = useState<View>('graph')

  // Build a fresh proposal every time the dialog opens.
  useEffect(() => {
    if (!open) return
    let cancelled = false
    setPhase('loading')
    setProposal(null)
    setError('')
    setCommitError('')
    api
      .proposeSandbox(sandboxId)
      .then((p) => {
        if (cancelled) return
        setProposal(p)
        setPhase('review')
      })
      .catch((err) => {
        if (cancelled) return
        setError(err instanceof ApiError ? err.message : 'Failed to reach the backend')
        setPhase('error')
      })
    return () => {
      cancelled = true
    }
  }, [open, sandboxId])

  async function commit() {
    if (!proposal) return
    setPhase('committing')
    setCommitError('')
    try {
      await api.commitBatch(proposal.batch.batch_id)
      setCommittedCounts({ entities: proposal.batch.entities.length, links: proposal.batch.links.length })
      setPhase('committed')
    } catch (err) {
      setCommitError(err instanceof ApiError ? err.message : 'Failed to reach the backend')
      setPhase('review')
    }
  }

  const batch = proposal?.batch
  const graph = useMemo(() => (proposal ? proposalToGraph(proposal) : null), [proposal])
  const nothing = !!batch && batch.entities.length === 0 && batch.links.length === 0

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/60" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 max-h-[88vh] w-[900px] max-w-[96vw] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-1)] p-5 shadow-2xl outline-none">
          <div className="mb-3 flex items-center justify-between">
            <Dialog.Title className="text-sm font-semibold text-[var(--color-text-primary)]">
              Save “{sandboxName}” to the knowledge graph
            </Dialog.Title>
            <Dialog.Close asChild>
              <button type="button" aria-label="Close" className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]">
                <X size={16} />
              </button>
            </Dialog.Close>
          </div>
          <Dialog.Description className="mb-3 text-xs text-[var(--color-text-muted)]">
            Only the new entities and new links from this sandbox are proposed, as new records. Existing
            records are never changed. Nothing is saved until you commit, and committed data is visible to
            everyone who uses the graph.
          </Dialog.Description>

          {phase === 'loading' && <p role="status" className="text-sm text-[var(--color-text-muted)]">Preparing the proposal…</p>}

          {phase === 'error' && (
            <p role="alert" className="text-sm text-[var(--color-status-destroyed)]">{error}</p>
          )}

          {(phase === 'review' || phase === 'committing') && proposal && batch && (
            <div className="flex flex-col gap-3">
              {proposal.merged.length > 0 && (
                <div className="rounded-md border border-[var(--color-border)] p-2 text-xs">
                  <p className="mb-1 font-medium text-[var(--color-text-primary)]">Matched to existing records (not duplicated)</p>
                  <ul className="flex flex-col gap-0.5 text-[var(--color-text-muted)]">
                    {proposal.merged.map((m) => (
                      <li key={m.into_id}>
                        “{m.label}” → existing “{m.into_label}” <span className="font-mono">({m.into_id})</span>; its links are attached to the existing record.
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {(proposal.skipped.length > 0 || proposal.notes.length > 0) && (
                <div className="rounded-md border border-[var(--color-border)] p-2 text-xs text-[var(--color-text-muted)]">
                  <p className="mb-1 font-medium text-[var(--color-text-primary)]">Left out</p>
                  <ul className="flex flex-col gap-0.5">
                    {proposal.skipped.map((s) => (
                      <li key={s.reason}>{s.count} × {s.reason}</li>
                    ))}
                    {proposal.notes.map((n) => (
                      <li key={n}>{n}</li>
                    ))}
                  </ul>
                </div>
              )}
              {!nothing && (
                <div role="tablist" aria-label="How to show the proposal" className="flex gap-1">
                  {(['graph', 'list'] as const).map((v) => (
                    <button
                      key={v}
                      type="button"
                      role="tab"
                      aria-selected={view === v}
                      onClick={() => setView(v)}
                      className={`rounded-md border px-3 py-1 text-xs ${
                        view === v
                          ? 'border-[var(--color-focus)] text-[var(--color-text-primary)]'
                          : 'border-[var(--color-border)] text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)]'
                      }`}
                    >
                      {v === 'graph' ? 'Graph' : 'List'}
                    </button>
                  ))}
                </div>
              )}
              {!nothing && view === 'graph' && graph && (
                <div className="flex flex-col gap-2">
                  <SphereView data={graph} />
                  <p className="text-xs text-[var(--color-text-muted)]">
                    Dashed spheres will be created; solid ones already exist. Bigger spheres have more links.
                    Click one to highlight its connections, drag to rearrange, scroll to zoom.
                  </p>
                  {(batch.rejected_entities.length > 0 || batch.rejected_links.length > 0) && (
                    <p className="text-xs text-[var(--color-text-muted)]">
                      {batch.rejected_entities.length + batch.rejected_links.length} item(s) did not fit the ontology
                      and are not drawn. Switch to the List view to fix them.
                    </p>
                  )}
                  {commitError && <p role="alert" className="text-sm text-[var(--color-status-destroyed)]">{commitError}</p>}
                  <div className="flex justify-end">
                    <Button
                      variant="primary"
                      onClick={commit}
                      disabled={phase === 'committing' || (batch.entities.length === 0 && batch.links.length === 0)}
                    >
                      {phase === 'committing' ? 'Committing…' : 'Commit to graph'}
                    </Button>
                  </div>
                </div>
              )}
              {nothing ? (
                <p className="text-sm text-[var(--color-text-primary)]">
                  Nothing new to save: this sandbox has no new entities or links that are not already in the graph.
                </p>
              ) : view === 'list' && (
                <BatchReviewPanel
                  batch={batch}
                  onCommit={commit}
                  committing={phase === 'committing'}
                  commitError={commitError}
                  onBatchUpdated={(b) => setProposal((p) => (p ? { ...p, batch: b } : p))}
                />
              )}
            </div>
          )}

          {phase === 'committed' && (
            <div className="flex flex-col gap-3">
              <p role="status" className="text-sm text-[var(--color-text-primary)]">
                Saved {committedCounts.entities} {committedCounts.entities === 1 ? 'entity' : 'entities'} and{' '}
                {committedCounts.links} {committedCounts.links === 1 ? 'link' : 'links'} to the knowledge graph.
              </p>
              <p className="text-xs text-[var(--color-text-muted)]">
                Your sandbox is unchanged. Click an entity in it to follow its connections from the graph.
              </p>
              <Button variant="primary" onClick={() => onOpenChange(false)}>Done</Button>
            </div>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
