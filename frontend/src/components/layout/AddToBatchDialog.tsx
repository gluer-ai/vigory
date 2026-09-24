import * as Dialog from '@radix-ui/react-dialog'
import * as Tabs from '@radix-ui/react-tabs'
import { X } from 'lucide-react'
import { api } from '../../lib/api'
import type { EntityCreateInput, IngestBatch, LinkCreateInput } from '../../lib/types'
import { EntityForm, LinkForm } from './AddResourceDialog'

interface AddToBatchDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  batch: IngestBatch
  defaultTab: 'entity' | 'link'
  onBatchUpdated: (batch: IngestBatch) => void
}

/** "+Add entity"/"+Add link" for a proposed IngestBatch under review —
 * reuses AddResourceDialog's forms, but targets the batch-scoped
 * POST /ingest/{id}/entities|links endpoints (staged, not committed) and
 * wires the entity form's synonym-suggestion step. */
export function AddToBatchDialog({ open, onOpenChange, batch, defaultTab, onBatchUpdated }: AddToBatchDialogProps) {
  function handleDone() {
    onOpenChange(false)
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/60" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 max-h-[85vh] w-[560px] max-w-[92vw] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-1)] p-5 shadow-2xl outline-none">
          <div className="mb-4 flex items-center justify-between">
            <Dialog.Title className="text-sm font-semibold text-[var(--color-text-primary)]">
              Add to batch
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

          <Tabs.Root defaultValue={defaultTab}>
            <Tabs.List className="mb-4 flex gap-1 border-b border-[var(--color-border)]" aria-label="Resource type">
              <Tabs.Trigger
                value="entity"
                className="border-b-2 border-transparent px-3 py-2 text-sm text-[var(--color-text-muted)] data-[state=active]:border-[var(--color-focus)] data-[state=active]:text-[var(--color-text-primary)]"
              >
                Entity
              </Tabs.Trigger>
              <Tabs.Trigger
                value="link"
                className="border-b-2 border-transparent px-3 py-2 text-sm text-[var(--color-text-muted)] data-[state=active]:border-[var(--color-focus)] data-[state=active]:text-[var(--color-text-primary)]"
              >
                Link
              </Tabs.Trigger>
            </Tabs.List>
            <Tabs.Content value="entity">
              <EntityForm
                onSubmit={(entity: EntityCreateInput) =>
                  api.addBatchEntity(batch.batch_id, entity).then((updated) => {
                    onBatchUpdated(updated)
                    return entity.entity_id
                  })
                }
                onCheckSynonym={(label, entityClass, aliases) =>
                  api
                    .suggestBatchEntity(batch.batch_id, label, entityClass, aliases)
                    .then((r) => (r.match ? { match: r.match, reason: r.reason ?? '' } : null))
                }
                onUseExisting={handleDone}
                onCreated={handleDone}
              />
            </Tabs.Content>
            <Tabs.Content value="link">
              <LinkForm
                onSubmit={(link: LinkCreateInput) =>
                  api.addBatchLink(batch.batch_id, link).then((updated) => {
                    onBatchUpdated(updated)
                    return link.source_entity
                  })
                }
                onCreated={handleDone}
                localEntities={batch.entities}
              />
            </Tabs.Content>
          </Tabs.Root>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
