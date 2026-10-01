import { describe, expect, it } from 'vitest'
import {
  addEdge,
  addGraphMatch,
  expandAround,
  freeSpotsAround,
  interleaveByRelation,
  pickAutoMatch,
  rankMatches,
  searchTerms,
  SANDBOX_MAX_NODES,
  addScenario,
  brokenLinksIfReclassified,
  addNewEntity,
  childCount,
  containerExists,
  copyEntityIntoSandbox,
  explodeInto,
  levelOf,
  pathTo,
  removeEdge,
  edgeStatus,
  nodeStatus,
  removeNode,
  revertNode,
  summarize,
  updateEdge,
  updateNode,
  validLinkTypes,
  type SandboxDoc,
} from './sandboxModel'
import type { ClassDef, Link, LinkDef, ScenarioExtract } from './types'

const cls = (key: string, label: string, parent: string | null = null): ClassDef => ({
  key, label, parent_key: parent, level: parent ? 2 : 1, notes: null,
})
const CLASSES = [
  cls('PERSON', 'Person'),
  cls('ORGANIZATION', 'Organization'),
  cls('LOCATION', 'Location'),
  cls('INFORMATION_OBJECT', 'Information Object'),
]
const def = (type: string, domain: string, range: string): LinkDef => ({
  type, domain, range, category: null, directionality: null, inverse: null, symmetric: null, transitive: null, notes: null,
})
const DEFS = [
  def('commands', 'Person', 'Organization'),
  def('headquartered_in', 'Organization', 'Location; Facility'),
  def('related_to', 'Any', 'Any'),
  def('affiliated_with', 'Person; Organization', 'Organization'),
]

const node = (id: string, sub: string, extra = {}) => ({
  id, origin: 'graph' as const, label: id, entity_subclass: sub, x: 0, y: 0,
  base: { label: id, entity_subclass: sub }, parent: null, ...extra,
})
const doc = (): SandboxDoc => ({
  nodes: [node('P', 'PERSON.MILITARY_PERSONNEL'), node('O', 'ORGANIZATION.COMMERCIAL_ENTITY')],
  edges: [{ id: 'L1', origin: 'graph', source: 'P', target: 'O', link_type: 'commands', base_type: 'commands', parent: null }],
})

describe('validLinkTypes', () => {
  it('offers only types whose domain and range accept both ends', () => {
    const types = (a: string, b: string) => validLinkTypes(DEFS, a, b, CLASSES).map((l) => l.type)
    expect(types('PERSON.X', 'ORGANIZATION.Y')).toEqual(['affiliated_with', 'commands', 'related_to'])
    expect(types('ORGANIZATION.Y', 'LOCATION.Z')).toEqual(['headquartered_in', 'related_to'])
    expect(types('LOCATION.Z', 'PERSON.X')).toEqual(['related_to'])
  })

  it('handles multi-word root classes and unknown classes safely', () => {
    const defs = [def('cites', 'Any', 'Information Object')]
    expect(validLinkTypes(defs, 'PERSON.X', 'INFORMATION_OBJECT.OFFICIAL', CLASSES)).toHaveLength(1)
    expect(validLinkTypes(DEFS, 'NOPE.X', 'PERSON.X', CLASSES).map((l) => l.type)).toEqual(['related_to'])
  })
})

describe('change status', () => {
  it('is original until label or class differs from the copied base', () => {
    const n = node('P', 'PERSON.A')
    expect(nodeStatus(n)).toBe('original')
    expect(nodeStatus({ ...n, label: 'renamed' })).toBe('edited')
    expect(nodeStatus({ ...n, entity_subclass: 'PERSON.B' })).toBe('edited')
    expect(nodeStatus({ ...n, origin: 'new', base: null })).toBe('new')
  })

  it('treats a moved node as unchanged', () => {
    expect(nodeStatus({ ...node('P', 'PERSON.A'), x: 500, y: 9 })).toBe('original')
  })

  it('tracks link edits', () => {
    const e = doc().edges[0]
    expect(edgeStatus(e)).toBe('original')
    expect(edgeStatus({ ...e, link_type: 'funds' })).toBe('edited')
    expect(edgeStatus({ ...e, origin: 'new', base_type: null })).toBe('new')
  })
})

