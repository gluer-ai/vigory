import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { useEffect, useRef } from 'react'
import { classMeta } from '../../lib/entityClass'
import { bboxAroundPoint, CLICK_RADIUS_LAT } from '../../lib/mapGeo'
import type { Entity } from '../../lib/types'

const SWEDEN_CENTER: [lat: number, lon: number] = [62.5, 16.5]
const SWEDEN_BOUNDS = new L.LatLngBounds([53.5, 3.0], [71.5, 32.0])

export interface MapPoint {
  entity: Entity
  lat: number
  lon: number
}

interface InteractiveMapProps {
  points: MapPoint[]
  onAreaClick: (bbox: [number, number, number, number]) => void
  onMarkerClick: (entityId: string) => void
}

/** Real pannable/zoomable OSM map (Leaflet, keyless tiles) — the click-to-
 * search counterpart to the old static region polygons. Plots every
 * geo-tagged entity as a dot; clicking empty map area searches a bbox
 * around that point, clicking a dot jumps straight to that entity. */
export function InteractiveMap({ points, onAreaClick, onMarkerClick }: InteractiveMapProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<L.Map | null>(null)
  const markerLayerRef = useRef<L.LayerGroup | null>(null)
  const searchAreaRef = useRef<L.Circle | null>(null)
  const onAreaClickRef = useRef(onAreaClick)
  const onMarkerClickRef = useRef(onMarkerClick)
  useEffect(() => {
    onAreaClickRef.current = onAreaClick
    onMarkerClickRef.current = onMarkerClick
  }, [onAreaClick, onMarkerClick])

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return
    const map = L.map(containerRef.current, {
      minZoom: 3,
      maxZoom: 17,
      maxBounds: SWEDEN_BOUNDS.pad(0.5),
    }).setView(SWEDEN_CENTER, 4)

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      maxZoom: 19,
    }).addTo(map)

    markerLayerRef.current = L.layerGroup().addTo(map)

    map.on('click', (e: L.LeafletMouseEvent) => {
      const { lat, lng } = e.latlng
      const bbox = bboxAroundPoint(lat, lng)
      if (searchAreaRef.current) searchAreaRef.current.remove()
      searchAreaRef.current = L.circle([lat, lng], {
        radius: CLICK_RADIUS_LAT * 111_000,
        color: 'var(--color-focus)',
        weight: 2,
        fillOpacity: 0.08,
      }).addTo(map)
      onAreaClickRef.current(bbox)
    })

    mapRef.current = map
    return () => {
      map.remove()
      mapRef.current = null
    }
  }, [])

  useEffect(() => {
    const layer = markerLayerRef.current
    if (!layer) return
    layer.clearLayers()
    for (const { entity, lat, lon } of points) {
      const { colorVar } = classMeta(entity.entity_class, entity.entity_subclass)
      const color = `var(${colorVar})`
      L.circleMarker([lat, lon], {
        radius: 5,
        color,
        fillColor: color,
        fillOpacity: 0.85,
        weight: 1,
      })
        .bindTooltip(entity.label)
        .on('click', (e: L.LeafletMouseEvent) => {
          // Stop the click from also reaching the map's own click handler,
          // which would otherwise draw an unwanted search circle here too.
          L.DomEvent.stopPropagation(e)
          onMarkerClickRef.current(entity.entity_id)
        })
        .addTo(layer)
    }
  }, [points])

  return (
    <div
      ref={containerRef}
      role="application"
      aria-label="Interactive map of Sweden — pan, zoom, and click an area to search entities near it"
      className="h-full min-h-[420px] w-full"
    />
  )
}
