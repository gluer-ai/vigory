# Cesium 3D Globe Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Sweden-bounded 2D Leaflet map with a keyless CesiumJS 3D globe, as phase 1 of 4 toward God's-Eye-View-style live camera feeds.

**Architecture:** `InteractiveMap.tsx` keeps its existing props contract (`points`, `onAreaClick`, `onMarkerClick`) but swaps its internals from a Leaflet `L.Map` to a Cesium `Viewer` with keyless OSM imagery. Click routing (entity pick vs. bare-globe pick) replaces Leaflet's marker/`stopPropagation` trick with a single `ScreenSpaceEventHandler`. `MapPage.tsx` and `mapGeo.ts` are otherwise unchanged except for dropping the hardcoded Sweden bbox filter on the initial entity fetch.

**Tech Stack:** React 19 + Vite 8 + TypeScript, CesiumJS 1.145 via `vite-plugin-cesium`, Vitest + Testing Library (mocked `cesium` module, no real WebGL in tests — same constraint the current Leaflet tests already work around).

**Spec:** `docs/superpowers/specs/2026-09-13-cesium-globe-migration-design.md`

## Global Constraints

- No Cesium ion token or Google Maps key required — base globe is keyless `OpenStreetMapImageryProvider` + Cesium's default `EllipsoidTerrainProvider`.
- `InteractiveMap`'s public prop contract (`points: MapPoint[]`, `onAreaClick`, `onMarkerClick`) must not change — `MapPage.tsx` consumes it as-is.
- No geographic camera constraint (no Leaflet-style `maxBounds`) — the globe must be freely pannable/zoomable anywhere on Earth.
- The "click empty area → search a box around it" radius stays the existing fixed-degree box (`CLICK_RADIUS_LAT`/`CLICK_RADIUS_LON` in `frontend/src/lib/mapGeo.ts`, via `bboxAroundPoint`) — do not make it zoom/altitude-aware. Out of scope per spec.
- No new camera data sources, no image rendering for `photo_url`, no viewshed/pose-calibration/clustering — those are later phases, not this plan.
- No 2D-map fallback for WebGL-unavailable environments — accepted risk, not handled here.
- Tests must not depend on real WebGL or a real Cesium `Viewer` — mock the `cesium` module, following the same pattern `InteractiveMap.test.tsx` already uses for `leaflet`.

---

## File Structure

- `frontend/package.json` — add `cesium` (runtime) and `vite-plugin-cesium` (dev) dependencies (Task 1); later remove `leaflet`/`@types/leaflet` (Task 4).
- `frontend/vite.config.ts` — register the `vite-plugin-cesium` plugin (Task 1).
- `frontend/src/components/map/InteractiveMap.tsx` — full rewrite: Cesium `Viewer` setup, entity markers, click routing, search-area indicator (Task 1); hover tooltip added on top (Task 2).
- `frontend/src/components/map/InteractiveMap.test.tsx` — full rewrite: mock `cesium` instead of `leaflet`, same click-routing behavior under test (Task 1).
- `frontend/src/components/map/MapPage.tsx` — drop the hardcoded `SWEDEN_BBOX` initial-fetch filter (Task 3).

`frontend/src/lib/mapGeo.ts` is not modified by this plan — `bboxAroundPoint` and `distanceKm` are already Cesium-agnostic pure functions and are reused as-is.

---

### Task 1: Cesium globe — keyless rendering + click routing

**Files:**
- Modify: `frontend/package.json` (add dependencies)
- Modify: `frontend/vite.config.ts`
- Modify: `frontend/src/components/map/InteractiveMap.tsx` (full rewrite, currently 107 lines)
- Test: `frontend/src/components/map/InteractiveMap.test.tsx` (full rewrite, currently 124 lines)

**Interfaces:**
- Consumes: `bboxAroundPoint(lat, lon)` and `CLICK_RADIUS_LAT` from `frontend/src/lib/mapGeo.ts` (unchanged); `classMeta(entityClass, entitySubclass)` from `frontend/src/lib/entityClass.ts` (unchanged, returns `{ icon, colorVar }`); `Entity` type from `frontend/src/lib/types.ts` (unchanged).
- Produces: `InteractiveMap({ points, onAreaClick, onMarkerClick })` — identical exported signature to today. `MapPoint` interface (`{ entity: Entity; lat: number; lon: number }`) — identical to today. No other file changes depend on this task's internals.

