import * as Dialog from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, ApiError } from '../../lib/api'
import type { IngestBatch } from '../../lib/types'
import { BatchReviewPanel } from '../layout/BatchReviewPanel'
import { Button } from '../ui/Button'

interface DocumentReviewDialogProps {
  open: boolean
  batchId: string | null
  onOpenChange: (open: boolean) => void
  /** Called after a successful commit so the caller's document list can
   * pick up the new 'committed' status on its next poll. */
  onCommitted: () => void
}

type Phase = 'loading' | 'ready' | 'committing' | 'committed' | 'error'

/** Review/commit a batch produced by background document extraction — the
 * knowledge-base counterpart to IngestDialog's review step, except the
 * batch is fetched by id (GET /ingest/{batch_id}) instead of held in local
 * state from a just-finished extraction call. */
export function DocumentReviewDialog({
  open,
  batchId,
  onOpenChange,
  onCommitted,
}: DocumentReviewDialogProps) {
  const [phase, setPhase] = useState<Phase>('loading')
  const [batch, setBatch] = useState<IngestBatch | null>(null)
  const [errorMessage, setErrorMessage] = useState('')
  const [commitError, setCommitError] = useState('')

  useEffect(() => {
    if (!open || !batchId) return
    let cancelled = false
    setPhase('loading')
    setBatch(null)
    setErrorMessage('')
    setCommitError('')
    api
      .getBatch(batchId)
      .then((result) => {
        if (!cancelled) {
          setBatch(result)
          setPhase('ready')
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setErrorMessage(err instanceof ApiError ? err.message : 'Failed to reach the backend')
          setPhase('error')
        }
      })
    return () => {
      cancelled = true
    }
  }, [open, batchId])

  async function handleCommit() {
    if (!batch) return
    setPhase('committing')
    setCommitError('')
    try {
      await api.commitBatch(batch.batch_id)
      setPhase('committed')
      onCommitted()
    } catch (err) {
      setCommitError(err instanceof ApiError ? err.message : 'Failed to reach the backend')
      setPhase('ready')
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/60" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 max-h-[85vh] w-[560px] max-w-[92vw] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-1)] p-5 shadow-2xl outline-none">
          <div className="mb-4 flex items-center justify-between">
            <Dialog.Title className="text-sm font-semibold text-[var(--color-text-primary)]">
              Review extracted document
            </Dialog.Title>
            <Dialog.Close asChild>
              <button
                type="button"
                aria-label="Close"
                className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
              >
                <X size={16} />
              </button>
            </Dialog.Close>
          </div>

          {phase === 'loading' && <p className="text-sm text-[var(--color-text-muted)]">Loading…</p>}

          {phase === 'error' && (
            <p role="alert" className="text-sm text-[var(--color-status-destroyed)]">
              {errorMessage}
            </p>
          )}

          {(phase === 'ready' || phase === 'committing') && batch && (
            <BatchReviewPanel
              batch={batch}
              onCommit={handleCommit}
              committing={phase === 'committing'}
              commitError={commitError}
              onBatchUpdated={setBatch}
            />
          )}

          {phase === 'committed' && (
            <div className="flex flex-col items-start gap-3">
              <p className="text-sm text-[var(--color-text-primary)]">
                Committed. Entities and links are now in the graph.
              </p>
              <Button variant="primary" onClick={() => onOpenChange(false)}>
                Close
              </Button>
            </div>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
