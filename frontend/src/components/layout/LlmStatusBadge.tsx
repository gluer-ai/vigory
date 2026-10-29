import { useEffect, useState } from 'react'
import { api } from '../../lib/api'
import { describeLlm, type LlmBadge } from '../../lib/llmProgress'
import type { LlmStatus } from '../../lib/types'

const IDLE_POLL_MS = 5000
const BUSY_POLL_MS = 1500

const TONE_COLOR: Record<LlmBadge['tone'], string> = {
  ok: 'var(--color-status-active)',
  busy: 'var(--color-focus)',
  warn: 'var(--color-status-unknown)',
  error: 'var(--color-status-destroyed)',
}

/** Always-visible model status: ready / working (what, for how long) /
 * downloading / unreachable. Polls faster while a call is in flight and
 * pauses while the tab is hidden. */
export function LlmStatusBadge() {
  const [status, setStatus] = useState<LlmStatus | null>(null)

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined

    async function tick() {
      if (document.visibilityState === 'visible') {
        try {
          const next = await api.getLlmStatus()
          if (!cancelled) setStatus(next)
        } catch {
          if (!cancelled) setStatus(null)
        }
      }
      if (!cancelled) {
        timer = setTimeout(() => void tick(), status?.state === 'busy' ? BUSY_POLL_MS : IDLE_POLL_MS)
      }
    }
    void tick()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
    // Re-arm with the right cadence whenever busy/idle flips.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status?.state === 'busy'])

  const badge = describeLlm(status)
  return (
    <div
      role="status"
      aria-live="polite"
      title={badge.detail}
      className="flex flex-col gap-0.5 rounded-md border border-[var(--color-border)] px-2.5 py-1.5"
    >
      <span className="flex items-center gap-2 text-xs font-medium text-[var(--color-text-primary)]">
        <span
          aria-hidden="true"
          className={`inline-block size-2 shrink-0 rounded-full ${badge.tone === 'busy' ? 'animate-pulse' : ''}`}
          style={{ background: TONE_COLOR[badge.tone] }}
        />
        <span className="truncate">{badge.text}</span>
      </span>
      <span className="truncate ps-4 text-xs text-[var(--color-text-muted)]">{badge.detail}</span>
    </div>
  )
}
