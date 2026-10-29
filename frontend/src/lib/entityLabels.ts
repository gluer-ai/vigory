import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from './api'

interface Named {
  entity_id: string
  label: string
}

const CHUNK = 100

/** The name for an entity id: a locally-known entity first, then one fetched
 * from the graph, else the id itself (so nothing ever renders blank). */
export function resolveLabel(
  id: string,
  known: ReadonlyMap<string, string>,
  fetched: Readonly<Record<string, string>>,
): string {
  return known.get(id) || fetched[id] || id
}

/** Ids that still need a lookup: not known locally, not fetched, not already tried. */
export function missingIds(
  ids: readonly string[],
  known: ReadonlyMap<string, string>,
  fetched: Readonly<Record<string, string>>,
  tried: ReadonlySet<string>,
): string[] {
  return [...new Set(ids)].filter((id) => id && !known.has(id) && !(id in fetched) && !tried.has(id)).sort()
}

/** Names for a set of entity ids (e.g. a link's endpoints), so the UI can show
 * "Ivan Petrov" instead of "P-1042". `known` are entities already in hand
 * (this batch, this scope); the rest are looked up in one request. Returns a
 * resolver that falls back to the id while loading or if it can't be found. */
export function useEntityLabels(ids: readonly string[], known: readonly Named[] = []): (id: string) => string {
  const knownMap = useMemo(() => new Map(known.map((e) => [e.entity_id, e.label])), [known])
  const [fetched, setFetched] = useState<Record<string, string>>({})
  const tried = useRef(new Set<string>())
  const wanted = ids.join('\u0000')

  useEffect(() => {
    const todo = missingIds(wanted ? wanted.split('\u0000') : [], knownMap, fetched, tried.current)
    if (todo.length === 0) return
    for (const id of todo) tried.current.add(id) // never retry in a loop, even on failure
    // No cancel-on-cleanup: React StrictMode runs this effect twice in dev, and
    // the second run is skipped by `tried`, so cancelling would drop the result.
    void (async () => {
      const found: Record<string, string> = {}
      for (let i = 0; i < todo.length; i += CHUNK) {
        try {
          Object.assign(found, await api.getEntityLabels(todo.slice(i, i + CHUNK)))
        } catch {
          // fall back to showing the id
        }
      }
      if (Object.keys(found).length > 0) setFetched((prev) => ({ ...prev, ...found }))
    })()
  }, [wanted, knownMap, fetched])

  return (id: string) => resolveLabel(id, knownMap, fetched)
}