describe('editing', () => {
  it('removing an entity also removes its links', () => {
    const next = removeNode(doc(), 'P')
    expect(next.nodes.map((n) => n.id)).toEqual(['O'])
    expect(next.edges).toEqual([])
  })

  it('does not mutate its input', () => {
    const d = doc()
    const snapshot = JSON.stringify(d)
    updateNode(d, 'P', { label: 'x' })
    updateEdge(d, 'L1', { link_type: 'y' })
    removeNode(d, 'P')
    addEdge(d, 'P', 'O', 'funds')
    expect(JSON.stringify(d)).toBe(snapshot)
  })

  it('reverts a node to the copied label and class', () => {
    const edited = updateNode(doc(), 'P', { label: 'X', entity_subclass: 'PERSON.OTHER' })
    expect(nodeStatus(edited.nodes[0])).toBe('edited')
    expect(revertNode(edited, 'P').nodes[0]).toMatchObject({ label: 'P', entity_subclass: 'PERSON.MILITARY_PERSONNEL' })
  })

  it('adds a new entity and links, refusing self-links', () => {
    const { doc: d1, id } = addNewEntity(doc(), 'New Co', 'ORGANIZATION.COMMERCIAL_ENTITY', { x: 1, y: 2 })
    expect(d1.nodes.find((n) => n.id === id)).toMatchObject({ origin: 'new', base: null, x: 1, y: 2 })
    const d2 = addEdge(d1, id, 'O', 'affiliated_with')
    expect(d2.edges.at(-1)).toMatchObject({ origin: 'new', source: id, target: 'O', base_type: null })
    expect(addEdge(d2, 'O', 'O', 'x')).toBe(d2)
  })

  it('copying an entity pulls in real links to what is already on the canvas, once', () => {
    const real: Link[] = [
      { link_id: 'RL1', link_type: 'headquartered_in', source_entity: 'O', target_entity: 'L' },
      { link_id: 'RL2', link_type: 'x', source_entity: 'L', target_entity: 'FAR' }, // FAR not on canvas
      { link_id: 'RL3', link_type: 'x', source_entity: 'P', target_entity: 'O' }, // unrelated to L
    ] as Link[]
    const ent = { entity_id: 'L', label: 'Oslo', entity_subclass: 'LOCATION.CITY' }
    const next = copyEntityIntoSandbox(doc(), ent, { x: 5, y: 5 }, real)
    expect(next.nodes.map((n) => n.id)).toEqual(['P', 'O', 'L'])
    expect(next.edges.map((e) => e.id)).toEqual(['L1', 'RL1'])
    expect(copyEntityIntoSandbox(next, ent, { x: 9, y: 9 }, real)).toBe(next) // idempotent
  })
})

describe('summarize', () => {
  it('counts what the user changed', () => {
    let d = updateNode(doc(), 'P', { label: 'renamed' })
    d = addNewEntity(d, 'N', 'PERSON.X', { x: 0, y: 0 }).doc
    d = addEdge(d, 'P', 'O', 'funds')
    d = updateEdge(d, 'L1', { link_type: 'affiliated_with' })
    expect(summarize(d)).toEqual({ newEntities: 1, editedEntities: 1, newLinks: 1, editedLinks: 1 })
  })
})

describe('brokenLinksIfReclassified', () => {
  it('reports links that the new class would invalidate, and none when compatible', () => {
    const d = doc() // P commands O  (Person -> Organization)
    // Person -> Location: 'commands' needs an Organization target, but P becomes an Organization source -> broken
    expect(brokenLinksIfReclassified(d, 'P', 'LOCATION.CITY', DEFS, CLASSES).map((e) => e.id)).toEqual(['L1'])
    expect(brokenLinksIfReclassified(d, 'P', 'PERSON.OTHER', DEFS, CLASSES)).toEqual([])
    expect(brokenLinksIfReclassified(d, 'O', 'LOCATION.CITY', DEFS, CLASSES).map((e) => e.id)).toEqual(['L1'])
  })

  it('ignores links that are not attached to the node', () => {
    const d = { ...doc(), nodes: [...doc().nodes, node('X', 'LOCATION.CITY')] }
    expect(brokenLinksIfReclassified(d, 'X', 'PERSON.A', DEFS, CLASSES)).toEqual([])
  })
})

// ---- nesting ------------------------------------------------------------------

const edge = (id: string, source: string, target: string, parent: string | null = null) => ({
  id, origin: 'new' as const, source, target, link_type: 'related_to', base_type: null, parent,
})

/** P and O at the top (linked by L1); inside P: A and B (linked by E-in); inside L1: C; inside A: D. */
function nested(): SandboxDoc {
  return {
    nodes: [
      node('P', 'PERSON.X'), node('O', 'ORGANIZATION.Y'),
      node('A', 'LOCATION.Z', { parent: 'P' }), node('B', 'LOCATION.Z', { parent: 'P' }),
      node('C', 'LOCATION.Z', { parent: 'L1' }), node('D', 'LOCATION.Z', { parent: 'A' }),
    ],
    edges: [
      { id: 'L1', origin: 'graph', source: 'P', target: 'O', link_type: 'commands', base_type: 'commands', parent: null },
      edge('E-in', 'A', 'B', 'P'),
    ],
  }
}

