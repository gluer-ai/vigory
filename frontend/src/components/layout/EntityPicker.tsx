import { useEffect, useState } from 'react'
import { api } from '../../lib/api'
import type { Entity } from '../../lib/types'

interface EntityPickerProps {
  value: string
  onChange: (entityId: string) => void
  /** Entities already known locally (e.g. this batch's own staged
   * entities) — matched alongside the backend substring search, so a
   * freshly-added-but-not-yet-committed entity is pickable immediately. */
  localEntities: Entity[]
  placeholder?: string
  'aria-label'?: string
}

/** Free-text entity_id input with a live suggestions dropdown, combining
 * locally-known entities and the backend's /entities/search (existing
 * committed entities) — the source/target picker for manually-added
 * batch links, which may point at either. */
export function EntityPicker({ value, onChange, localEntities, placeholder, ...aria }: EntityPickerProps) {
  const [query, setQuery] = useState(value)
  const [remoteResults, setRemoteResults] = useState<Entity[]>([])
  const [open, setOpen] = useState(false)

  useEffect(() => {
    setQuery(value)
  }, [value])

  useEffect(() => {
    if (!query.trim()) {
      setRemoteResults([])
      return
    }
    let cancelled = false
    const timer = setTimeout(() => {
      api
        .searchEntities(query.trim())
        .then((results) => {
          if (!cancelled) setRemoteResults(results)
        })
        .catch(() => {
          if (!cancelled) setRemoteResults([])
        })
    }, 250)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [query])

  const needle = query.trim().toLowerCase()
  const localMatches = localEntities.filter(
    (e) => e.entity_id.toLowerCase().includes(needle) || e.label.toLowerCase().includes(needle),
  )
  const seen = new Set(localMatches.map((e) => e.entity_id))
  const suggestions = [...localMatches, ...remoteResults.filter((e) => !seen.has(e.entity_id))]

  function pick(entity: Entity) {
    onChange(entity.entity_id)
    setQuery(entity.entity_id)
    setOpen(false)
  }

  return (
    <div className="relative flex flex-col gap-1.5">
      <input
        type="text"
        value={query}
        onChange={(e) => {
          setQuery(e.target.value)
          onChange(e.target.value)
          setOpen(true)
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        placeholder={placeholder}
        aria-label={aria['aria-label']}
        className="rounded-md border border-[var(--color-border)] bg-[var(--color-surface-0)] px-3 py-1.5 text-sm text-[var(--color-text-primary)] placeholder:text-[var(--color-text-muted)] focus-visible:border-[var(--color-focus)]"
      />
      {open && needle && suggestions.length > 0 && (
        <ul className="absolute top-full z-10 mt-1 max-h-48 w-full overflow-y-auto rounded-md border border-[var(--color-border)] bg-[var(--color-surface-1)] shadow-lg">
          {suggestions.map((entity) => (
            <li key={entity.entity_id}>
              <button
                type="button"
                onMouseDown={() => pick(entity)}
                className="flex w-full flex-col items-start px-2.5 py-1.5 text-start hover:bg-[var(--color-surface-hover)]"
              >
                <span className="text-sm text-[var(--color-text-primary)]">{entity.label}</span>
                <span className="font-mono text-xs text-[var(--color-text-muted)]">{entity.entity_id}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