- [ ] **Step 1: Add Cesium dependencies**

```bash
cd frontend
npm install cesium@^1.145.0
npm install -D vite-plugin-cesium@^1.2.23
```

- [ ] **Step 2: Wire the Vite plugin**

Replace the full contents of `frontend/vite.config.ts`:

```ts
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import cesium from 'vite-plugin-cesium'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss(), cesium()],
})
```

- [ ] **Step 3: Write the failing test (new `cesium` mock, same click-routing behavior)**

Replace the full contents of `frontend/src/components/map/InteractiveMap.test.tsx`:

```tsx
/**
 * Regression test for click routing: clicking a plotted entity must call
 * onMarkerClick (not onAreaClick); clicking bare globe must call
 * onAreaClick with a bbox around the clicked point (not onMarkerClick).
 *
 * Real Cesium needs a real WebGL context that jsdom can't provide, so
 * `cesium` is mocked here to a minimal fake that records the handlers
 * InteractiveMap registers for LEFT_CLICK — letting this test invoke
 * exactly that handler and assert which callback fires, without a real
 * globe/WebGL/network.
 */
import { render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Entity } from '../../lib/types'
import { InteractiveMap } from './InteractiveMap'

// vi.mock factories are hoisted above imports, so any state they close over
// must go through vi.hoisted() rather than a plain top-level `const`/`let`.
const { inputActions, fakeViewer } = vi.hoisted(() => {
  const inputActions: Record<string, (arg: unknown) => void> = {}
  const fakeViewer = {
    scene: {
      canvas: {},
      pick: vi.fn(),
      globe: { ellipsoid: {} },
    },
    camera: { pickEllipsoid: vi.fn() },
    entities: {
      add: vi.fn((opts: unknown) => ({ opts })),
      removeAll: vi.fn(),
    },
    destroy: vi.fn(),
  }
  return { inputActions, fakeViewer }
})

vi.mock('cesium', () => ({
  Viewer: vi.fn(() => fakeViewer),
  OpenStreetMapImageryProvider: vi.fn(),
  ScreenSpaceEventHandler: vi.fn(() => ({
    setInputAction: vi.fn((fn: (arg: unknown) => void, type: string) => {
      inputActions[type] = fn
    }),
    destroy: vi.fn(),
  })),
  ScreenSpaceEventType: { LEFT_CLICK: 'LEFT_CLICK', MOUSE_MOVE: 'MOUSE_MOVE' },
  Cartesian3: { fromDegrees: vi.fn() },
  Cartographic: { fromCartesian: vi.fn(() => ({ latitude: 60.0, longitude: 15.0 })) },
  Math: { toDegrees: vi.fn((v: number) => v) },
  Color: { fromCssColorString: vi.fn(() => ({ withAlpha: vi.fn(() => ({})) })) },
}))

const ENTITY: Entity = {
  entity_id: 'OPENSKY-abc123',
  entity_class: 'VEHICLE',
  entity_subclass: 'VEHICLE.AIR_VEHICLE.FIXED_WING_AIRCRAFT',
  label: 'SAS123',
  status: 'active',
  confidence: 'B2',
  source_ref: 'aircraft',
  aliases: [],
  first_observed: null,
  last_observed: null,
  attrs: { lat: 59.33, lon: 18.06 },
}

afterEach(() => {
  vi.clearAllMocks()
  delete inputActions.LEFT_CLICK
  delete inputActions.MOUSE_MOVE
})

describe('InteractiveMap click routing', () => {
  it('calls onMarkerClick and not onAreaClick when a plotted entity is picked', () => {
    const onAreaClick = vi.fn()
    const onMarkerClick = vi.fn()
    render(
      <InteractiveMap
        points={[{ entity: ENTITY, lat: 59.33, lon: 18.06 }]}
        onAreaClick={onAreaClick}
        onMarkerClick={onMarkerClick}
      />,
    )

    const addedCesiumEntity = fakeViewer.entities.add.mock.results[0].value
    fakeViewer.scene.pick.mockReturnValue({ id: addedCesiumEntity })

    expect(inputActions.LEFT_CLICK).toBeDefined()
    inputActions.LEFT_CLICK!({ position: {} })

    expect(onMarkerClick).toHaveBeenCalledWith('OPENSKY-abc123')
    expect(onAreaClick).not.toHaveBeenCalled()
  })

  it('picking bare globe (no entity under the click) calls onAreaClick with the expected bbox', () => {
    const onAreaClick = vi.fn()
    const onMarkerClick = vi.fn()
    render(
      <InteractiveMap
        points={[{ entity: ENTITY, lat: 59.33, lon: 18.06 }]}
        onAreaClick={onAreaClick}
        onMarkerClick={onMarkerClick}
      />,
    )

    fakeViewer.scene.pick.mockReturnValue(undefined)
    fakeViewer.camera.pickEllipsoid.mockReturnValue({})

    expect(inputActions.LEFT_CLICK).toBeDefined()
    inputActions.LEFT_CLICK!({ position: {} })

    expect(onAreaClick).toHaveBeenCalledWith([60.0 - 1.3, 60.0 + 1.3, 15.0 - 2.5, 15.0 + 2.5])
    expect(onMarkerClick).not.toHaveBeenCalled()
  })

  it('a click that misses the globe entirely calls neither callback', () => {
    const onAreaClick = vi.fn()
    const onMarkerClick = vi.fn()
    render(<InteractiveMap points={[]} onAreaClick={onAreaClick} onMarkerClick={onMarkerClick} />)

    fakeViewer.scene.pick.mockReturnValue(undefined)
    fakeViewer.camera.pickEllipsoid.mockReturnValue(undefined)

    inputActions.LEFT_CLICK!({ position: {} })

    expect(onAreaClick).not.toHaveBeenCalled()
    expect(onMarkerClick).not.toHaveBeenCalled()
  })
})
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/components/map/InteractiveMap.test.tsx`
Expected: FAIL — `InteractiveMap.tsx` still imports the real `leaflet` package (untouched until Step 5), never touches the mocked `cesium` module, so `inputActions.LEFT_CLICK` is never populated and `expect(inputActions.LEFT_CLICK).toBeDefined()` fails. (If real Leaflet throws first when rendered against jsdom's incomplete layout APIs, that's an equally valid red result — either way this step must not pass.)

