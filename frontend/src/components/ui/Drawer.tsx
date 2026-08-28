import * as Dialog from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import type { ReactNode } from 'react'

interface DrawerProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  children: ReactNode
}

/** Right inspector drawer: slides in, never covers the canvas (fixed width). */
export function Drawer({ open, onOpenChange, title, children }: DrawerProps) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange} modal={false}>
      <Dialog.Portal>
        <Dialog.Content
          className="fixed inset-y-0 right-0 z-40 w-[380px] max-w-[90vw] border-l border-[var(--color-border)] bg-[var(--color-surface-1)] p-4 shadow-2xl outline-none data-[state=open]:animate-[slide-in_var(--duration-base)_var(--ease-standard)]"
          onInteractOutside={(e) => e.preventDefault()}
        >
          <div className="mb-4 flex items-center justify-between">
            <Dialog.Title className="text-sm font-semibold text-[var(--color-text-primary)]">
              {title}
            </Dialog.Title>
            <Dialog.Close asChild>
              <button
                type="button"
                className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
                aria-label="Close inspector"
              >
                <X size={16} />
              </button>
            </Dialog.Close>
          </div>
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