describe('levels', () => {
  it('shows only the items that live directly in a container', () => {
    const d = nested()
    expect(levelOf(d, null).nodes.map((n) => n.id)).toEqual(['P', 'O'])
    expect(levelOf(d, null).edges.map((e) => e.id)).toEqual(['L1'])
    expect(levelOf(d, 'P').nodes.map((n) => n.id)).toEqual(['A', 'B'])
    expect(levelOf(d, 'P').edges.map((e) => e.id)).toEqual(['E-in'])
    expect(levelOf(d, 'L1').nodes.map((n) => n.id)).toEqual(['C']) // inside a relation
    expect(levelOf(d, 'A').nodes.map((n) => n.id)).toEqual(['D'])  // two levels down
  })

  it('counts what is directly inside, for entities and relations alike', () => {
    const d = nested()
    expect(childCount(d, 'P')).toBe(3) // A, B and the link between them
    expect(childCount(d, 'L1')).toBe(1)
    expect(childCount(d, 'O')).toBe(0)
  })

  it('treats a missing parent as the top level', () => {
    const d: SandboxDoc = { nodes: [{ ...node('X', 'PERSON.X'), parent: undefined as unknown as null }], edges: [] }
    expect(levelOf(d, null).nodes).toHaveLength(1)
  })

  it('builds a breadcrumb path down to any depth, naming relations by their ends', () => {
    const d = nested()
    expect(pathTo(d, null)).toEqual([])
    expect(pathTo(d, 'D').map((p) => p.id)).toEqual(['P', 'A', 'D'])
    expect(pathTo(d, 'C')).toEqual([
      { id: 'L1', name: 'P commands O' },
      { id: 'C', name: 'C' },
    ])
    expect(pathTo(d, 'gone')).toEqual([])
  })

  it('knows when a container has been removed', () => {
    expect(containerExists(nested(), 'A')).toBe(true)
    expect(containerExists(nested(), 'zzz')).toBe(false)
    expect(containerExists(nested(), null)).toBe(true)
  })
})

describe('removing a container', () => {
  it('removing an entity removes everything inside it, at every depth', () => {
    const d = removeNode(nested(), 'P')
    // P goes, its attached link L1 goes, what was inside L1 (C) goes, and what was inside P (A, B, E-in, D) goes
    expect(d.nodes.map((n) => n.id)).toEqual(['O'])
    expect(d.edges).toEqual([])
  })

  it('removing a relation removes only what is inside it', () => {
    const d = removeEdge(nested(), 'L1')
    expect(d.nodes.map((n) => n.id).sort()).toEqual(['A', 'B', 'D', 'O', 'P'])
    expect(d.edges.map((e) => e.id)).toEqual(['E-in'])
  })

  it('removing a nested entity leaves its siblings and ancestors alone', () => {
    const d = removeNode(nested(), 'A')
    expect(d.nodes.map((n) => n.id).sort()).toEqual(['B', 'C', 'O', 'P'])
    expect(d.edges.map((e) => e.id)).toEqual(['L1']) // E-in attached to A went with it
  })
})

describe('adding at a level', () => {
  it('new and copied entities land inside the current container', () => {
    const { doc: d1, id } = addNewEntity({ nodes: [], edges: [] }, 'Fresh', 'PERSON.X', { x: 1, y: 2 }, 'P')
    expect(d1.nodes.find((n) => n.id === id)?.parent).toBe('P')
    const d2 = copyEntityIntoSandbox(d1, { entity_id: 'R-1', label: 'Real', entity_subclass: 'LOCATION.Z' }, { x: 0, y: 0 }, [], 'P')
    expect(d2.nodes.find((n) => n.id === 'R-1')?.parent).toBe('P')
  })

  it('only pulls real links whose other end is on the same level', () => {
    const d: SandboxDoc = { nodes: [node('IN', 'PERSON.X', { parent: 'box' }), node('OUT', 'PERSON.X')], edges: [] }
    const real = (id: string, other: string): Link => ({
      link_id: id, link_type: 'commands', source_entity: 'R', target_entity: other, direction: 'directed',
      inverse_type: null, valid_from: null, valid_to: null, assertion_status: 'reported', confidence: 'B2', source_ref: '', attrs: {},
    })
    const out = copyEntityIntoSandbox(d, { entity_id: 'R', label: 'R', entity_subclass: 'PERSON.X' }, { x: 0, y: 0 }, [real('LA', 'IN'), real('LB', 'OUT')], 'box')
    expect(out.edges.map((e) => e.id)).toEqual(['LA']) // OUT is on another level
    expect(out.edges[0].parent).toBe('box')
  })

  it('a link is created on its ends\' level, and refused across levels', () => {
    const d = nested()
    const same = addEdge(d, 'A', 'B', 'related_to')
    expect(same.edges.at(-1)?.parent).toBe('P')
    expect(addEdge(d, 'P', 'A', 'related_to')).toBe(d) // P is top level, A is inside P
    expect(addEdge(d, 'A', 'A', 'related_to')).toBe(d)
    expect(addEdge(d, 'A', 'missing', 'related_to')).toBe(d)
  })
})