- [ ] **Step 5: Rewrite `InteractiveMap.tsx` for Cesium**

Replace the full contents of `frontend/src/components/map/InteractiveMap.tsx`:

```tsx
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
  useEffect(() => {
    onAreaClickRef.current = onAreaClick
    onMarkerClickRef.current = onMarkerClick
  }, [onAreaClick, onMarkerClick])

  useEffect(() => {
    if (!containerRef.current || viewerRef.current) return
    const viewer = new Cesium.Viewer(containerRef.current, {
      imageryProvider: new Cesium.OpenStreetMapImageryProvider({ url: 'https://tile.openstreetmap.org/' }),
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
    <div
      ref={containerRef}
      role="application"
      aria-label="Interactive 3D globe — pan, zoom, and click an area to search entities near it"
      className="h-full min-h-[420px] w-full"
    />
  )
}
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd frontend && npx vitest run src/components/map/InteractiveMap.test.tsx`
Expected: PASS (3 tests)

- [ ] **Step 7: Type-check and lint**

Run: `cd frontend && npx tsc -b && npx oxlint`
Expected: no errors. (`viewer.entities.remove`/`.add`/`.removeAll` and the Cesium types referenced above are all part of `cesium`'s public TypeScript types, so `tsc -b` validates the real API shape even though the test mocks it.)

- [ ] **Step 8: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/vite.config.ts \
  frontend/src/components/map/InteractiveMap.tsx frontend/src/components/map/InteractiveMap.test.tsx
git commit -m "feat(map): replace Leaflet with a keyless Cesium 3D globe"
```

---

### Task 2: Hover tooltip on entity points

**Files:**
- Modify: `frontend/src/components/map/InteractiveMap.tsx`

**Interfaces:**
- Consumes: same as Task 1 (no new external interfaces).
- Produces: no change to `InteractiveMap`'s exported signature.

There is no automated test for this step — per the spec's Testing section, hover-tooltip coverage isn't added (jsdom/mocked-Cesium can't meaningfully assert on cursor-relative positioning). Verify manually via the dev server.

- [ ] **Step 1: Add a tooltip element and MOUSE_MOVE routing**

In `frontend/src/components/map/InteractiveMap.tsx`:

1. Add a `tooltipRef` and a `pointsRef` (so the mouse-move handler, registered once in the mount effect, always sees the latest `points` instead of the stale array from mount time):

```tsx
const tooltipRef = useRef<HTMLDivElement>(null)
const pointsRef = useRef(points)
```

2. In the `[points]` effect, keep `pointsRef` in sync — add this line right after `entityIdByCesiumId.current = new Map()`:

```tsx
pointsRef.current = points
```

3. In the mount effect, right after the existing `handler.setInputAction(..., Cesium.ScreenSpaceEventType.LEFT_CLICK)` call, register a second input action:

```tsx
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
```

4. Replace the returned JSX to wrap the container and add the tooltip element:

```tsx
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
```

- [ ] **Step 2: Run the existing test suite to confirm no regression**

Run: `cd frontend && npx vitest run src/components/map/InteractiveMap.test.tsx`
Expected: PASS (still 3 tests — this task adds no new automated test, per the spec)

- [ ] **Step 3: Manual verification**

Run: `cd frontend && npm run dev`, open the Map tab, poll a live feed with geo-tagged entities (e.g. `POST /feeds/earthquakes/poll` via `http://localhost:8000/docs`), then on the map page hover over a plotted point.
Expected: a small label showing the entity's name follows the cursor near the point and disappears when the cursor moves off it.

- [ ] **Step 4: Type-check and lint**

Run: `cd frontend && npx tsc -b && npx oxlint`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/map/InteractiveMap.tsx
git commit -m "feat(map): add hover tooltip on Cesium globe entity points"
```

---

### Task 3: Go global — drop the hardcoded Sweden bbox filter

**Files:**
- Modify: `frontend/src/components/map/MapPage.tsx:10,49`

**Interfaces:**
- Consumes: `api.listEntities({ limit, offset, bbox? })` from `frontend/src/lib/api.ts` (unchanged — `bbox` is already optional there, and `backend/app/api/entities.py` already treats an omitted `bbox` as "no geo filter", confirmed during design; no backend change needed).
- Produces: no change to `MapPage`'s exported signature.

- [ ] **Step 1: Remove the `SWEDEN_BBOX` constant and its usage**

In `frontend/src/components/map/MapPage.tsx`, delete line 10:

```tsx
const SWEDEN_BBOX = '55.0,69.1,10.9,24.2'
```

Then change the initial fetch (originally line 49) from:

```tsx
      .listEntities({ limit: 200, offset: 0, bbox: SWEDEN_BBOX })
```

to:

```tsx
      .listEntities({ limit: 200, offset: 0 })
```

- [ ] **Step 2: Run the frontend test suite**

Run: `cd frontend && npx vitest run`
Expected: PASS — there is no `MapPage.test.tsx` in this codebase today, so this step confirms no other suite (e.g. `InteractiveMap.test.tsx`) broke.

- [ ] **Step 3: Type-check**

Run: `cd frontend && npx tsc -b`
Expected: no errors (confirms no other file still references the removed `SWEDEN_BBOX` export — it was module-local, not exported, so this is a redundant-but-cheap safety check).

- [ ] **Step 4: Manual verification**

Run: `cd frontend && npm run dev`, open the Map tab.
Expected: the globe still loads and plots existing (Swedish) entities exactly as before — this step only widens the filter, it doesn't change what data currently exists.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/map/MapPage.tsx
git commit -m "feat(map): drop hardcoded Sweden bbox filter on initial entity load"
```

---

### Task 4: Remove the unused Leaflet dependency

**Files:**
- Modify: `frontend/package.json`

**Interfaces:** None — pure dependency cleanup, no code changes.

- [ ] **Step 1: Confirm nothing still imports Leaflet**

Run: `cd frontend && grep -rn "leaflet" src`
Expected: no output. (Task 1 already removed the only two files that imported it — `InteractiveMap.tsx` and `InteractiveMap.test.tsx`.)

- [ ] **Step 2: Remove the dependencies**

```bash
cd frontend
npm uninstall leaflet @types/leaflet
```

- [ ] **Step 3: Run the full test suite and build**

Run: `cd frontend && npx vitest run && npx tsc -b && npx vite build`
Expected: all pass — confirms the app builds and tests pass with `leaflet` fully removed.

- [ ] **Step 4: Commit**

```bash
git add frontend/package.json frontend/package-lock.json
git commit -m "chore(map): remove unused leaflet dependency"
```
