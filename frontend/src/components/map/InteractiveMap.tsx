import * as Cesium from 'cesium'
import 'cesium/Build/Cesium/Widgets/widgets.css'
import { useEffect, useRef } from 'react'
import { classMeta } from '../../lib/entityClass'
import { bboxAroundPoint, CLICK_RADIUS_LAT } from '../../lib/mapGeo'
import type { Entity } from '../../lib/types'

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

/** Reads a CSS custom property's current value (a hex/rgb string) off the
 * document root — the same design tokens the rest of the app uses — since
 * Cesium renders to a WebGL canvas and can't consume `var(...)` directly. */
function cssColor(varName: string, fallback: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(varName).trim()
  return value || fallback
}

/** Real pannable/zoomable 3D globe (CesiumJS, keyless OSM imagery) — the
 * click-to-search counterpart to the old static region polygons. Plots
 * every geo-tagged entity as a point; clicking empty globe searches a bbox
 * around that point, clicking a point jumps straight to that entity. */
export function InteractiveMap({ points, onAreaClick, onMarkerClick }: InteractiveMapProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const viewerRef = useRef<Cesium.Viewer | null>(null)
  const entityIdByCesiumId = useRef<Map<unknown, string>>(new Map())
  const searchAreaEntityRef = useRef<unknown>(null)
  const onAreaClickRef = useRef(onAreaClick)
  const onMarkerClickRef = useRef(onMarkerClick)
  const tooltipRef = useRef<HTMLDivElement>(null)
  const pointsRef = useRef(points)
  useEffect(() => {
    onAreaClickRef.current = onAreaClick
    onMarkerClickRef.current = onMarkerClick
  }, [onAreaClick, onMarkerClick])

  useEffect(() => {
    if (!containerRef.current || viewerRef.current) return
    const viewer = new Cesium.Viewer(containerRef.current, {
      baseLayer: Cesium.ImageryLayer.fromProviderAsync(
        Promise.resolve(new Cesium.OpenStreetMapImageryProvider({ url: 'https://tile.openstreetmap.org/' })),
      ),
      baseLayerPicker: false,
      geocoder: false,
      homeButton: false,
      sceneModePicker: false,
      navigationHelpButton: false,
      animation: false,
      timeline: false,
      fullscreenButton: false,
      infoBox: false,
      selectionIndicator: false,
    })

    const handler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas)
    handler.setInputAction((click: { position: Cesium.Cartesian2 }) => {
      const picked = viewer.scene.pick(click.position)
      const entityId = picked?.id ? entityIdByCesiumId.current.get(picked.id) : undefined
      if (entityId) {
        onMarkerClickRef.current(entityId)
        return
      }

      const cartesian = viewer.camera.pickEllipsoid(click.position, viewer.scene.globe.ellipsoid)
      if (!cartesian) return
      const cartographic = Cesium.Cartographic.fromCartesian(cartesian)
      const lat = Cesium.Math.toDegrees(cartographic.latitude)
      const lon = Cesium.Math.toDegrees(cartographic.longitude)

      if (searchAreaEntityRef.current) viewer.entities.remove(searchAreaEntityRef.current as Cesium.Entity)
      const focusColor = Cesium.Color.fromCssColorString(cssColor('--color-focus', '#4fd1a5'))
      searchAreaEntityRef.current = viewer.entities.add({
        position: Cesium.Cartesian3.fromDegrees(lon, lat),
        ellipse: {
          semiMinorAxis: CLICK_RADIUS_LAT * 111_000,
          semiMajorAxis: CLICK_RADIUS_LAT * 111_000,
          material: focusColor.withAlpha(0.08),
          outline: true,
          outlineColor: focusColor,
        },
      })

      onAreaClickRef.current(bboxAroundPoint(lat, lon))
    }, Cesium.ScreenSpaceEventType.LEFT_CLICK)

    handler.setInputAction((movement: { endPosition: Cesium.Cartesian2 }) => {
      const tooltip = tooltipRef.current
      if (!tooltip) return
      const picked = viewer.scene.pick(movement.endPosition)
      const entityId = picked?.id ? entityIdByCesiumId.current.get(picked.id) : undefined
      const point = entityId ? pointsRef.current.find((p) => p.entity.entity_id === entityId) : undefined
      if (point) {
        tooltip.textContent = point.entity.label
        tooltip.style.left = `${movement.endPosition.x + 12}px`
        tooltip.style.top = `${movement.endPosition.y + 12}px`
        tooltip.classList.remove('hidden')
      } else {
        tooltip.classList.add('hidden')
      }
    }, Cesium.ScreenSpaceEventType.MOUSE_MOVE)

    viewerRef.current = viewer
    return () => {
      handler.destroy()
      viewer.destroy()
      viewerRef.current = null
    }
  }, [])

  useEffect(() => {
    const viewer = viewerRef.current
    if (!viewer) return
    viewer.entities.removeAll()
    searchAreaEntityRef.current = null
    entityIdByCesiumId.current = new Map()
    pointsRef.current = points
    for (const { entity, lat, lon } of points) {
      const { colorVar } = classMeta(entity.entity_class, entity.entity_subclass)
      const cesiumEntity = viewer.entities.add({
        position: Cesium.Cartesian3.fromDegrees(lon, lat),
        point: { pixelSize: 8, color: Cesium.Color.fromCssColorString(cssColor(colorVar, '#ffffff')) },
      })
      entityIdByCesiumId.current.set(cesiumEntity, entity.entity_id)
    }
  }, [points])

  return (
    <div className="relative h-full min-h-[420px] w-full">
      <div
        ref={containerRef}
        role="application"
        aria-label="Interactive 3D globe — pan, zoom, and click an area to search entities near it"
        className="h-full w-full"
      />
      <div
        ref={tooltipRef}
        className="pointer-events-none absolute z-10 hidden rounded bg-[var(--color-surface-1)] px-2 py-1 text-xs text-[var(--color-text-primary)] shadow"
      />
    </div>
  )
}