describe('explodeInto', () => {
  const ent = (id: string) => ({ entity_id: id, label: id, entity_subclass: 'LOCATION.Z' })
  const lk = (id: string, a: string, b: string): Link => ({
    link_id: id, link_type: 'adjacent_to', source_entity: a, target_entity: b, direction: 'directed',
    inverse_type: null, valid_from: null, valid_to: null, assertion_status: 'reported', confidence: 'B2', source_ref: '', attrs: {},
  })

  it('copies connected entities and the links among them inside the container', () => {
    const d: SandboxDoc = { nodes: [node('P', 'PERSON.X')], edges: [] }
    const out = explodeInto(d, 'P', [ent('P'), ent('N1'), ent('N2')], [lk('K1', 'N1', 'N2'), lk('K2', 'P', 'N1')])
    const inside = levelOf(out, 'P')
    expect(inside.nodes.map((n) => n.id)).toEqual(['N1', 'N2'])
    expect(inside.nodes.every((n) => n.origin === 'graph' && n.base?.label === n.label)).toBe(true)
    expect(inside.edges.map((e) => e.id)).toEqual(['K1']) // K2 touches P itself, which is not inside P
    expect(levelOf(out, null).nodes.map((n) => n.id)).toEqual(['P'])
  })

  it('skips entities already elsewhere in the sandbox and is a no-op when nothing is new', () => {
    const d: SandboxDoc = { nodes: [node('P', 'PERSON.X'), node('N1', 'LOCATION.Z')], edges: [] }
    const out = explodeInto(d, 'P', [ent('N1'), ent('N2')], [])
    expect(out.nodes.map((n) => n.id)).toEqual(['P', 'N1', 'N2'])
    expect(out.nodes.find((n) => n.id === 'N1')?.parent).toBeNull() // stayed where it was
    expect(explodeInto(d, 'P', [ent('N1')], [])).toBe(d)
  })
})

