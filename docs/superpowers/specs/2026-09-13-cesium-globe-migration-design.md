# Cesium 3D globe migration — design

## Context

The user wants live public-camera feeds "like [God's Eye View](https://github.com/bilawalsidhu/gods-eye-view)" added to this app. Investigation found:

- This app already has a structurally equivalent piece: `backend/app/feeds/trafikverket.py` polls Sweden's traffic-camera API into graph entities carrying a `photo_url`, but nothing renders that photo — `Inspector.tsx` prints it as a text string.
- God's Eye View's camera layer ("CCTV Mesh") is public traffic-camera catalogs from Austin, Caltrans, and TfL London, projected into a Cesium photorealistic 3D globe with estimated viewshed cones.
- This app's map (`InteractiveMap.tsx`) is a 2D Leaflet map hard-bounded to Sweden (`SWEDEN_BOUNDS`), used by every geo-tagged feed (situations, cameras, earthquakes, aircraft, vessels), not just cameras.

Given the size of full replication, the work was decomposed into ordered sub-projects:

1. **Cesium 3D globe migration** (this spec) — foundational; every other phase depends on it.
2. Global camera source integrations (Austin, Caltrans, TfL, alongside Trafikverket) as new backend feed pollers.
3. Live camera viewing UI — render `photo_url` as an actual image, with refresh.
4. *(Not yet scoped)* viewshed cones, camera pose calibration, clustering.

This spec covers **phase 1 only**: swap the map's rendering engine from 2D Leaflet to a 3D Cesium globe, with no new camera capability yet. The user confirmed this build order explicitly (globe first, cameras after) and confirmed Cesium (not just loosening Leaflet's bounds) as the target.

## Scope

