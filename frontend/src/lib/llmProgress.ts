import type { LlmStatus } from './types'

/** "42s" / "3m 05s" - for waits that can run for minutes on a small model. */
export function formatElapsed(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  if (s < 60) return `${s}s`
  return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`
}

/** "Extracting entities and links (part 2/5)" from a job's stage + counters. */
export function describeStage(
  stage: string | null | undefined,
  current: number | null | undefined,
  total: number | null | undefined,
): string {
  const base = stage || 'Working'
  return total && total > 1 && current ? `${base} (part ${current}/${total})` : base
}

/** 0-100 when the job has a known number of parts, else null (indeterminate). */
export function progressPercent(
  current: number | null | undefined,
  total: number | null | undefined,
): number | null {
  if (!total || total < 1 || current == null) return null
  // `current` is the part now being worked on, so completed parts are current-1.
  return Math.min(100, Math.round((Math.max(0, current - 1) / total) * 100))
}

export interface LlmBadge {
  tone: 'ok' | 'busy' | 'warn' | 'error'
  text: string
  detail: string
}

/** One-line summary of the model's state for the always-visible badge. */
export function describeLlm(status: LlmStatus | null): LlmBadge {
  if (!status) return { tone: 'warn', text: 'Model status unknown', detail: 'Could not reach the backend' }
  const name = status.model.split('/').pop() ?? status.model
  switch (status.state) {
    case 'busy': {
      const call = status.active[0]
      const extra = status.active.length > 1 ? ` +${status.active.length - 1} more` : ''
      return {
        tone: 'busy',
        text: `${name} working${call ? ` · ${formatElapsed(call.elapsed_seconds)}` : ''}`,
        detail: call ? `${call.operation}${extra}` : 'Processing',
      }
    }
    case 'downloading':
      return { tone: 'warn', text: `${name} downloading`, detail: 'The model is still being pulled; AI features will fail until it finishes' }
    case 'unreachable':
      return { tone: 'error', text: `${name} unreachable`, detail: 'The model server is not responding' }
    case 'unconfigured':
      return { tone: 'error', text: `${name} not configured`, detail: 'Missing API key for the configured provider' }
    default:
      return {
        tone: 'ok',
        text: `${name} ready`,
        detail:
          status.local && status.model_loaded === false
            ? 'Idle - the first request will take longer while the model loads into memory'
            : status.last_call
              ? `Last: ${status.last_call.operation} (${formatElapsed(status.last_call.duration_seconds)}${status.last_call.ok ? '' : ', failed'})`
              : 'Idle',
      }
  }
}