describe('addScenario', () => {
  const ex = (over: Partial<ScenarioExtract> = {}): ScenarioExtract => ({
    entities: [
      { entity_id: 'P-temp1', label: 'Ivan', entity_subclass: 'PERSON.X' },
      { entity_id: 'O-temp1', label: 'Acme', entity_subclass: 'ORGANIZATION.Y' },
    ],
    links: [{ link_type: 'commands', source_entity: 'P-temp1', target_entity: 'O-temp1' }],
    rejected_entities: [], rejected_links: [], existing_entities: [], ...over,
  })

  it('adds new entities and links as sandbox-only, with fresh ids', () => {
    const r = addScenario({ nodes: [], edges: [] }, ex())
    expect([r.entities, r.links, r.copied, r.skippedLinks]).toEqual([2, 1, 0, 0])
    expect(r.doc.nodes.every((n) => n.origin === 'new' && n.base === null && n.parent === null)).toBe(true)
    expect(r.doc.nodes.map((n) => n.id).some((id) => id.includes('temp'))).toBe(false)
    const [ivan, acme] = r.doc.nodes
    expect(r.doc.edges[0]).toMatchObject({ source: ivan.id, target: acme.id, link_type: 'commands', origin: 'new', base_type: null })
    expect(new Set(r.doc.nodes.map((n) => `${n.x},${n.y}`)).size).toBe(2) // not stacked
  })

  it('copies referenced real entities in (as graph origin) so links can reach them', () => {
    const r = addScenario({ nodes: [], edges: [] }, ex({
      entities: [{ entity_id: 'O-temp1', label: 'Acme', entity_subclass: 'ORGANIZATION.Y' }],
      existing_entities: [{ entity_id: 'P-1', label: 'Ivan', entity_subclass: 'PERSON.X' }],
      links: [{ link_type: 'commands', source_entity: 'P-1', target_entity: 'O-temp1' }],
    }))
    const real = r.doc.nodes.find((n) => n.id === 'P-1')!
    expect(real).toMatchObject({ origin: 'graph', base: { label: 'Ivan', entity_subclass: 'PERSON.X' } })
    expect([r.copied, r.links]).toEqual([1, 1])
    expect(r.doc.edges[0].source).toBe('P-1')
  })

  it('reuses a real entity already on this level, and skips links to one on another level', () => {
    const base: SandboxDoc = { nodes: [node('P-1', 'PERSON.X'), node('Q-1', 'PERSON.X', { parent: 'box' })], edges: [] }
    const real = (id: string) => ({ entity_id: id, label: id, entity_subclass: 'PERSON.X' })
    const r = addScenario(base, ex({
      existing_entities: [real('P-1'), real('Q-1')],
      links: [
        { link_type: 'commands', source_entity: 'P-1', target_entity: 'O-temp1' },
        { link_type: 'commands', source_entity: 'Q-1', target_entity: 'O-temp1' },
      ],
    }))
    expect(r.copied).toBe(0)
    expect(r.doc.nodes.filter((n) => n.id === 'P-1')).toHaveLength(1)
    expect([r.links, r.skippedLinks]).toEqual([1, 1])
  })

  it('skips dangling, self and duplicate links', () => {
    const r = addScenario({ nodes: [], edges: [] }, ex({
      links: [
        { link_type: 'commands', source_entity: 'P-temp1', target_entity: 'GONE' },
        { link_type: 'commands', source_entity: 'P-temp1', target_entity: 'P-temp1' },
        { link_type: 'commands', source_entity: 'P-temp1', target_entity: 'O-temp1' },
        { link_type: 'commands', source_entity: 'P-temp1', target_entity: 'O-temp1' },
      ],
    }))
    expect([r.links, r.skippedLinks]).toEqual([1, 3])
  })

  it('lands inside a container and below what is already on that level', () => {
    const base: SandboxDoc = { nodes: [node('box', 'PERSON.X', { y: 300 }), node('in', 'PERSON.X', { parent: 'box', y: 500 })], edges: [] }
    const r = addScenario(base, ex(), 'box')
    const added = r.doc.nodes.filter((n) => n.origin === 'new')
    expect(added.every((n) => n.parent === 'box' && n.y >= 720)).toBe(true)
    expect(r.doc.edges[0].parent).toBe('box')
  })

  it('leaves the input document untouched', () => {
    const base: SandboxDoc = { nodes: [], edges: [] }
    addScenario(base, ex())
    expect(base).toEqual({ nodes: [], edges: [] })
  })
})

// ---- following connections ---------------------------------------------------------