**In scope:**
- Replace Leaflet with CesiumJS inside `InteractiveMap.tsx`, preserving its existing public contract (`points`, `onAreaClick`, `onMarkerClick`).
- Keyless base globe: `Cesium.OpenStreetMapImageryProvider` + default `EllipsoidTerrainProvider`. No Cesium ion token, no Google Maps key.
- Per-entity point markers colored via the existing `--color-class-*` CSS tokens (`entityClass.ts` stays the single source of truth for the color scheme).
- Click routing: pick an entity vs. pick bare globe, using one `ScreenSpaceEventHandler` on `LEFT_CLICK` (replaces Leaflet's marker/`stopPropagation` mechanism).
- Hover tooltip via a custom positioned HTML element (replaces Leaflet's `bindTooltip`).
- Remove the Sweden-only camera bound (`SWEDEN_BOUNDS.pad(0.5)` `maxBounds`) — no geographic camera constraint.
- Drop `MapPage.tsx`'s hardcoded `SWEDEN_BBOX` initial fetch filter (the backend already treats an omitted `bbox` as "no filter" — no backend change needed).
- Update `InteractiveMap.test.tsx` to mock `cesium` instead of `leaflet`, preserving both existing test cases (marker click → `onMarkerClick`; background click → `onAreaClick` with the expected bbox).
- Add `cesium` + `vite-plugin-cesium` dependencies; wire `vite.config.ts`. Remove `leaflet` + `@types/leaflet` once nothing references them.

**Explicitly out of scope (deferred to later phases or never):**
- Any new camera data source (Austin/Caltrans/TfL) — phase 2.
- Rendering `photo_url` as an image anywhere — phase 3.
- Photorealistic 3D terrain/imagery (Cesium ion token, Google Photorealistic 3D Tiles) — noted as a future optional upgrade behind an env var, not built now.
- Viewshed cones, camera pose calibration gizmo, clustering, LOD — phase 4, unscoped.
- Voice control, cockpit mode, detection overlays, satellite tracking, or any other God's Eye View capability beyond the camera-feed line of work.
- A 2D-map fallback for browsers/environments without WebGL. Not planned; acceptable risk for this project's audience.
- Making the "click area → search radius" (`CLICK_RADIUS_LAT`/`LON` in `mapGeo.ts`) zoom/altitude-aware. It stays a fixed degree-box, unchanged from today, even though a fixed box is a worse fit once the camera can be zoomed to see the whole planet or a single street. This is a known, deliberate limitation — a real improvement, but orthogonal to "swap the rendering engine."

## Architecture

No new components at the page level. `InteractiveMap.tsx` keeps its existing props (`points: MapPoint[]`, `onAreaClick`, `onMarkerClick`) so `MapPage.tsx` requires only the one `SWEDEN_BBOX` removal described above. `mapGeo.ts` (bbox-around-point math, haversine distance) is Cesium-agnostic today and stays that way — no changes.

Inside `InteractiveMap.tsx`:
- A `useRef`-held `Cesium.Viewer`, created once on mount (mirrors the current `mapRef`/`useEffect` pattern used for the Leaflet `L.Map`).
- Imagery configured at viewer construction: `new Cesium.OpenStreetMapImageryProvider({ url: 'https://tile.openstreetmap.org/' })`. Terrain is left at Cesium's default (`EllipsoidTerrainProvider`) — no explicit terrain configuration needed.
- A plain array (or `viewer.entities` collection itself, queried by id) tracking which `Cesium.Entity` corresponds to which `MapPoint`, rebuilt on every `points` change: `viewer.entities.removeAll()` then re-add (mirrors the current `layer.clearLayers()` + re-add pattern).
- Color resolution: a small local helper reads `getComputedStyle(document.documentElement).getPropertyValue(colorVar)` and passes the resulting CSS color string to `Cesium.Color.fromCssColorString(...)`. This helper is Cesium-specific and stays local to this file — `entityClass.ts` continues to only know about CSS var names, not Cesium types, keeping the Cesium dependency contained to the map component (per the project's existing "isolate the imperative map library behind one component" pattern).
- One `Cesium.ScreenSpaceEventHandler` registered on `viewer.scene.canvas`:
  - `LEFT_CLICK`: `viewer.scene.pick(click.position)` first. If the pick resolves to one of our own entities (matched via a `Map<Cesium.Entity, string /* entity_id */>` built alongside the entities collection), call `onMarkerClickRef.current(entityId)` and stop. Otherwise, `viewer.camera.pickEllipsoid(click.position, viewer.scene.globe.ellipsoid)` → `Cesium.Cartographic.fromCartesian` → `Cesium.Math.toDegrees` on `latitude`/`longitude` → the existing `bboxAroundPoint(lat, lon)` from `mapGeo.ts` → `onAreaClickRef.current(bbox)`. If `pickEllipsoid` returns `undefined` (click missed the globe, e.g. clicked space background at low zoom), do nothing.
  - `MOUSE_MOVE`: pick under `movement.endPosition`; if it resolves to a tracked entity, show a small absolute-positioned `<div>` (rendered by the React component, position updated imperatively via a ref, not re-rendered through React state on every mouse move) near the cursor with `entity.label`; otherwise hide it.
- `onAreaClickRef`/`onMarkerClickRef` pattern (refs updated via `useEffect`, read inside the Cesium callbacks) is carried over unchanged from the current Leaflet implementation — it already exists to avoid stale closures over the latest callback props.
- No `maxBounds`/`minZoom`/`maxZoom` camera constraints are configured — the camera is free to view the whole globe or zoom to street level anywhere.

## Data flow

Unchanged end-to-end. `MapPage.tsx` fetches entities from `GET /entities` (now without a `bbox` filter), converts geo-tagged ones to `MapPoint[]`, and passes them to `InteractiveMap`. Clicking a marker or an empty area still flows back through `onMarkerClick`/`onAreaClick` into `MapPage.tsx`'s existing `handleMarkerClick`/`handleAreaClick` → `runNearbySearch` → `Inspector`/`EntityList` rendering. None of that changes; only the picking mechanism producing the callback arguments changes.

## Error handling

- If Cesium fails to initialize (e.g. WebGL unavailable), the current design has no explicit fallback UI — the viewer construction may throw or render a blank canvas. Given this is an accepted, explicitly out-of-scope risk (see Scope), no new error-boundary or fallback message is being added in this phase. If this turns out to be unacceptable in practice, that's a follow-up, not a blocker here.
- Tile-load failures from `OpenStreetMapImageryProvider` (e.g. offline) are handled by Cesium's own built-in imagery-provider retry/error-event behavior; no custom handling is added, matching how the current Leaflet `tileLayer` has no custom error handling either.
- `pickEllipsoid` returning `undefined` (click missed the globe) is a normal no-op, not an error.

## Testing

`InteractiveMap.test.tsx` currently fully mocks the `leaflet` module (`vi.mock('leaflet', ...)`) to drive click-routing logic in jsdom, since real Leaflet needs real browser layout jsdom can't provide. The same strategy applies to Cesium, which additionally needs real WebGL that jsdom can't provide either:

- Mock `cesium`'s `Viewer` (constructor + `.entities.add/removeAll`, `.scene.pick`, `.scene.canvas`, `.camera.pickEllipsoid`, `.scene.globe.ellipsoid`, `.destroy`), `ScreenSpaceEventHandler` (constructor + `.setInputAction`, capturing the registered `LEFT_CLICK`/`MOUSE_MOVE` handlers the same way the current test captures Leaflet's `on('click', ...)` handler), `ScreenSpaceEventType` (`{ LEFT_CLICK, MOUSE_MOVE }` enum-like object), `Cartographic.fromCartesian`, `Math.toDegrees` (Cesium's own, under `Cesium.Math`), `Color.fromCssColorString`, and `OpenStreetMapImageryProvider`/`EllipsoidTerrainProvider` (constructors only, never invoked meaningfully by the test).
- Both existing test cases are preserved with the same assertions: (1) picking a tracked entity calls `onMarkerClick` with its `entity_id` and does not call `onAreaClick`; (2) picking bare globe (mocked `pickEllipsoid` returning a fixed Cartesian, mocked `Cartographic.fromCartesian`/`toDegrees` returning fixed lat/lon) calls `onAreaClick` with the exact `bboxAroundPoint` result and does not call `onMarkerClick`.
- No new test coverage is added for the hover tooltip or for real rendering — consistent with the current test file's scope (it only covers click routing, not visual rendering).

## Dependencies

- Add: `cesium`, `vite-plugin-cesium` (dev dependency; copies Cesium's static `Workers`/`Assets`/`ThirdParty`/`Widgets` into the build and sets `window.CESIUM_BASE_URL` — without it Cesium's web workers fail to resolve at runtime).
- Remove: `leaflet`, `@types/leaflet`, once `InteractiveMap.tsx`/`InteractiveMap.test.tsx` no longer reference them (confirmed via grep these are the only two referencing files today).
- `vite.config.ts` gains the `vite-plugin-cesium` plugin alongside the existing `react()`/`tailwindcss()` plugins.

## Risks

- First WebGL dependency in the frontend (`@xyflow/react`'s canvas is not WebGL) and a meaningfully heavier bundle than Leaflet. No bundle-size budget is defined or enforced here.
- No 2D fallback for WebGL-unavailable environments (explicitly out of scope, see above).
