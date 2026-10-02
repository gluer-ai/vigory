import { describe, expect, it } from 'vitest'
import { degrees, diameterFor, layoutSpheres, MAX_DIAMETER, MIN_DIAMETER, neighbourhood, proposalToGraph, type SphereData } from './sphereGraph'
import type { SandboxProposal } from './types'

const node = (id: string, kind: 'new' | 'existing' = 'new') => ({ id, label: id, entity_subclass: 'PERSON.X', kind })
const edge = (id: string, source: string, target: string) => ({ id, source, target, link_type: 'commands' })

describe('proposalToGraph', () => {
  const ent = (id: string) => ({
    entity_id: id, entity_class: 'PERSON', entity_subclass: 'PERSON.X', label: id, aliases: [], status: 'active',
    confidence: 'C3', source_ref: '', first_observed: null, last_observed: null, attrs: {},
  })
  const link = (id: string, s: string, t: string) => ({
    link_id: id, link_type: 'commands', source_entity: s, target_entity: t, direction: 'directed', inverse_type: null,
    valid_from: null, valid_to: null, assertion_status: 'reported', confidence: 'C3', source_ref: '', attrs: {},
  })
  const proposal = (over: Partial<SandboxProposal> = {}): SandboxProposal => ({
    batch: { batch_id: 'B', status: 'proposed', source_text: '', entities: [ent('A'), ent('B')], links: [link('L1', 'A', 'B'), link('L2', 'B', 'REAL')], rejected_entities: [], rejected_links: [] },
    merged: [], skipped: [], notes: [], existing_entities: [{ entity_id: 'REAL', label: 'Real one', entity_subclass: 'ORGANIZATION.Y' }], ...over,
  } as SandboxProposal)

  it('draws new entities and the real entities the links attach to', () => {
    const g = proposalToGraph(proposal())
    expect(g.nodes.map((n) => [n.id, n.kind])).toEqual([['A', 'new'], ['B', 'new'], ['REAL', 'existing']])
    expect(g.edges.map((e) => e.id)).toEqual(['L1', 'L2'])
  })

  it('drops links with an end it cannot draw, and self-links', () => {
    const g = proposalToGraph(proposal({ existing_entities: [] }))
    expect(g.edges.map((e) => e.id)).toEqual(['L1']) // L2 points at REAL, which is unknown
  })

  it('never lists an entity twice', () => {
    const g = proposalToGraph(proposal({ existing_entities: [{ entity_id: 'A', label: 'dup', entity_subclass: 'X.Y' }] }))
    expect(g.nodes.filter((n) => n.id === 'A')).toHaveLength(1)
    expect(g.nodes.find((n) => n.id === 'A')?.kind).toBe('new')
  })
})

describe('sizes', () => {
  it('grows with connections and is capped', () => {
    expect(diameterFor(0)).toBe(MIN_DIAMETER)
    expect(diameterFor(3)).toBeGreaterThan(diameterFor(1))
    expect(diameterFor(500)).toBe(MAX_DIAMETER)
  })
  it('counts both ends of every link', () => {
    const d: SphereData = { nodes: [node('A'), node('B'), node('C')], edges: [edge('1', 'A', 'B'), edge('2', 'A', 'C')] }
    expect([...degrees(d)]).toEqual([['A', 2], ['B', 1], ['C', 1]])
  })
})

