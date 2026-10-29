import * as Dialog from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, ApiError } from '../../lib/api'
import { describeKept, describeRemoval, nothingToRemove } from '../../lib/removal'
import type { Document, RemovalPreview } from '../../lib/types'
import { Button } from '../ui/Button'

interface DeleteSourceDialogProps {
  document: Document | null
  onOpenChange: (open: boolean) => void
  /** Called after the source is gone so the list can refresh. */
  onDeleted: () => void
}

type Mode = 'source' | 'source_and_data'

/** Delete an uploaded source: either just the source (what it imported stays
 * in the graph) or the source AND everything it created - entities, links and
 * the ontology terms its extraction added. Shows a read-only preview of the
 * latter before anything is removed. */
export function DeleteSourceDialog({ document, onOpenChange, onDeleted }: DeleteSourceDialogProps) {
  const [mode, setMode] = useState<Mode>('source')
  const [preview, setPreview] = useState<RemovalPreview | null>(null)
  const [previewError, setPreviewError] = useState('')
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState('')

  const documentId = document?.document_id
  useEffect(() => {
    if (!documentId) return
    let cancelled = false
    setMode('source')
    setPreview(null)
    setPreviewError('')
    setError('')
    api
      .getRemovalPreview(documentId)
      .then((p) => !cancelled && setPreview(p))
      .catch(
        (err) =>
          !cancelled && setPreviewError(err instanceof ApiError ? err.message : 'Failed to reach the backend'),
      )
    return () => {
      cancelled = true
    }
  }, [documentId])

  async function handleDelete() {
    if (!document) return
    setDeleting(true)
    setError('')
    try {
      await api.deleteDocument(document.document_id, mode === 'source_and_data')
      onDeleted()
      onOpenChange(false)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to reach the backend')
    } finally {
      setDeleting(false)
    }
  }

  const removing = preview ? describeRemoval(preview) : []
  const kept = preview ? describeKept(preview) : []
  const dataChoiceDisabled = !preview
  const destructive = mode === 'source_and_data'

  return (
    <Dialog.Root open={document !== null} onOpenChange={(o) => !deleting && onOpenChange(o)}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/60" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 max-h-[85vh] w-[520px] max-w-[92vw] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-1)] p-5 shadow-2xl outline-none">
          <div className="mb-3 flex items-center justify-between">
            <Dialog.Title className="text-sm font-semibold text-[var(--color-text-primary)]">
              Delete “{document?.filename}”
            </Dialog.Title>
            <Dialog.Close asChild>
              <button
                type="button"
                aria-label="Close"
                disabled={deleting}
                className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
              >
                <X size={16} />
              </button>
            </Dialog.Close>
          </div>
          <Dialog.Description className="mb-3 text-sm text-[var(--color-text-muted)]">
            Choose what happens to the data this document added to the graph.
          </Dialog.Description>

          <fieldset className="flex flex-col gap-2 border-0 p-0">
            <legend className="sr-only">What to delete</legend>

            <label className="flex cursor-pointer items-start gap-2 rounded-md border border-[var(--color-border)] p-3">
              <input
                type="radio"
                name="delete-mode"
                checked={mode === 'source'}
                onChange={() => setMode('source')}
                className="mt-0.5"
              />
              <span className="text-sm">
                <span className="block font-medium text-[var(--color-text-primary)]">Remove the source only</span>
                <span className="text-xs text-[var(--color-text-muted)]">
                  Deletes the file and its review batch. Everything it already added to the graph stays,
                  as standalone data.
                </span>
              </span>
            </label>

            <label
              className={`flex items-start gap-2 rounded-md border p-3 ${
                dataChoiceDisabled ? 'opacity-60' : 'cursor-pointer'
              } ${destructive ? 'border-[var(--color-status-destroyed)]' : 'border-[var(--color-border)]'}`}
            >
              <input
                type="radio"
                name="delete-mode"
                checked={destructive}
                disabled={dataChoiceDisabled}
                onChange={() => setMode('source_and_data')}
                className="mt-0.5"
              />
              <span className="text-sm">
                <span className="block font-medium text-[var(--color-text-primary)]">
                  Remove the source and its imported data
                </span>
                <span className="text-xs text-[var(--color-text-muted)]">
                  Also removes what it created: entities and links in the graph, and ontology classes and
                  link types the AI added for it. This can’t be undone.
                </span>
              </span>
            </label>
          </fieldset>

          <div className="mt-3 min-h-10 text-xs" aria-live="polite">
            {!preview && !previewError && (
              <p className="text-[var(--color-text-muted)]">Checking what this document added…</p>
            )}
            {previewError && (
              <p role="alert" className="text-[var(--color-status-destroyed)]">
                Couldn’t check what would be removed: {previewError}
              </p>
            )}
            {preview && destructive && (
              <div className="flex flex-col gap-1.5 rounded-md border border-[var(--color-border)] p-2.5">
                {nothingToRemove(preview) ? (
                  <p className="text-[var(--color-text-muted)]">
                    Nothing in the graph can be traced back to this document, so only the source would be
                    removed.
                  </p>
                ) : (
                  <p className="text-[var(--color-text-primary)]">Will remove {removing.join(', ')}.</p>
                )}
                {preview.entities.sample.length > 0 && (
                  <p className="text-[var(--color-text-muted)]">
                    Including {preview.entities.sample.join(', ')}
                    {preview.entities.remove > preview.entities.sample.length ? ' …' : ''}
                  </p>
                )}
                {preview.entities.untagged_import > 0 && (
                  <p className="text-[var(--color-text-muted)]">
                    {preview.entities.untagged_import} of these were found through this import’s record
                    (added before source tracking existed).
                  </p>
                )}
                {kept.map((line) => (
                  <p key={line} className="text-[var(--color-text-muted)]">
                    {line}
                  </p>
                ))}
              </div>
            )}
          </div>

          {error && (
            <p role="alert" className="mt-2 text-sm text-[var(--color-status-destroyed)]">
              {error}
            </p>
          )}

          <div className="mt-4 flex justify-end gap-2">
            <Button onClick={() => onOpenChange(false)} disabled={deleting}>
              Cancel
            </Button>
            <button
              type="button"
              onClick={() => void handleDelete()}
              disabled={deleting}
              className="inline-flex items-center gap-2 rounded-md bg-[var(--color-status-destroyed)] px-3 py-1.5 text-sm font-medium text-[var(--color-text-inverse)] hover:brightness-110 disabled:pointer-events-none disabled:opacity-50"
            >
              {deleting ? 'Deleting…' : destructive ? 'Delete source and data' : 'Delete source'}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