describe('expandAround', () => {
  const ent = (id: string, label = id) => ({ entity_id: id, label, entity_subclass: 'LOCATION.Z' })
  const lk = (id: string, a: string, b: string, type = 'adjacent_to'): Link => ({
    link_id: id, link_type: type, source_entity: a, target_entity: b, direction: 'directed',
    inverse_type: null, valid_from: null, valid_to: null, assertion_status: 'reported', confidence: 'B2', source_ref: '', attrs: {},
  })
  const start = (): SandboxDoc => ({ nodes: [node('C', 'ORGANIZATION.Y', { x: 100, y: 100 })], edges: [] })

  it('copies connected entities around the clicked one and draws the real links', () => {
    const r = expandAround(start(), 'C', [ent('C'), ent('A'), ent('B')], [lk('K1', 'C', 'A', 'owns'), lk('K2', 'B', 'C', 'owned_by')])
    expect(r.added.sort()).toEqual(['A', 'B'])
    expect([r.links, r.total, r.hidden, r.skippedLinks]).toEqual([2, 2, 0, 0])
    expect(r.doc.nodes.filter((n) => n.id !== 'C').every((n) => n.origin === 'graph' && n.base?.label === n.label && n.parent === null)).toBe(true)
    expect(r.doc.edges.map((e) => [e.id, e.source, e.target, e.link_type, e.origin, e.base_type]).sort()).toEqual([
      ['K1', 'C', 'A', 'owns', 'graph', 'owns'],
      ['K2', 'B', 'C', 'owned_by', 'graph', 'owned_by'],
    ])
    const pts = r.doc.nodes.map((n) => `${n.x},${n.y}`)
    expect(new Set(pts).size).toBe(3) // nothing stacked
  })

  it('never overlaps nodes, even for a large fan-out next to existing entities', () => {
    const many = Array.from({ length: 30 }, (_, i) => ent(`N${String(i).padStart(2, '0')}`))
    const d: SandboxDoc = { nodes: [node('C', 'ORGANIZATION.Y', { x: 0, y: 0 }), node('E', 'PERSON.X', { x: 300, y: 0 })], edges: [] }
    const r = expandAround(d, 'C', many, [], 30)
    const ns = r.doc.nodes
    for (let i = 0; i < ns.length; i++)
      for (let j = i + 1; j < ns.length; j++)
        expect(Math.abs(ns[i].x - ns[j].x) >= 240 || Math.abs(ns[i].y - ns[j].y) >= 110).toBe(true)
  })

  it('is repeatable: a second run adds only what is still missing', () => {
    const first = expandAround(start(), 'C', [ent('A')], [lk('K1', 'C', 'A')])
    const again = expandAround(first.doc, 'C', [ent('A'), ent('B')], [lk('K1', 'C', 'A'), lk('K2', 'C', 'B')])
    expect(again.added).toEqual(['B'])
    expect(again.links).toBe(1)
    expect(expandAround(again.doc, 'C', [ent('A'), ent('B')], [lk('K1', 'C', 'A'), lk('K2', 'C', 'B')])).toMatchObject({ added: [], links: 0 })
  })

  it('follows the trail: expanding a neighbour continues from it', () => {
    const one = expandAround(start(), 'C', [ent('A')], [lk('K1', 'C', 'A')])
    const two = expandAround(one.doc, 'A', [ent('C'), ent('D')], [lk('K1', 'C', 'A'), lk('K3', 'A', 'D')])
    expect(two.added).toEqual(['D'])
    expect(two.links).toBe(1) // K1 already drawn, only K3 is new
    const d = two.doc.nodes.find((n) => n.id === 'D')!
    const a = two.doc.nodes.find((n) => n.id === 'A')!
    expect(Math.hypot(d.x - a.x, d.y - a.y)).toBeGreaterThanOrEqual(255) // placed around A, not C
  })

  it('adds a batch at a time and reports the rest as hidden', () => {
    const many = Array.from({ length: 45 }, (_, i) => ent(`N${String(i).padStart(2, '0')}`))
    const links = many.map((e, i) => lk(`K${i}`, 'C', e.entity_id))
    const r1 = expandAround(start(), 'C', many, links, 30)
    expect([r1.added.length, r1.hidden, r1.total, r1.links]).toEqual([30, 15, 45, 30])
    const r2 = expandAround(r1.doc, 'C', many, links, 30)
    expect([r2.added.length, r2.hidden, r2.links]).toEqual([15, 0, 15])
    expect(r2.added.every((id) => !r1.added.includes(id))).toBe(true)
  })

  it('never grows the canvas past the sandbox size limit', () => {
    const filler = Array.from({ length: SANDBOX_MAX_NODES - 1 }, (_, i) => node(`F${i}`, 'PERSON.X'))
    const full: SandboxDoc = { nodes: [node('C', 'ORGANIZATION.Y'), ...filler], edges: [] }
    expect(full.nodes).toHaveLength(SANDBOX_MAX_NODES)
    const r = expandAround(full, 'C', [ent('A'), ent('B')], [lk('K1', 'C', 'A')])
    expect([r.added.length, r.hidden]).toEqual([0, 2])
    expect(r.doc.nodes).toHaveLength(SANDBOX_MAX_NODES)
  })

  it('stays on the clicked entity\'s level and skips links to entities on other levels', () => {
    const d: SandboxDoc = {
      nodes: [node('box', 'ORGANIZATION.Y'), node('C', 'ORGANIZATION.Y', { parent: 'box' }), node('ELSE', 'LOCATION.Z')],
      edges: [],
    }
    const r = expandAround(d, 'C', [ent('A'), ent('ELSE')], [lk('K1', 'C', 'A'), lk('K2', 'C', 'ELSE')])
    expect(r.doc.nodes.find((n) => n.id === 'A')?.parent).toBe('box')
    expect(r.doc.edges.map((e) => [e.id, e.parent])).toEqual([['K1', 'box']])
    expect(r.skippedLinks).toBe(1)
  })

  it('links the clicked entity to neighbours that are already on its level', () => {
    const d: SandboxDoc = { nodes: [node('C', 'ORGANIZATION.Y'), node('A', 'LOCATION.Z')], edges: [] }
    const r = expandAround(d, 'C', [ent('A')], [lk('K1', 'C', 'A')])
    expect([r.added.length, r.links]).toEqual([0, 1])
  })

  it('ignores a missing centre, self links, and duplicate links', () => {
    const d = start()
    expect(expandAround(d, 'GONE', [ent('A')], [])).toMatchObject({ doc: d, added: [] })
    const r = expandAround(d, 'C', [ent('A')], [lk('K1', 'C', 'C'), lk('K2', 'C', 'A'), lk('K3', 'C', 'A')])
    expect(r.links).toBe(1) // K3 is the same source/target/type as K2
  })

  it('leaves the input untouched', () => {
    const d = start()
    expandAround(d, 'C', [ent('A')], [lk('K1', 'C', 'A')])
    expect(d.nodes).toHaveLength(1)
    expect(d.edges).toHaveLength(0)
  })
})

