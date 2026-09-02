import { RefreshCw } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, ApiError } from '../../lib/api'
import type { FeedStatus } from '../../lib/types'

const FEED_NAMES = ['situations', 'cameras', 'earthquakes', 'aircraft', 'vessels', 'news', 'radio_news']

const FEED_LABELS: Record<string, string> = {
  situations: 'Traffic incidents (Trafikverket)',
  cameras: 'Traffic cameras (Trafikverket)',
  earthquakes: 'Earthquakes (USGS)',
  aircraft: 'Aircraft (OpenSky)',
  vessels: 'Vessels (aisstream.io)',
  news: 'News (GDELT)',
  radio_news: 'Radio news (Sveriges Radio)',
}

const SCHEDULE_OPTIONS: { label: string; value: number | null }[] = [
  { label: 'Off (manual only)', value: null },
  { label: 'Every 1 min', value: 60 },
  { label: 'Every 5 min', value: 300 },
  { label: 'Every 15 min', value: 900 },
  { label: 'Every hour', value: 3600 },
]

const REFRESH_MS = 5000

/** Feed admin: poll any feed on demand, or turn on an interval schedule
 * that keeps polling server-side (survives closing this tab) until turned
 * off. Status auto-refreshes so a running schedule's progress is visible. */
export function FeedsPage() {
  const [status, setStatus] = useState<Record<string, FeedStatus> | null>(null)
  const [loadError, setLoadError] = useState('')
  const [pollingNow, setPollingNow] = useState<Set<string>>(new Set())
  const [rowError, setRowError] = useState<Record<string, string>>({})

  useEffect(() => {
    let cancelled = false
    async function refresh() {
      try {
        const result = await api.getFeedsStatus()
        if (!cancelled) {
          setStatus(result)
          setLoadError('')
        }
      } catch (err) {
        if (!cancelled) setLoadError(err instanceof ApiError ? err.message : 'Failed to reach the backend')
      }
    }
    refresh()
    const id = setInterval(refresh, REFRESH_MS)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [])

  async function handlePollNow(name: string) {
    setPollingNow((prev) => new Set(prev).add(name))
    setRowError((prev) => ({ ...prev, [name]: '' }))
    try {
      const result = await api.pollFeed(name)
      setStatus((prev) => ({ ...(prev ?? {}), [name]: { ...(prev?.[name] ?? { schedule_interval_seconds: null }), ...result } }))
    } catch (err) {
      setRowError((prev) => ({ ...prev, [name]: err instanceof ApiError ? err.message : 'Failed to reach the backend' }))
    } finally {
      setPollingNow((prev) => {
        const next = new Set(prev)
        next.delete(name)
        return next
      })
    }
  }

  async function handleScheduleChange(name: string, intervalSeconds: number | null) {
    setRowError((prev) => ({ ...prev, [name]: '' }))
    try {
      const result = await api.setFeedSchedule(name, intervalSeconds)
      setStatus((prev) => ({
        ...(prev ?? {}),
        [name]: { ...(prev?.[name] ?? {}), schedule_interval_seconds: result.schedule_interval_seconds },
      }))
    } catch (err) {
      setRowError((prev) => ({ ...prev, [name]: err instanceof ApiError ? err.message : 'Failed to reach the backend' }))
    }
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="border-b border-[var(--color-border)] px-6 py-4">
        <h1 className="text-base font-semibold text-[var(--color-text-primary)]">Feeds</h1>
        <p className="mt-0.5 text-sm text-[var(--color-text-muted)]">
          Poll any live feed on demand, or set a schedule so it keeps polling on the server —
          even after you close this tab.
        </p>
      </div>
      <div className="flex-1 overflow-y-auto p-6">
        {loadError && (
          <p role="alert" className="mb-3 text-sm text-[var(--color-status-destroyed)]">
            {loadError}
          </p>
        )}
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-[var(--color-border)] text-left text-[var(--color-text-muted)]">
              <th className="py-2 pe-3 font-medium">Feed</th>
              <th className="py-2 pe-3 font-medium">Last poll</th>
              <th className="py-2 pe-3 font-medium">Schedule</th>
              <th className="py-2 pe-3 font-medium" />
            </tr>
          </thead>
          <tbody>
            {FEED_NAMES.map((name) => {
              const feedStatus = status?.[name]
              const isPolling = pollingNow.has(name)
              return (
                <tr key={name} className="border-b border-[var(--color-border)] align-top">
                  <td className="py-2 pe-3 text-[var(--color-text-primary)]">
                    {FEED_LABELS[name] ?? name}
                    <div className="font-mono text-xs text-[var(--color-text-muted)]">{name}</div>
                  </td>
                  <td className="py-2 pe-3 text-xs text-[var(--color-text-muted)]">
                    {feedStatus?.error ? (
                      <span className="text-[var(--color-status-destroyed)]">{feedStatus.error}</span>
                    ) : feedStatus?.polled_at ? (
                      <>
                        {feedStatus.fetched ?? 0} fetched / {feedStatus.written ?? 0} written
                        <div>{new Date(feedStatus.polled_at).toLocaleString()}</div>
                      </>
                    ) : (
                      'never polled'
                    )}
                    {rowError[name] && <div className="mt-1 text-[var(--color-status-destroyed)]">{rowError[name]}</div>}
                  </td>
                  <td className="py-2 pe-3">
                    <select
                      value={String(feedStatus?.schedule_interval_seconds ?? '')}
                      onChange={(e) =>
                        handleScheduleChange(name, e.target.value === '' ? null : Number(e.target.value))
                      }
                      className="rounded border border-[var(--color-border)] bg-transparent px-2 py-1 text-xs text-[var(--color-text-primary)]"
                      aria-label={`Poll schedule for ${FEED_LABELS[name] ?? name}`}
                    >
                      {SCHEDULE_OPTIONS.map((opt) => (
                        <option key={opt.label} value={opt.value ?? ''} className="bg-[var(--color-surface-1)]">
                          {opt.label}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="py-2 pe-3">
                    <button
                      type="button"
                      onClick={() => handlePollNow(name)}
                      disabled={isPolling}
                      className="flex items-center gap-1.5 rounded-md border border-[var(--color-border)] px-2.5 py-1 text-xs text-[var(--color-text-primary)] hover:bg-[var(--color-surface-hover)] disabled:opacity-50"
                    >
                      <RefreshCw size={12} className={isPolling ? 'animate-spin' : ''} aria-hidden="true" />
                      {isPolling ? 'Polling…' : 'Poll now'}
                    </button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
