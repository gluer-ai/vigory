import { confidenceLevel } from '../../lib/entityClass'

/** Solid coded chip for confidence/status — never a tint-on-tint badge (DESIGN.md). */
export function ConfidenceChip({ code }: { code: string }) {
  const level = confidenceLevel(code)
  const color = `var(--color-confidence-${level})`
  return (
    <span
      className="inline-flex items-center rounded px-1.5 py-0.5 font-mono text-xs font-medium text-[var(--color-text-inverse)]"
      style={{ background: color }}
      title={`confidence ${code}`}
    >
      {code}
    </span>
  )
}

export function StatusChip({ status }: { status: string }) {
  const color = `var(--color-status-${status})` || 'var(--color-status-unknown)'
  return (
    <span
      className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium text-[var(--color-text-inverse)]"
      style={{ background: color }}
    >
      {status}
    </span>
  )
}

/** Dashed outline treatment for LLM-proposed/unconfirmed entities (open-world rule). */
export function ProposedChip() {
  return (
    <span className="inline-flex items-center rounded border border-dashed border-[var(--color-border-strong)] px-1.5 py-0.5 text-xs font-medium text-[var(--color-text-muted)]">
      proposed
    </span>
  )
}
