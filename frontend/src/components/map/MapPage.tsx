import { useEffect, useState } from 'react'
import { api, ApiError } from '../../lib/api'
import { classMeta } from '../../lib/entityClass'
import { bboxAroundPoint, distanceKm } from '../../lib/mapGeo'
import type { Entity } from '../../lib/types'
import { Inspector } from '../layout/Inspector'
import { ConfidenceChip, StatusChip } from '../ui/Chip'
import { InteractiveMap, type MapPoint } from './InteractiveMap'

const SWEDEN_BBOX = '55.0,69.1,10.9,24.2'
const NEWS_LIMIT = 8

interface MapPageProps {
  onSelectEntity: (entityId: string) => void
}

type Selection = { kind: 'area'; bbox: [number, number, number, number] } | { kind: 'marker'; point: MapPoint }

function toPoints(entities: Entity[]): MapPoint[] {
  const points: MapPoint[] = []
  for (const entity of entities) {
    const lat = entity.attrs.lat
    const lon = entity.attrs.lon
    if (typeof lat === 'number' && typeof lon === 'number') points.push({ entity, lat, lon })
  }
  return points
}

/** Pan/zoom the live map to find geo-tagged entities, or click an area (or a
 * marker) to search near that point. A click also pulls in non-geo-tagged
 * context — the latest news/radio items — since feeds don't create graph
 * links between entities of different types yet, so that's the closest
 * "related across feeds" view available without a backend change. */
export function MapPage({ onSelectEntity }: MapPageProps) {
  const [allPoints, setAllPoints] = useState<MapPoint[]>([])
  const [loadStatus, setLoadStatus] = useState<'loading' | 'error' | 'ready'>('loading')
  const [loadError, setLoadError] = useState('')

  const [newsItems, setNewsItems] = useState<Entity[]>([])

  const [selection, setSelection] = useState<Selection | null>(null)
  const [nearby, setNearby] = useState<Entity[] | null>(null)
  const [nearbyStatus, setNearbyStatus] = useState<'idle' | 'loading' | 'error'>('idle')
  const [nearbyError, setNearbyError] = useState('')

  useEffect(() => {
    setLoadStatus('loading')
    api
      .listEntities({ limit: 200, offset: 0, bbox: SWEDEN_BBOX })
      .then((rows) => {
        setAllPoints(toPoints(rows))
        setLoadStatus('ready')
      })
      .catch((err) => {
        setLoadError(err instanceof ApiError ? err.message : 'Failed to reach the backend')
        setLoadStatus('error')
      })
    api
      .listEntities({ limit: NEWS_LIMIT, offset: 0, entityClass: 'INFORMATION_OBJECT' })
      .then(setNewsItems)
      .catch(() => setNewsItems([])) // news is supplementary context; a failure here shouldn't block the map
  }, [])

  function runNearbySearch(bbox: [number, number, number, number], excludeEntityId?: string) {
    setNearbyStatus('loading')
    api
      .listEntities({ limit: 100, offset: 0, bbox: bbox.join(',') })
      .then((rows) => {
        setNearby(excludeEntityId ? rows.filter((e) => e.entity_id !== excludeEntityId) : rows)
        setNearbyStatus('idle')
      })
      .catch((err) => {
        setNearbyError(err instanceof ApiError ? err.message : 'Failed to reach the backend')
        setNearbyStatus('error')
      })
  }

  function handleAreaClick(bbox: [number, number, number, number]) {
    setSelection({ kind: 'area', bbox })
    runNearbySearch(bbox)
  }

  function handleMarkerClick(entityId: string) {
    const point = allPoints.find((p) => p.entity.entity_id === entityId)
    if (!point) return
    setSelection({ kind: 'marker', point })
    runNearbySearch(bboxAroundPoint(point.lat, point.lon), entityId)
  }

  const sortedNearby =
    selection?.kind === 'marker' && nearby
      ? [...nearby].sort((a, b) => {
          const da = typeof a.attrs.lat === 'number' && typeof a.attrs.lon === 'number'
            ? distanceKm(selection.point.lat, selection.point.lon, a.attrs.lat, a.attrs.lon)
            : Infinity
          const db = typeof b.attrs.lat === 'number' && typeof b.attrs.lon === 'number'
            ? distanceKm(selection.point.lat, selection.point.lon, b.attrs.lat, b.attrs.lon)
            : Infinity
          return da - db
        })
      : nearby

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="border-b border-[var(--color-border)] px-6 py-4">
        <h1 className="text-base font-semibold text-[var(--color-text-primary)]">Map search</h1>
        <p className="mt-0.5 text-sm text-[var(--color-text-muted)]">
          Pan and zoom the live map, click an area, or click a plotted entity to see what's nearby
          and in the news.
        </p>
      </div>
      <div className="flex flex-1 overflow-hidden">
        <div className="relative flex-1">
          {loadStatus === 'error' && (
            <p role="alert" className="absolute inset-x-0 top-0 z-10 bg-[var(--color-surface-1)] p-3 text-sm text-[var(--color-status-destroyed)]">
              {loadError}
            </p>
          )}
          <InteractiveMap points={allPoints} onAreaClick={handleAreaClick} onMarkerClick={handleMarkerClick} />
        </div>
        <div className="w-[360px] shrink-0 overflow-y-auto border-s border-[var(--color-border)] p-4">
          {selection === null && (
            <p className="text-sm text-[var(--color-text-muted)]">
              {loadStatus === 'loading'
                ? 'Loading map…'
                : `${allPoints.length} geo-tagged entities on the map. Click an area or an entity to search it.`}
            </p>
          )}

          {selection?.kind === 'marker' && (
            <div className="mb-5 border-b border-[var(--color-border)] pb-4">
              <Inspector entity={selection.point.entity} />
              <button
                type="button"
                onClick={() => onSelectEntity(selection.point.entity.entity_id)}
                className="mt-3 w-full rounded-md border border-[var(--color-border)] px-3 py-1.5 text-sm text-[var(--color-text-primary)] hover:bg-[var(--color-surface-hover)]"
              >
                Open in scope graph (relationships)
              </button>
            </div>
          )}

          {nearbyStatus === 'loading' && <p className="text-sm text-[var(--color-text-muted)]">Searching…</p>}
          {nearbyStatus === 'error' && (
            <p role="alert" className="text-sm text-[var(--color-status-destroyed)]">
              {nearbyError}
            </p>
          )}

          {selection !== null && nearbyStatus !== 'loading' && (
            <EntityList
              title={selection.kind === 'marker' ? 'Nearby' : 'In this area'}
              entities={sortedNearby ?? []}
              origin={selection.kind === 'marker' ? selection.point : null}
              emptyLabel="No other geo-tagged entities here — try polling a live feed (aircraft, vessels, earthquakes, traffic) first."
              onSelectEntity={onSelectEntity}
            />
          )}

          {selection !== null && (
            <EntityList
              title="Latest news (not geo-located, shown for context)"
              entities={newsItems}
              origin={null}
              emptyLabel="No news polled yet — try POST /feeds/news/poll or /feeds/radio_news/poll."
              onSelectEntity={onSelectEntity}
              className="mt-5 border-t border-[var(--color-border)] pt-4"
            />
          )}
        </div>
      </div>
    </div>
  )
}