describe('matching a sandbox-only entity to the graph', () => {
  it('tries the full name, then drops company suffixes, never below two words', () => {
    expect(searchTerms('Lehman Brothers Holdings Inc.')).toEqual(['Lehman Brothers Holdings Inc.', 'Lehman Brothers Holdings', 'Lehman Brothers'])
    expect(searchTerms('Acme, Inc.')).toEqual(['Acme Inc.']) // comma stripped; two words, so no shortening
    expect(searchTerms('Barclays')).toEqual(['Barclays'])
    expect(searchTerms('Richard Fuld')).toEqual(['Richard Fuld'])
    expect(searchTerms('  ')).toEqual([])
    expect(searchTerms('AB')).toEqual([]) // too short to search for
  })

  it('ranks the full name, then the name without company suffixes, then the closest in length', () => {
    const found = [{ label: 'Lehman Brothers International (Europe)' }, { label: 'lehman brothers' }, { label: 'Lehman Brothers Inc' }]
    expect(rankMatches('Lehman Brothers', found).map((e) => e.label)).toEqual([
      'lehman brothers', 'Lehman Brothers Inc', 'Lehman Brothers International (Europe)',
    ])
  })

  it('prefers "Lehman Brothers" over "Lehman Brothers International (Europe)" for a suffixed name', () => {
    const found = [{ label: 'Lehman Brothers International (Europe)' }, { label: 'Lehman Brothers' }]
    expect(rankMatches('Lehman Brothers Holdings Inc.', found).map((e) => e.label)).toEqual([
      'Lehman Brothers', 'Lehman Brothers International (Europe)',
    ])
  })

  const real = { entity_id: 'P-9', label: 'Lehman Brothers', entity_subclass: 'ORGANIZATION.Y' }
  const base = (): SandboxDoc => ({ nodes: [node('N-1', 'ORGANIZATION.Y', { origin: 'new', base: null, x: 10, y: 20 })], edges: [] })

  it('copies the real entity beside it and links them as the same entity', () => {
    const d = addGraphMatch(base(), 'N-1', real, 'same_as')
    const copy = d.nodes.find((n) => n.id === 'P-9')!
    expect(copy).toMatchObject({ origin: 'graph', x: 290, y: 20, parent: null, base: { label: 'Lehman Brothers' } })
    expect(d.edges).toHaveLength(1)
    expect(d.edges[0]).toMatchObject({ origin: 'new', source: 'N-1', target: 'P-9', link_type: 'same_as', base_type: null, parent: null })
  })

  it('omits the link when the ontology has no such type, and refuses duplicates or a missing node', () => {
    expect(addGraphMatch(base(), 'N-1', real, null).edges).toEqual([])
    const once = addGraphMatch(base(), 'N-1', real, 'same_as')
    expect(addGraphMatch(once, 'N-1', real, 'same_as')).toBe(once)
    const b = base()
    expect(addGraphMatch(b, 'GONE', real, 'same_as')).toBe(b)
  })
})

describe('freeSpotsAround', () => {
  it('returns the requested number of distinct spots, all clear of existing nodes', () => {
    const spots = freeSpotsAround({ x: 0, y: 0 }, [{ x: 0, y: 0 }, { x: 0, y: -300 }], 25)
    expect(spots).toHaveLength(25)
    expect(new Set(spots.map((s) => `${s.x},${s.y}`)).size).toBe(25)
    expect(spots.every((s) => Math.abs(s.x) >= 240 || Math.abs(s.y) >= 110)).toBe(true)
    expect(spots.every((s) => Math.abs(s.x) >= 240 || Math.abs(s.y + 300) >= 110)).toBe(true)
  })
  it('starts close to the centre and moves outward only when needed', () => {
    const few = freeSpotsAround({ x: 0, y: 0 }, [], 3)
    expect(few.every((s) => Math.hypot(s.x, s.y) < 310)).toBe(true)
    const many = freeSpotsAround({ x: 0, y: 0 }, [], 60)
    expect(Math.max(...many.map((s) => Math.hypot(s.x, s.y)))).toBeGreaterThan(500)
  })
})

