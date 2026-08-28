import { AlertTriangle, Search } from 'lucide-react'
import { Button } from '../ui/Button'

export function EmptyState() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
      <Search size={28} className="text-[var(--color-text-muted)]" />
      <p className="text-sm text-[var(--color-text-muted)]">
        Pick a trigger entity in the left rail to scope its subgraph.
      </p>
    </div>
  )
}

export function LoadingState() {
  return (
    <div className="flex h-full items-center justify-center" role="status" aria-label="Scoping subgraph">
      <div className="grid grid-cols-3 gap-8 opacity-40">
        {Array.from({ length: 6 }).map((_, i) => (
          <div
            key={i}
            className="h-16 w-16 animate-pulse rounded-full border border-[var(--color-border-strong)] bg-[var(--color-surface-2)]"
          />
        ))}
      </div>
    </div>
  )
}

export function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 text-center" role="alert">
      <AlertTriangle size={28} className="text-[var(--color-status-destroyed)]" />
      <p className="max-w-sm text-sm text-[var(--color-text-muted)]">{message}</p>
      <Button variant="primary" onClick={onRetry}>
        Retry
      </Button>
    </div>
  )
}