describe('layoutSpheres', () => {
  const ring = (n: number): SphereData => ({
    nodes: Array.from({ length: n }, (_, i) => node(`N${String(i).padStart(2, '0')}`)),
    edges: Array.from({ length: n }, (_, i) => edge(`E${i}`, `N${String(i).padStart(2, '0')}`, `N${String((i + 1) % n).padStart(2, '0')}`)),
  })
  const hub = (n: number): SphereData => ({
    nodes: [node('HUB'), ...Array.from({ length: n }, (_, i) => node(`S${String(i).padStart(2, '0')}`))],
    edges: Array.from({ length: n }, (_, i) => edge(`E${i}`, 'HUB', `S${String(i).padStart(2, '0')}`)),
  })

  it('handles an empty graph and a single sphere', () => {
    expect(layoutSpheres({ nodes: [], edges: [] })).toEqual([])
    expect(layoutSpheres({ nodes: [node('A')], edges: [] })).toEqual([{ id: 'A', x: 0, y: 0, diameter: MIN_DIAMETER }])
  })

  it('is deterministic', () => {
    expect(layoutSpheres(hub(9))).toEqual(layoutSpheres(hub(9)))
  })

  it.each([['ring', ring(12)], ['hub', hub(25)], ['disconnected', { nodes: ['A', 'B', 'C', 'D', 'E'].map((i) => node(i)), edges: [] }]])(
    'never lets two spheres overlap (%s)', (_name, data) => {
      const p = layoutSpheres(data as SphereData)
      for (let i = 0; i < p.length; i++)
        for (let j = i + 1; j < p.length; j++)
          expect(Math.hypot(p[i].x - p[j].x, p[i].y - p[j].y)).toBeGreaterThanOrEqual((p[i].diameter + p[j].diameter) / 2)
    })

  it('keeps linked spheres closer together than unlinked ones', () => {
    const data: SphereData = { nodes: ['A', 'B', 'C', 'D'].map((i) => node(i)), edges: [edge('1', 'A', 'B'), edge('2', 'C', 'D')] }
    const p = Object.fromEntries(layoutSpheres(data).map((q) => [q.id, q]))
    const dist = (a: string, b: string) => Math.hypot(p[a].x - p[b].x, p[a].y - p[b].y)
    expect(dist('A', 'B')).toBeLessThan(dist('A', 'C'))
    expect(dist('C', 'D')).toBeLessThan(dist('B', 'D'))
  })

  it('puts a hub in the middle of its neighbours', () => {
    const p = layoutSpheres(hub(10))
    const h = p.find((q) => q.id === 'HUB')!
    const others = p.filter((q) => q.id !== 'HUB')
    const mean = { x: others.reduce((s, q) => s + q.x, 0) / others.length, y: others.reduce((s, q) => s + q.y, 0) / others.length }
    expect(Math.hypot(h.x - mean.x, h.y - mean.y)).toBeLessThan(80)
    expect(h.diameter).toBeGreaterThan(others[0].diameter) // and drawn bigger
  })
})

describe('neighbourhood', () => {
  const d: SphereData = { nodes: ['A', 'B', 'C', 'D'].map((i) => node(i)), edges: [edge('1', 'A', 'B'), edge('2', 'B', 'C'), edge('3', 'C', 'D')] }
  it('is null with nothing selected, else the selection and what touches it', () => {
    expect(neighbourhood(d, null)).toBeNull()
    const n = neighbourhood(d, 'B')!
    expect([...n.nodes].sort()).toEqual(['A', 'B', 'C'])
    expect([...n.edges].sort()).toEqual(['1', '2'])
  })
})

describe('layout stays compact', () => {
  // Regression: unconnected groups used to push each other thousands of units apart, so
  // the picture could not be fitted on screen. These bounds keep it framed at a readable zoom.
  const mk = (n: number, pairs: number[][]): SphereData => ({
    nodes: Array.from({ length: n }, (_, i) => node(`B${i}`)),
    edges: pairs.map(([a, b], i) => edge(`X${i}`, `B${a}`, `B${b}`)),
  })
  const extent = (d: SphereData) => {
    const p = layoutSpheres(d)
    const xs = p.map((q) => q.x), ys = p.map((q) => q.y)
    return Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys))
  }
  it('keeps many separate small groups in one picture', () => {
    const pairs = [[0, 8], [1, 9], [2, 10], [4, 11], [3, 8], [12, 17], [5, 13], [8, 18], [16, 19], [13, 15], [22, 23], [8, 23], [14, 23], [7, 13], [9, 11], [8, 17], [11, 17], [15, 20], [22, 21], [6, 14]]
    expect(extent(mk(24, pairs))).toBeLessThan(1600)
  })
  it('keeps unconnected spheres together too', () => {
    expect(extent(mk(12, []))).toBeLessThan(1200)
  })
  it('keeps a small graph small and a 40-way hub bounded', () => {
    expect(extent(mk(4, [[0, 1], [0, 2], [3, 0]]))).toBeLessThan(700)
    expect(extent(mk(41, Array.from({ length: 40 }, (_, i) => [0, i + 1])))).toBeLessThan(1700)
  })
})