describe('pickAutoMatch', () => {
  const f = (...labels: string[]) => labels.map((label) => ({ label }))
  it('adopts a single exact name match, ignoring case and company suffixes', () => {
    expect(pickAutoMatch('Lehman Brothers Holdings Inc.', f('Lehman Brothers', 'Lehman Brothers International (Europe)'))?.label).toBe('Lehman Brothers')
    expect(pickAutoMatch('barclays', f('Barclays', 'Barclays Capital'))?.label).toBe('Barclays')
  })
  it('does not guess when the match is fuzzy, ambiguous or absent', () => {
    expect(pickAutoMatch('Lehman', f('Lehman Brothers', 'Lehman Subsidiary'))).toBeNull() // only similar
    expect(pickAutoMatch('Acme', f('Acme', 'ACME'))).toBeNull() // two equally exact
    expect(pickAutoMatch('Acme', [])).toBeNull()
  })
})

describe('interleaveByRelation', () => {
  const e = (id: string, label = id) => ({ entity_id: id, label })
  const link = (id: string, type: string, other: string) => ({ link_type: type, source_entity: 'C', target_entity: other, link_id: id })

  it('puts one of each kind of relation first, rarest kinds first, then round-robins', () => {
    const subs = Array.from({ length: 6 }, (_, i) => e(`S${i}`, `Sub ${i}`))
    const ents = [...subs, e('HQ', 'New York'), e('CEO', 'Fuld'), e('REG', 'SEC')]
    const links = [...subs.map((s) => link(`k${s.entity_id}`, 'subsidiary_of', s.entity_id)),
      link('a', 'headquartered_in', 'HQ'), link('b', 'commands', 'CEO'), link('c', 'regulated_by', 'REG')]
    const order = interleaveByRelation(ents, 'C', links).map((x) => x.entity_id)
    expect(order.slice(0, 4).sort()).toEqual(['CEO', 'HQ', 'REG', 'S0']) // a sample of every relation in the first batch
    expect(order.slice(4)).toEqual(['S1', 'S2', 'S3', 'S4', 'S5']) // then the long tail, in name order
    expect(order).toHaveLength(9)
  })

  it('is deterministic and keeps every entity exactly once', () => {
    const ents = [e('B', 'b'), e('A', 'a'), e('Z', 'z')]
    const links = [link('1', 'x', 'B'), link('2', 'y', 'A'), link('3', 'x', 'Z')]
    const one = interleaveByRelation(ents, 'C', links).map((x) => x.entity_id)
    expect(interleaveByRelation([...ents].reverse(), 'C', links).map((x) => x.entity_id)).toEqual(one)
    expect([...one].sort()).toEqual(['A', 'B', 'Z'])
  })

  it('treats a link in either direction as the same relation, and unknown ones as one group', () => {
    const ents = [e('A', 'a'), e('B', 'b'), e('X', 'x')]
    const links = [{ link_type: 'owns', source_entity: 'A', target_entity: 'C', link_id: '1' }, link('2', 'owns', 'B')]
    expect(interleaveByRelation(ents, 'C', links).map((x) => x.entity_id)).toEqual(['X', 'A', 'B'])
  })
})

describe('a hub with many relations of one kind', () => {
  it('shows its rarer relations in the first batch', () => {
    const lk = (id: string, a: string, b: string, type: string): Link => ({
      link_id: id, link_type: type, source_entity: a, target_entity: b, direction: 'directed',
      inverse_type: null, valid_from: null, valid_to: null, assertion_status: 'reported', confidence: 'B2', source_ref: '', attrs: {},
    })
    const subs = Array.from({ length: 40 }, (_, i) => ({ entity_id: `S${i}`, label: `Lehman Subsidiary ${String(i).padStart(2, '0')}`, entity_subclass: 'ORGANIZATION.Y' }))
    const rare = [
      { entity_id: 'NYC', label: 'New York City', entity_subclass: 'LOCATION.Z' },
      { entity_id: 'FULD', label: 'Richard Fuld', entity_subclass: 'PERSON.X' },
      { entity_id: 'SEC', label: 'Securities and Exchange Commission', entity_subclass: 'ORGANIZATION.Y' },
    ]
    const links = [...subs.map((s) => lk(`k${s.entity_id}`, s.entity_id, 'C', 'subsidiary_of')), lk('a', 'C', 'NYC', 'headquartered_in'), lk('b', 'FULD', 'C', 'commands'), lk('c', 'C', 'SEC', 'regulated_by')]
    const r = expandAround({ nodes: [node('C', 'ORGANIZATION.Y')], edges: [] }, 'C', [...subs, ...rare], links, 30)
    expect(r.added).toHaveLength(30)
    expect(['NYC', 'FULD', 'SEC'].every((id) => r.added.includes(id))).toBe(true)
    expect(r.hidden).toBe(13)
  })
})
