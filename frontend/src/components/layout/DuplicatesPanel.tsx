import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../../lib/api'
import { subclassName } from '../../lib/entityClass'
import { chosenTarget, describeReason, describeWhere, entitySignature } from '../../lib/duplicates'
import type { DuplicateDecision, DuplicateItem, IngestBatch } from '../../lib/types'
import { Button } from '../ui/Button'

interface DuplicatesPanelProps {
  batch: IngestBatch
  onBatchUpdated: (batch: IngestBatch) => void
  /** How many duplicates still need a decision, so the parent can hold back Commit. */
  onUnresolvedChange: (count: number) => void
}

/** Lists proposed entities that look like duplicates (of the graph or of each
 * other) and lets the reviewer decide each one: keep it as a separate entity,
 * or remove it because it IS the existing one (its links move to that entity). */
export function DuplicatesPanel({ batch, onBatchUpdated, onUnresolvedChange }: DuplicatesPanelProps) {
  const [items, setItems] = useState<DuplicateItem[] | null>(null)
  const [loadError, setLoadError] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [picked, setPicked] = useState<Record<string, string>>({})
  const latest = useRef(0)

  const load = useCallback(async () => {
    const mine = ++latest.current
    try {
      const { duplicates } = await api.getDuplicates(batch.batch_id)
      if (latest.current !== mine) return
      setItems(duplicates)
      setLoadError('')
    } catch (err) {
      if (latest.current !== mine) return
      setLoadError(err instanceof ApiError ? err.message : 'Failed to reach the backend')
    }
  }, [batch.batch_id])

  const signature = entitySignature(batch.entities)
  useEffect(() => {
    void load()
  }, [load, signature])

  useEffect(() => {
    onUnresolvedChange(items?.length ?? 0)
  }, [items, onUnresolvedChange])

  async function resolve(decisions: DuplicateDecision[]) {
    setBusy(true)
    setError('')
    try {
      onBatchUpdated(await api.resolveDuplicates(batch.batch_id, decisions))
      await load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to reach the backend')
    } finally {
      setBusy(false)
    }
  }

  if (loadError) {
    return (
      <p role="alert" className="flex items-center gap-2 text-xs text-[var(--color-status-destroyed)]">
        Couldn't check for duplicates: {loadError}
        <Button onClick={() => void load()}>Retry</Button>
      </p>
    )
  }
  if (!items || items.length === 0) return null

  return (
    <section
      aria-label="Possible duplicates"
      className="rounded-md border border-[var(--color-status-unknown)] p-3"
    >
      <div className="mb-2 flex items-center justify-between gap-2">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-[var(--color-text-primary)]">
          Possible duplicates ({items.length})
        </h3>
        <Button
          disabled={busy}
          onClick={() => void resolve(items.map((i) => ({ entity_id: i.entity_id, action: 'keep_separate' })))}
        >
          Keep all as separate
        </Button>
      </div>
      <p className="mb-3 text-xs text-[var(--color-text-muted)]">
        Decide each one before committing. <strong>Keep separate</strong> adds it as its own entity;{' '}
        <strong>Remove</strong> drops this copy and points its links at the existing entity.
      </p>

      <ul className="flex flex-col gap-3">
        {items.map((item) => {
          const target = chosenTarget(item, picked[item.entity_id])
          return (
            <li
              key={item.entity_id}
              className="rounded border border-[var(--color-border)] p-2.5"
            >
              <div className="text-sm text-[var(--color-text-primary)]">
                {item.label || item.entity_id}{' '}
                <span className="text-xs text-[var(--color-text-muted)]">
                  {subclassName(item.entity_subclass)}
                </span>
              </div>

              <fieldset className="mt-1.5 flex flex-col gap-1 border-0 p-0">
                <legend className="sr-only">Which entity is “{item.label}” a duplicate of?</legend>
                {item.matches.map((m) => (
                  <label key={m.entity_id} className="flex items-start gap-2 text-xs text-[var(--color-text-muted)]">
                    {item.matches.length > 1 ? (
                      <input
                        type="radio"
                        name={`dup-${item.entity_id}`}
                        checked={target.entity_id === m.entity_id}
                        onChange={() => setPicked((p) => ({ ...p, [item.entity_id]: m.entity_id }))}
                        className="mt-0.5"
                      />
                    ) : (
                      <span aria-hidden="true">↳</span>
                    )}
                    <span>
                      <span className="text-[var(--color-text-primary)]" title={m.entity_id}>
                        {m.label}
                      </span>{' '}
                      — {describeWhere(m.where)} ·{' '}
                      {describeReason(m.reason)}
                    </span>
                  </label>
                ))}
              </fieldset>

              <div className="mt-2 flex flex-wrap gap-2">
                <Button
                  disabled={busy}
                  onClick={() => void resolve([{ entity_id: item.entity_id, action: 'keep_separate' }])}
                >
                  Keep as separate entity
                </Button>
                <Button
                  disabled={busy}
                  onClick={() =>
                    void resolve([{ entity_id: item.entity_id, action: 'remove', target_id: target.entity_id }])
                  }
                >
                  Remove — same as “{target.label}”
                </Button>
              </div>
            </li>
          )
        })}
      </ul>

      {error && (
        <p role="alert" className="mt-2 text-xs text-[var(--color-status-destroyed)]">
          {error}
        </p>
      )}
    </section>
  )
}
