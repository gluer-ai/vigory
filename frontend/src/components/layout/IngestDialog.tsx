import * as Dialog from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import { useState } from 'react'
import { api, ApiError } from '../../lib/api'
import { SAMPLE_SCENARIOS } from '../../lib/sampleScenarios'
import type { IngestBatch } from '../../lib/types'
import { Button } from '../ui/Button'
import { BatchReviewPanel } from './BatchReviewPanel'

interface IngestDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Called after a successful commit so the caller can jump the scope view
   * to the newly created entities. */
  onCommitted: (firstEntityId: string) => void
}

type Phase = 'draft' | 'extracting' | 'review' | 'committing' | 'committed' | 'error'

/** Paste a scenario -> extract proposed entities/links (never auto-merged) ->
 * review -> commit. Mirrors the backend's "identity is a hypothesis" rule:
 * nothing lands in the graph until the analyst explicitly commits it. */
export function IngestDialog({ open, onOpenChange, onCommitted }: IngestDialogProps) {
  const [text, setText] = useState('')
  const [phase, setPhase] = useState<Phase>('draft')
  const [batch, setBatch] = useState<IngestBatch | null>(null)
  const [errorMessage, setErrorMessage] = useState('')
  const [commitError, setCommitError] = useState('')

  function reset() {
    setText('')
    setPhase('draft')
    setBatch(null)
    setErrorMessage('')
    setCommitError('')
  }

  function handleOpenChange(next: boolean) {
    if (!next) reset()
    onOpenChange(next)
  }

  async function handleExtract() {
    setPhase('extracting')
    try {
      const result = await api.ingestText(text)
      setBatch(result)
      setPhase('review')
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : 'Failed to reach the backend')
      setPhase('error')
    }
  }

  async function handleCommit() {
    if (!batch) return
    setPhase('committing')
    setCommitError('')
    try {
      await api.commitBatch(batch.batch_id)
      setPhase('committed')
      // A follow-up scenario can commit zero *new* entities (everything it
      // mentioned already existed) while still adding real links — fall
      // back to a link endpoint so the graph still jumps somewhere useful.
      const firstId = batch.entities[0]?.entity_id ?? batch.links[0]?.source_entity
      if (firstId) onCommitted(firstId)
    } catch (err) {
      setCommitError(err instanceof ApiError ? err.message : 'Failed to reach the backend')
      setPhase('review')
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={handleOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/60" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 max-h-[85vh] w-[560px] max-w-[92vw] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-1)] p-5 shadow-2xl outline-none">
          <div className="mb-4 flex items-center justify-between">
            <Dialog.Title className="text-sm font-semibold text-[var(--color-text-primary)]">
              Ingest scenario text
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

          {(phase === 'draft' || phase === 'extracting' || phase === 'error') && (
            <div className="flex flex-col gap-3">
              <label htmlFor="scenario-text" className="text-sm text-[var(--color-text-muted)]">
                Describe the scenario in free text. The extraction agent proposes entities and
                links for review — nothing is saved until you commit.
              </label>

              {phase === 'draft' && (
                <div className="flex flex-col gap-1.5">
                  <span className="text-xs text-[var(--color-text-muted)]">Try a sample:</span>
                  <div className="flex flex-wrap gap-1.5">
                    {SAMPLE_SCENARIOS.map((sample) => (
                      <button
                        key={sample.title}
                        type="button"
                        onClick={() => setText(sample.text)}
                        className="rounded-full border border-[var(--color-border)] px-2.5 py-1 text-xs text-[var(--color-text-muted)] hover:border-[var(--color-border-strong)] hover:text-[var(--color-text-primary)]"
                      >
                        {sample.title}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              <textarea
                id="scenario-text"
                value={text}
                onChange={(e) => setText(e.target.value)}
                rows={8}
                placeholder="e.g. Major Ivan Petrov commands the 3rd Motor Rifle Battalion, based near..."
                className="rounded-md border border-[var(--color-border)] bg-[var(--color-surface-0)] p-3 text-sm text-[var(--color-text-primary)] placeholder:text-[var(--color-text-muted)] focus-visible:border-[var(--color-focus)]"
              />
              {phase === 'error' && (
                <p role="alert" className="text-sm text-[var(--color-status-destroyed)]">
                  {errorMessage}
                </p>
              )}
              <Button
                variant="primary"
                onClick={handleExtract}
                disabled={!text.trim() || phase === 'extracting'}
              >
                {phase === 'extracting' ? 'Extracting…' : 'Extract entities & links'}
              </Button>
            </div>
          )}

          {(phase === 'review' || phase === 'committing') && batch && (
            <BatchReviewPanel
              batch={batch}
              onCommit={handleCommit}
              committing={phase === 'committing'}
              commitError={commitError}
              extraActions={
                <Button onClick={reset} disabled={phase === 'committing'}>
                  Start over
                </Button>
              }
            />
          )}

          {phase === 'committed' && (
            <div className="flex flex-col items-start gap-3">
              <p className="text-sm text-[var(--color-text-primary)]">
                Committed. Entities and links are now in the graph.
              </p>
              <Button variant="primary" onClick={() => handleOpenChange(false)}>
                View in graph
              </Button>
            </div>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
