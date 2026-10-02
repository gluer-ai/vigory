import type { SandboxProposal } from './types'

/** Pure helpers for the round ("sphere") view of a proposal: what to draw, how big,
 * and where. No React, no network. */

export interface SphereNode {
  id: string
  label: string
  entity_subclass: string
  /** 'new' entities will be created on commit; 'existing' ones are real records the new links attach to. */
  kind: 'new' | 'existing'
}

export interface SphereEdge {
  id: string
  source: string
  target: string
  link_type: string
}

export interface SphereData {
  nodes: SphereNode[]
  edges: SphereEdge[]
}

/** The proposal's entities and links as a graph. Real entities that only appear as the
 * end of a link are included (from `existing_entities`) so no line is left dangling. */
export function proposalToGraph(proposal: SandboxProposal): SphereData {
  const { batch } = proposal
  const nodes: SphereNode[] = batch.entities.map((e) => ({
    id: e.entity_id, label: e.label, entity_subclass: e.entity_subclass, kind: 'new',
  }))
  const known = new Set(nodes.map((n) => n.id))
  for (const e of proposal.existing_entities ?? []) {
    if (known.has(e.entity_id)) continue
    known.add(e.entity_id)
    nodes.push({ id: e.entity_id, label: e.label, entity_subclass: e.entity_subclass, kind: 'existing' })
  }
  const edges: SphereEdge[] = batch.links
    .filter((l) => known.has(l.source_entity) && known.has(l.target_entity) && l.source_entity !== l.target_entity)
    .map((l) => ({ id: l.link_id, source: l.source_entity, target: l.target_entity, link_type: l.link_type }))
  return { nodes, edges }
}

export const MIN_DIAMETER = 72
export const MAX_DIAMETER = 128
const GAP = 44
const REPULSION = 0.55
const GRAVITY = 0.16 // clear space kept between two spheres (room for labels)

/** Bigger sphere = more connections, so hubs stand out. */
export function diameterFor(degree: number): number {
  return Math.min(MAX_DIAMETER, MIN_DIAMETER + degree * 8)
}

export function degrees(data: SphereData): Map<string, number> {
  const d = new Map(data.nodes.map((n) => [n.id, 0]))
  for (const e of data.edges) {
    d.set(e.source, (d.get(e.source) ?? 0) + 1)
    d.set(e.target, (d.get(e.target) ?? 0) + 1)
  }
  return d
}

export interface Placed {
  id: string
  /** centre of the sphere */
  x: number
  y: number
  diameter: number
}

/** Force-directed layout: every sphere pushes the others away, links pull their ends
 * together, and a light pull to the middle keeps separate groups from drifting apart.
 * Deterministic (the same graph always lays out the same way), and finishes with a pass
 * that guarantees no two spheres overlap. */
export function layoutSpheres(data: SphereData, iterations = 320): Placed[] {
  const n = data.nodes.length
  if (n === 0) return []
  const deg = degrees(data)
  const nodes = [...data.nodes].sort((a, b) => a.id.localeCompare(b.id))
  const diam = nodes.map((nd) => diameterFor(deg.get(nd.id) ?? 0))
  const index = new Map(nodes.map((nd, i) => [nd.id, i]))
  if (n === 1) return [{ id: nodes[0].id, x: 0, y: 0, diameter: diam[0] }]

  const pos = nodes.map((_, i) => {
    const angle = (2 * Math.PI * i) / n
    const r = 100 + 22 * n
    return { x: r * Math.cos(angle), y: r * Math.sin(angle) }
  })
  const links = data.edges
    .map((e) => [index.get(e.source), index.get(e.target)] as const)
    .filter((p): p is [number, number] => p[0] !== undefined && p[1] !== undefined && p[0] !== p[1])

  const rest = 115 + 6 * Math.sqrt(n) // preferred length of a link, centre to centre
  for (let step = 0; step < iterations; step++) {
    const cooling = 1 - step / iterations
    const force = pos.map(() => ({ x: 0, y: 0 }))
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        let dx = pos[i].x - pos[j].x
        let dy = pos[i].y - pos[j].y
        let d = Math.hypot(dx, dy)
        if (d < 0.01) { dx = Math.cos(i + j); dy = Math.sin(i + j); d = 1 }
        if (d > rest * 3.2) continue // far-apart spheres do not push each other: groups stay in one picture
        const push = (rest * rest * REPULSION) / d
        force[i].x += (dx / d) * push; force[i].y += (dy / d) * push
        force[j].x -= (dx / d) * push; force[j].y -= (dy / d) * push
      }
    }
    for (const [a, b] of links) {
      const dx = pos[b].x - pos[a].x
      const dy = pos[b].y - pos[a].y
      const d = Math.max(0.01, Math.hypot(dx, dy))
      const pull = ((d - rest) * 0.35)
      force[a].x += (dx / d) * pull; force[a].y += (dy / d) * pull
      force[b].x -= (dx / d) * pull; force[b].y -= (dy / d) * pull
    }
    for (let i = 0; i < n; i++) {
      // Gravity: grows with distance from the middle, so unconnected groups stay near each other.
      force[i].x -= pos[i].x * GRAVITY
      force[i].y -= pos[i].y * GRAVITY
      const mag = Math.hypot(force[i].x, force[i].y)
      const cap = 40 * cooling + 1
      const k = mag > cap ? cap / mag : 1
      pos[i].x += force[i].x * k
      pos[i].y += force[i].y * k
    }
  }

  // Guarantee: no overlaps, whatever the forces did.
  for (let pass = 0; pass < 60; pass++) {
    let moved = false
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const need = (diam[i] + diam[j]) / 2 + GAP
        let dx = pos[j].x - pos[i].x
        let dy = pos[j].y - pos[i].y
        let d = Math.hypot(dx, dy)
        if (d >= need) continue
        if (d < 0.01) { dx = 1; dy = 0; d = 1 }
        const shift = (need - d) / 2 + 0.5
        pos[i].x -= (dx / d) * shift; pos[i].y -= (dy / d) * shift
        pos[j].x += (dx / d) * shift; pos[j].y += (dy / d) * shift
        moved = true
      }
    }
    if (!moved) break
  }

  return nodes.map((nd, i) => ({ id: nd.id, x: Math.round(pos[i].x), y: Math.round(pos[i].y), diameter: diam[i] }))
}

/** Which nodes and links to emphasise when `id` is selected (null = nothing selected). */
export function neighbourhood(data: SphereData, id: string | null): { nodes: Set<string>; edges: Set<string> } | null {
  if (id === null) return null
  const edges = new Set<string>()
  const nodes = new Set<string>([id])
  for (const e of data.edges) {
    if (e.source === id || e.target === id) {
      edges.add(e.id)
      nodes.add(e.source)
      nodes.add(e.target)
    }
  }
  return { nodes, edges }
}
