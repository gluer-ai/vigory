import { Search } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { api } from '../../lib/api'
import { classMeta } from '../../lib/entityClass'
import type { Entity } from '../../lib/types'

interface EntitySearchProps {
  onSelect: (entityId: string) => void
}

/** Debounced name/alias search — the by-name counterpart to typing an exact
 * Trigger entity ID. Picking a result sets it as the trigger. */
export function EntitySearch({ onSelect }: EntitySearchProps) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<Entity[]>([])
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const trimmed = query.trim()
    if (trimmed.length < 2) {
      setResults([])
      setOpen(false)
      return
    }
    setLoading(true)
    const timer = setTimeout(() => {
      api
        .searchEntities(trimmed)
        .then((entities) => {
          setResults(entities)
          setOpen(true)
        })
        .catch(() => setResults([]))
        .finally(() => setLoading(false))
    }, 250)
    return () => clearTimeout(timer)
  }, [query])

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  function handleSelect(entity: Entity) {
    onSelect(entity.entity_id)
    setQuery('')
    setResults([])
    setOpen(false)
  }

  return (
    <div ref={containerRef} className="relative flex flex-col gap-1.5">
      <label htmlFor="entity-search" className="text-sm text-[var(--color-text-muted)]">
        Search by name
      </label>
      <div className="relative">
        <Search
          size={14}
          className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--color-text-muted)]"
        />
        <input
          id="entity-search"
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onFocus={() => results.length > 0 && setOpen(true)}
          placeholder="e.g. Petrov"
          role="combobox"
          aria-expanded={open}
          aria-controls="entity-search-results"
          autoComplete="off"
          className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-surface-1)] py-1.5 pe-3 ps-8 text-sm text-[var(--color-text-primary)] placeholder:text-[var(--color-text-muted)] focus-visible:border-[var(--color-focus)]"
        />
      </div>

      {open && (
        <ul
          id="entity-search-results"
          role="listbox"
          className="absolute top-full z-10 mt-1 max-h-64 w-full overflow-y-auto rounded-md border border-[var(--color-border)] bg-[var(--color-surface-1)] p-1 shadow-lg"
        >
          {loading && <li className="px-2 py-1.5 text-xs text-[var(--color-text-muted)]">Searching…</li>}
          {!loading && results.length === 0 && (
            <li className="px-2 py-1.5 text-xs text-[var(--color-text-muted)]">No matches</li>
          )}
          {!loading &&
            results.map((entity) => {
              const { icon: Icon, colorVar } = classMeta(entity.entity_class)
              return (
                <li key={entity.entity_id} role="option" aria-selected={false}>
                  <button
                    type="button"
                    onClick={() => handleSelect(entity)}
                    className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-start text-sm text-[var(--color-text-primary)] hover:bg-[var(--color-surface-hover)]"
                  >
                    <Icon size={14} style={{ color: `var(${colorVar})` }} aria-hidden="true" />
                    <span className="truncate">{entity.label}</span>
                    <span className="ms-auto shrink-0 font-mono text-xs text-[var(--color-text-muted)]">
                      {entity.entity_id}
                    </span>
                  </button>
                </li>
              )
            })}
        </ul>
      )}
    </div>
  )
}