function EntityList({
  title,
  entities,
  origin,
  emptyLabel,
  onSelectEntity,
  className,
}: {
  title: string
  entities: Entity[]
  origin: MapPoint | null
  emptyLabel: string
  onSelectEntity: (id: string) => void
  className?: string
}) {
  return (
    <div className={className}>
      <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
        {title} ({entities.length})
      </p>
      {entities.length === 0 ? (
        <p className="py-2 text-sm text-[var(--color-text-muted)]">{emptyLabel}</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {entities.map((e) => {
            const { icon: Icon, colorVar } = classMeta(e.entity_class, e.entity_subclass)
            const km =
              origin && typeof e.attrs.lat === 'number' && typeof e.attrs.lon === 'number'
                ? distanceKm(origin.lat, origin.lon, e.attrs.lat, e.attrs.lon)
                : null
            return (
              <li key={e.entity_id}>
                <button
                  type="button"
                  onClick={() => onSelectEntity(e.entity_id)}
                  className="flex w-full items-center gap-2 rounded-md border border-[var(--color-border)] p-2 text-start text-sm text-[var(--color-text-primary)] hover:bg-[var(--color-surface-hover)]"
                >
                  <Icon size={14} style={{ color: `var(${colorVar})` }} aria-hidden="true" />
                  <span className="flex-1 truncate">{e.label}</span>
                  {km !== null && (
                    <span className="shrink-0 text-xs text-[var(--color-text-muted)]">{km.toFixed(0)} km</span>
                  )}
                  <StatusChip status={e.status} />
                  <ConfidenceChip code={e.confidence} />
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
