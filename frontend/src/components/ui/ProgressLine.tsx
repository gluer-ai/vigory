import { describeStage, formatElapsed, progressPercent } from '../../lib/llmProgress'

interface ProgressLineProps {
  stage?: string | null
  current?: number | null
  total?: number | null
  /** Seconds since the job started, when known. */
  elapsedSeconds?: number
}

/** Live status of a long-running model job: stage text, optional elapsed time
 * and a bar that is determinate only when the part count is known. Announced
 * politely to screen readers as the stage changes. */
export function ProgressLine({ stage, current, total, elapsedSeconds }: ProgressLineProps) {
  const percent = progressPercent(current, total)
  return (
    <div className="flex flex-col gap-1.5" role="status" aria-live="polite">
      <div className="flex items-baseline justify-between gap-3 text-xs text-[var(--color-text-muted)]">
        <span>{describeStage(stage, current, total)}…</span>
        {elapsedSeconds !== undefined && <span className="tabular-nums">{formatElapsed(elapsedSeconds)}</span>}
      </div>
      <div
        className="h-1 overflow-hidden rounded bg-[var(--color-surface-hover)]"
        role="progressbar"
        aria-label="Extraction progress"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent ?? undefined}
      >
        <div
          className={`h-full rounded bg-[var(--color-focus)] ${percent === null ? 'w-1/3 animate-pulse' : 'transition-[width]'}`}
          style={percent === null ? undefined : { width: `${Math.max(percent, 4)}%` }}
        />
      </div>
    </div>
  )
}
