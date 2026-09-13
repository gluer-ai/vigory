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
  Viewer: vi.fn(function() { return fakeViewer }),
  OpenStreetMapImageryProvider: vi.fn(),
  ImageryLayer: {
    fromProviderAsync: vi.fn(() => Promise.resolve({})),
  },
  ScreenSpaceEventHandler: vi.fn(function() {
    return {
      setInputAction: vi.fn((fn: (arg: unknown) => void, type: string) => {
        inputActions[type] = fn
      }),
      destroy: vi.fn(),
    }
  }),
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
