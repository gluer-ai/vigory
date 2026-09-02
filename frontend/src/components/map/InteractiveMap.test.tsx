/**
 * Regression test for the marker/area-click bug: clicking a plotted marker
 * used to also fire the map's own click handler underneath it, triggering
 * an unwanted area search on top of the intended entity navigation. The
 * fix calls L.DomEvent.stopPropagation() on the marker's click event.
 *
 * Real Leaflet needs real browser layout (getBoundingClientRect etc.) that
 * jsdom can't provide reliably, so `leaflet` is mocked here to a minimal
 * fake that records the handlers InteractiveMap registers — letting this
 * test invoke exactly the marker click handler and assert which callback
 * fires and that propagation is stopped, without a real map/DOM/network.
 */
import { render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Entity } from '../../lib/types'
import { InteractiveMap } from './InteractiveMap'

// vi.mock factories are hoisted above imports, so any state they close over
// must go through vi.hoisted() rather than a plain top-level `const`/`let`.
const { stopPropagation, handlers } = vi.hoisted(() => ({
  stopPropagation: vi.fn(),
  handlers: {
    marker: undefined as ((e: unknown) => void) | undefined,
    map: undefined as ((e: unknown) => void) | undefined,
  },
}))

vi.mock('leaflet', () => {
  const fakeLayer = { addTo: vi.fn().mockReturnThis(), clearLayers: vi.fn() }
  const fakeMap = {
    setView: vi.fn().mockReturnThis(),
    on: vi.fn((event: string, handler: (e: unknown) => void) => {
      if (event === 'click') handlers.map = handler
      return fakeMap
    }),
    remove: vi.fn(),
  }
  const fakeMarker = {
    bindTooltip: vi.fn().mockReturnThis(),
    on: vi.fn((event: string, handler: (e: unknown) => void) => {
      if (event === 'click') handlers.marker = handler
      return fakeMarker
    }),
    addTo: vi.fn().mockReturnThis(),
  }
  return {
    default: {
      map: vi.fn(() => fakeMap),
      tileLayer: vi.fn(() => ({ addTo: vi.fn() })),
      layerGroup: vi.fn(() => fakeLayer),
      circleMarker: vi.fn(() => fakeMarker),
      circle: vi.fn(() => ({ addTo: vi.fn().mockReturnThis(), remove: vi.fn() })),
      LatLngBounds: vi.fn().mockImplementation(function () {
        return { pad: vi.fn().mockReturnThis() }
      }),
      DomEvent: { stopPropagation },
    },
  }
})

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
  handlers.marker = undefined
  handlers.map = undefined
})

describe('InteractiveMap marker click', () => {
  it('calls onMarkerClick and stops propagation, without triggering onAreaClick', () => {
    const onAreaClick = vi.fn()
    const onMarkerClick = vi.fn()
    render(
      <InteractiveMap
        points={[{ entity: ENTITY, lat: 59.33, lon: 18.06 }]}
        onAreaClick={onAreaClick}
        onMarkerClick={onMarkerClick}
      />,
    )

    expect(handlers.marker).toBeDefined()
    const fakeEvent = { latlng: { lat: 59.33, lng: 18.06 } }
    handlers.marker!(fakeEvent)

    expect(stopPropagation).toHaveBeenCalledWith(fakeEvent)
    expect(onMarkerClick).toHaveBeenCalledWith('OPENSKY-abc123')
    expect(onAreaClick).not.toHaveBeenCalled()
  })

  it('map background click (not a marker) still triggers onAreaClick', () => {
    const onAreaClick = vi.fn()
    const onMarkerClick = vi.fn()
    render(
      <InteractiveMap
        points={[{ entity: ENTITY, lat: 59.33, lon: 18.06 }]}
        onAreaClick={onAreaClick}
        onMarkerClick={onMarkerClick}
      />,
    )

    expect(handlers.map).toBeDefined()
    handlers.map!({ latlng: { lat: 60.0, lng: 15.0 } })

    expect(onAreaClick).toHaveBeenCalledWith([
      60.0 - 1.3,
      60.0 + 1.3,
      15.0 - 2.5,
      15.0 + 2.5,
    ])
    expect(onMarkerClick).not.toHaveBeenCalled()
  })
})
