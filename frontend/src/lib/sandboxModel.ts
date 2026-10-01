import type {
  ClassDef,
  Entity,
  Link,
  LinkDef,
  SandboxEdgeData,
  SandboxNodeData,
  ScenarioExtract,
} from './types'

/** Pure editing rules for the sandbox canvas (no React, no network). */

export const rootKey = (subclass: string) => subclass.split('.')[0]

export type ChangeStatus = 'original' | 'edited' | 'new'

export function nodeStatus(n: SandboxNodeData): ChangeStatus {
  if (n.origin === 'new') return 'new'
  if (!n.base) return 'original'
  return n.base.label !== n.label || n.base.entity_subclass !== n.entity_subclass ? 'edited' : 'original'
}

export function edgeStatus(e: SandboxEdgeData): ChangeStatus {
  if (e.origin === 'new') return 'new'
  return e.base_type !== null && e.base_type !== e.link_type ? 'edited' : 'original'
}

/** Root class label ("Person") for a class key, from the ontology list. */
export function rootLabelOf(subclass: string, classes: ClassDef[]): string {
  const root = classes.find((c) => c.key === rootKey(subclass))
  return (root?.label ?? '').toLowerCase()
}

const allows = (list: string, rootLabel: string) =>
  list.trim().toLowerCase() === 'any' ||
  list
    .split(';')
    .map((p) => p.trim().toLowerCase())
    .includes(rootLabel)

/** Link types whose domain/range accept this source and target — the same
 * rule the server enforces on save, so the picker never offers a type that
 * would be rejected. */
export function validLinkTypes(
  linkDefs: LinkDef[],
  sourceSubclass: string,
  targetSubclass: string,
  classes: ClassDef[],
): LinkDef[] {
  const s = rootLabelOf(sourceSubclass, classes)
  const t = rootLabelOf(targetSubclass, classes)
  return linkDefs
    .filter((l) => allows(l.domain, s) && allows(l.range, t))
    .sort((a, b) => a.type.localeCompare(b.type))
}

export function newId(prefix: 'N' | 'E'): string {
  return `${prefix}-${crypto.randomUUID().slice(0, 8)}`
}

export interface SandboxDoc {
  nodes: SandboxNodeData[]
  edges: SandboxEdgeData[]
}

/** Items (entities and links) whose parent is `container` — one level of the
 * hierarchy. null = the top level. */
export function levelOf(doc: SandboxDoc, container: string | null): SandboxDoc {
  return {
    nodes: doc.nodes.filter((n) => (n.parent ?? null) === container),
    edges: doc.edges.filter((e) => (e.parent ?? null) === container),
  }
}

/** How many entities/links sit directly inside this one. */
export function childCount(doc: SandboxDoc, id: string): number {
  return (
    doc.nodes.filter((n) => n.parent === id).length + doc.edges.filter((e) => e.parent === id).length
  )
}

export function containerExists(doc: SandboxDoc, id: string | null): boolean {
  return id === null || doc.nodes.some((n) => n.id === id) || doc.edges.some((e) => e.id === id)
}

/** Ancestors from the top level down to (and including) `id`, with a readable name each. */
export function pathTo(doc: SandboxDoc, id: string | null): { id: string; name: string }[] {
  const out: { id: string; name: string }[] = []
  const label = (x: string) => doc.nodes.find((n) => n.id === x)?.label ?? x
  let cur = id
  for (let guard = 0; cur !== null && guard < 20; guard++) {
    const node = doc.nodes.find((n) => n.id === cur)
    const edge = node ? undefined : doc.edges.find((e) => e.id === cur)
    if (node) out.unshift({ id: node.id, name: node.label })
    else if (edge) out.unshift({ id: edge.id, name: `${label(edge.source)} ${edge.link_type} ${label(edge.target)}` })
    else break
    cur = (node ?? edge)!.parent ?? null
  }
  return out
}

/** Everything that must go when `ids` are removed: the items themselves,
 * links attached to a removed entity, and (recursively) whatever is inside. */
function withDependents(doc: SandboxDoc, ids: string[]): Set<string> {
  const gone = new Set(ids)
  let grew = true
  while (grew) {
    grew = false
    const add = (id: string) => {
      if (!gone.has(id)) {
        gone.add(id)
        grew = true
      }
    }
    for (const e of doc.edges) if (gone.has(e.source) || gone.has(e.target)) add(e.id)
    for (const n of doc.nodes) if (n.parent && gone.has(n.parent)) add(n.id)
    for (const e of doc.edges) if (e.parent && gone.has(e.parent)) add(e.id)
  }
  return gone
}

export function copyEntityIntoSandbox(
  doc: SandboxDoc,
  entity: Pick<Entity, 'entity_id' | 'label' | 'entity_subclass'>,
  at: { x: number; y: number },
  realLinks: Link[] = [],
  parent: string | null = null,
): SandboxDoc {
  if (doc.nodes.some((n) => n.id === entity.entity_id)) return doc
  const nodes = [
    ...doc.nodes,
    {
      id: entity.entity_id,
      origin: 'graph' as const,
      label: entity.label,
      entity_subclass: entity.entity_subclass,
      x: at.x,
      y: at.y,
      base: { label: entity.label, entity_subclass: entity.entity_subclass },
      parent,
    },
  ]
  // Bring along real links between this entity and what's already on THIS level.
  const present = new Set(nodes.filter((n) => (n.parent ?? null) === parent).map((n) => n.id))
  const have = new Set([...doc.edges.map((e) => e.id), ...nodes.map((n) => n.id)])
  const pulled = realLinks
    .filter(
      (l) =>
        !have.has(l.link_id) &&
        present.has(l.source_entity) &&
        present.has(l.target_entity) &&
        (l.source_entity === entity.entity_id || l.target_entity === entity.entity_id),
    )
    .map((l) => ({
      id: l.link_id,
      origin: 'graph' as const,
      source: l.source_entity,
      target: l.target_entity,
      link_type: l.link_type,
      base_type: l.link_type,
      parent,
    }))
  return { nodes, edges: [...doc.edges, ...pulled] }
}

/** "Explode" an entity: copy its real, directly-connected entities (and the
 * real links among them) INSIDE it. Entities already somewhere in the sandbox
 * are skipped, since an entity can only appear once. Read-only on the real graph. */
export function explodeInto(
  doc: SandboxDoc,
  container: string,
  neighbours: Pick<Entity, 'entity_id' | 'label' | 'entity_subclass'>[],
  realLinks: Link[],
): SandboxDoc {
  const taken = new Set([...doc.nodes.map((n) => n.id), ...doc.edges.map((e) => e.id)])
  const fresh = neighbours.filter((e) => e.entity_id !== container && !taken.has(e.entity_id))
  if (fresh.length === 0) return doc
  const added: SandboxNodeData[] = fresh.map((e, i) => {
    const angle = (2 * Math.PI * i) / fresh.length
    return {
      id: e.entity_id,
      origin: 'graph',
      label: e.label,
      entity_subclass: e.entity_subclass,
      x: Math.round(240 * Math.cos(angle)),
      y: Math.round(240 * Math.sin(angle)),
      base: { label: e.label, entity_subclass: e.entity_subclass },
      parent: container,
    }
  })
  const inside = new Set(added.map((n) => n.id))
  const links: SandboxEdgeData[] = realLinks
    .filter((l) => !taken.has(l.link_id) && inside.has(l.source_entity) && inside.has(l.target_entity))
    .map((l) => ({
      id: l.link_id,
      origin: 'graph',
      source: l.source_entity,
      target: l.target_entity,
      link_type: l.link_type,
      base_type: l.link_type,
      parent: container,
    }))
  return { nodes: [...doc.nodes, ...added], edges: [...doc.edges, ...links] }
}

export function addNewEntity(
  doc: SandboxDoc,
  label: string,
  subclass: string,
  at: { x: number; y: number },
  parent: string | null = null,
): { doc: SandboxDoc; id: string } {
  const id = newId('N')
  return {
    id,
    doc: {
      ...doc,
      nodes: [
        ...doc.nodes,
        { id, origin: 'new', label, entity_subclass: subclass, x: at.x, y: at.y, base: null, parent },
      ],
    },
  }
}

export function updateNode(doc: SandboxDoc, id: string, patch: Partial<SandboxNodeData>): SandboxDoc {
  return { ...doc, nodes: doc.nodes.map((n) => (n.id === id ? { ...n, ...patch } : n)) }
}

export function updateEdge(doc: SandboxDoc, id: string, patch: Partial<SandboxEdgeData>): SandboxDoc {
  return { ...doc, edges: doc.edges.map((e) => (e.id === id ? { ...e, ...patch } : e)) }
}

/** Removing an entity also removes the links attached to it and everything inside it. */
export function removeNode(doc: SandboxDoc, id: string): SandboxDoc {
  const gone = withDependents(doc, [id])
  return {
    nodes: doc.nodes.filter((n) => !gone.has(n.id)),
    edges: doc.edges.filter((e) => !gone.has(e.id)),
  }
}

/** Removing a link also removes everything inside it. */
export function removeEdge(doc: SandboxDoc, id: string): SandboxDoc {
  const gone = withDependents(doc, [id])
  return {
    nodes: doc.nodes.filter((n) => !gone.has(n.id)),
    edges: doc.edges.filter((e) => !gone.has(e.id)),
  }
}

export function revertNode(doc: SandboxDoc, id: string): SandboxDoc {
  const n = doc.nodes.find((x) => x.id === id)
  return n?.base ? updateNode(doc, id, { label: n.base.label, entity_subclass: n.base.entity_subclass }) : doc
}

export function addEdge(doc: SandboxDoc, source: string, target: string, linkType: string): SandboxDoc {
  const s = doc.nodes.find((n) => n.id === source)
  const t = doc.nodes.find((n) => n.id === target)
  // A link lives on the same level as both of its ends.
  if (!s || !t || source === target || (s.parent ?? null) !== (t.parent ?? null)) return doc
  return {
    ...doc,
    edges: [
      ...doc.edges,
      { id: newId('E'), origin: 'new', source, target, link_type: linkType, base_type: null, parent: s.parent ?? null },
    ],
  }
}

export function summarize(doc: SandboxDoc) {
  const count = (items: ChangeStatus[], s: ChangeStatus) => items.filter((x) => x === s).length
  const ns = doc.nodes.map(nodeStatus)
  const es = doc.edges.map(edgeStatus)
  return {
    newEntities: count(ns, 'new'),
    editedEntities: count(ns, 'edited'),
    newLinks: count(es, 'new'),
    editedLinks: count(es, 'edited'),
  }
}

/** Links that would stop being valid if `nodeId` were reclassified. The UI
 * refuses the change while any exist (the server would reject the save). */
export function brokenLinksIfReclassified(
  doc: SandboxDoc,
  nodeId: string,
  newSubclass: string,
  linkDefs: LinkDef[],
  classes: ClassDef[],
): SandboxEdgeData[] {
  const sub = (id: string) =>
    id === nodeId ? newSubclass : (doc.nodes.find((n) => n.id === id)?.entity_subclass ?? '')
  return doc.edges.filter(
    (e) =>
      (e.source === nodeId || e.target === nodeId) &&
      !validLinkTypes(linkDefs, sub(e.source), sub(e.target), classes).some((l) => l.type === e.link_type),
  )
}

export interface ScenarioAddition {
  doc: SandboxDoc
  entities: number
  /** Real graph entities copied in because the text referred to them. */
  copied: number
  links: number
  /** Links not added: an end is missing, on another level, or the link already exists. */
  skippedLinks: number
}

/** Place an extracted scenario into the sandbox at one level: new entities
 * and links are sandbox-only ("new"); real entities the text mentioned are
 * copied in. Pure - the real graph is never involved. */
export function addScenario(
  doc: SandboxDoc,
  extract: ScenarioExtract,
  container: string | null = null,
): ScenarioAddition {
  const level = doc.nodes.filter((n) => (n.parent ?? null) === container)
  const top = level.length ? Math.max(...level.map((n) => n.y)) + 220 : 0
  let slot = 0
  const nextPos = () => {
    const pos = { x: (slot % 4) * 260, y: top + Math.floor(slot / 4) * 170 }
    slot += 1
    return pos
  }

  const ids = new Map<string, string>() // extracted/real id -> id on this canvas
  const nodes = [...doc.nodes]
  let copied = 0

  for (const e of extract.existing_entities) {
    const there = doc.nodes.find((n) => n.id === e.entity_id)
    if (there) {
      if ((there.parent ?? null) === container) ids.set(e.entity_id, there.id)
      continue // on another level: links to it are skipped
    }
    nodes.push({
      id: e.entity_id,
      origin: 'graph',
      label: e.label,
      entity_subclass: e.entity_subclass,
      ...nextPos(),
      base: { label: e.label, entity_subclass: e.entity_subclass },
      parent: container,
    })
    ids.set(e.entity_id, e.entity_id)
    copied += 1
  }
  for (const e of extract.entities) {
    const id = newId('N')
    ids.set(e.entity_id, id)
    nodes.push({
      id,
      origin: 'new',
      label: e.label,
      entity_subclass: e.entity_subclass,
      ...nextPos(),
      base: null,
      parent: container,
    })
  }

  const edges = [...doc.edges]
  let links = 0
  let skippedLinks = 0
  for (const l of extract.links) {
    const s = ids.get(l.source_entity)
    const t = ids.get(l.target_entity)
    const duplicate = edges.some(
      (x) => x.source === s && x.target === t && x.link_type === l.link_type && (x.parent ?? null) === container,
    )
    if (!s || !t || s === t || duplicate) {
      skippedLinks += 1
      continue
    }
    edges.push({
      id: newId('E'),
      origin: 'new',
      source: s,
      target: t,
      link_type: l.link_type,
      base_type: null,
      parent: container,
    })
    links += 1
  }
  return { doc: { nodes, edges }, entities: extract.entities.length, copied, links, skippedLinks }
}

// ---- follow connections from the knowledge graph ------------------------------------

// Mirror the server's per-sandbox limits so an expansion can never make a canvas the
// server would refuse to save.
export const SANDBOX_MAX_NODES = 300
export const SANDBOX_MAX_EDGES = 1000
/** Connected entities added per click; "Show more" adds the next batch. */
export const EXPAND_BATCH = 30

type GraphEntity = Pick<Entity, 'entity_id' | 'label' | 'entity_subclass'>

export interface Expansion {
  doc: SandboxDoc
  /** ids of the entities this expansion put on the canvas */
  added: string[]
  /** links added (all touch the expanded entity) */
  links: number
  /** connected entities in the graph, excluding the expanded one */
  total: number
  /** connected entities not shown yet (batch / canvas size limit) */
  hidden: number
  /** links not drawn because the other end sits on a different level */
  skippedLinks: number
}

const NODE_W = 240
const NODE_H = 110

/** `count` positions on concentric rings around `center`, skipping any spot that would
 * overlap an entity already on the level (or one placed earlier in this call). Rings
 * are sized to the node width, so a big fan-out spreads outward instead of stacking. */
export function freeSpotsAround(
  center: { x: number; y: number },
  existing: { x: number; y: number }[],
  count: number,
): { x: number; y: number }[] {
  const taken = existing.map((n) => ({ x: n.x, y: n.y }))
  const hits = (x: number, y: number) =>
    taken.some((t) => Math.abs(t.x - x) < NODE_W && Math.abs(t.y - y) < NODE_H)
  const out: { x: number; y: number }[] = []
  for (let ring = 0; out.length < count && ring < 40; ring++) {
    const radius = 300 + ring * 230
    const slots = Math.max(6, Math.floor((2 * Math.PI * radius) / (NODE_W + 20)))
    const offset = ring % 2 === 0 ? 0 : Math.PI / slots // stagger alternate rings
    for (let k = 0; k < slots && out.length < count; k++) {
      const angle = -Math.PI / 2 + offset + (2 * Math.PI * k) / slots
      const x = Math.round(center.x + radius * Math.cos(angle))
      const y = Math.round(center.y + radius * Math.sin(angle))
      if (hits(x, y)) continue
      taken.push({ x, y })
      out.push({ x, y })
    }
  }
  // Pathological fallback (a huge crowded canvas): stack remaining ones far below.
  while (out.length < count) out.push({ x: center.x, y: center.y + 4000 + out.length * NODE_H })
  return out
}

/** Order connected entities so any batch is a fair sample: one of each kind of relation
 * first (rarest kinds first), then round-robin. Without this, an entity with 40
 * "subsidiary_of" links would bury its single headquarters, CEO and regulator behind
 * the subsidiaries. Within a kind, alphabetical. Deterministic. */
export function interleaveByRelation<T extends { entity_id: string; label: string }>(
  entities: T[],
  centerId: string,
  realLinks: Pick<Link, 'link_type' | 'source_entity' | 'target_entity'>[],
): T[] {
  const kindOf = new Map<string, string>()
  for (const l of realLinks) {
    const other = l.source_entity === centerId ? l.target_entity : l.target_entity === centerId ? l.source_entity : null
    if (other && !kindOf.has(other)) kindOf.set(other, l.link_type)
  }
  const groups = new Map<string, T[]>()
  for (const e of entities) {
    const k = kindOf.get(e.entity_id) ?? ''
    groups.set(k, [...(groups.get(k) ?? []), e])
  }
  const ordered = [...groups.entries()]
    .map(([kind, list]) => ({ kind, list: [...list].sort((a, b) => a.label.localeCompare(b.label)) }))
    .sort((a, b) => a.list.length - b.list.length || a.kind.localeCompare(b.kind))
  const out: T[] = []
  for (let round = 0; ordered.some((g) => round < g.list.length); round++) {
    for (const g of ordered) if (round < g.list.length) out.push(g.list[round])
  }
  return out
}

/** Show an entity's real connections on its own level: each connected entity not
 * already in the sandbox is copied in around it (up to `limit`), and the real links
 * between it and anything on this level are drawn. Pure; the real graph is only read
 * by the caller to produce `neighbours` / `realLinks`. Running it again only adds
 * what is still missing, which is what makes "click to keep going" safe. */
export function expandAround(
  doc: SandboxDoc,
  centerId: string,
  neighbours: GraphEntity[],
  realLinks: Link[],
  limit: number = EXPAND_BATCH,
): Expansion {
  const none: Expansion = { doc, added: [], links: 0, total: 0, hidden: 0, skippedLinks: 0 }
  const center = doc.nodes.find((n) => n.id === centerId)
  if (!center) return none
  const level = center.parent ?? null

  const byId = new Map(doc.nodes.map((n) => [n.id, n]))
  const others = neighbours.filter((e) => e.entity_id !== centerId)
  const fresh = interleaveByRelation(
    others.filter((e) => !byId.has(e.entity_id)),
    centerId,
    realLinks,
  )
  const room = Math.max(0, Math.min(limit, SANDBOX_MAX_NODES - doc.nodes.length))
  const toAdd = fresh.slice(0, room)

  const spots = freeSpotsAround(center, level === null ? doc.nodes.filter((n) => n.parent == null) : doc.nodes.filter((n) => n.parent === level), toAdd.length)
  const added: SandboxNodeData[] = toAdd.map((e, i) => ({
    id: e.entity_id,
    origin: 'graph',
    label: e.label,
    entity_subclass: e.entity_subclass,
    x: spots[i].x,
    y: spots[i].y,
    base: { label: e.label, entity_subclass: e.entity_subclass },
    parent: level,
  }))

  const onLevel = new Set([
    ...doc.nodes.filter((n) => (n.parent ?? null) === level).map((n) => n.id),
    ...added.map((n) => n.id),
  ])
  const taken = new Set([...doc.nodes.map((n) => n.id), ...doc.edges.map((e) => e.id), ...added.map((n) => n.id)])
  const edges = [...doc.edges]
  let links = 0
  let skippedLinks = 0
  for (const l of realLinks) {
    const other =
      l.source_entity === centerId ? l.target_entity : l.target_entity === centerId ? l.source_entity : null
    if (other === null || other === centerId) continue
    if (!onLevel.has(other)) {
      if (byId.has(other)) skippedLinks += 1 // exists, but on another level
      continue // otherwise it is a not-yet-shown neighbour: drawn once that one is added
    }
    const duplicate = edges.some(
      (x) =>
        x.source === l.source_entity &&
        x.target === l.target_entity &&
        x.link_type === l.link_type &&
        (x.parent ?? null) === level,
    )
    if (taken.has(l.link_id) || duplicate || edges.length >= SANDBOX_MAX_EDGES) continue
    taken.add(l.link_id)
    edges.push({
      id: l.link_id,
      origin: 'graph',
      source: l.source_entity,
      target: l.target_entity,
      link_type: l.link_type,
      base_type: l.link_type,
      parent: level,
    })
    links += 1
  }

  return {
    doc: { nodes: [...doc.nodes, ...added], edges },
    added: added.map((n) => n.id),
    links,
    total: others.length,
    hidden: fresh.length - toAdd.length,
    skippedLinks,
  }
}

const NAME_SUFFIXES = new Set([
  'inc', 'inc.', 'ltd', 'ltd.', 'llc', 'plc', 'corp', 'corp.', 'corporation', 'co', 'co.',
  'company', 'holdings', 'holding', 'group', 'ag', 'sa', 'gmbh', 'limited',
])

/** Names to try when looking for a sandbox-only entity in the graph, most specific
 * first: "Lehman Brothers Holdings Inc." -> that, "Lehman Brothers Holdings",
 * "Lehman Brothers". Never shorter than two words, so it cannot degrade to "Lehman". */
export function searchTerms(label: string): string[] {
  const words = label.trim().split(/\s+/).map((w) => w.replace(/,+$/, '')).filter(Boolean)
  const terms: string[] = []
  const push = (w: string[]) => {
    const t = w.join(' ')
    if (t.length >= 3 && !terms.some((x) => x.toLowerCase() === t.toLowerCase())) terms.push(t)
  }
  push(words)
  let w = words
  while (w.length > 2 && NAME_SUFFIXES.has(w[w.length - 1].toLowerCase())) {
    w = w.slice(0, -1)
    push(w)
  }
  return terms
}

/** Best candidates first: the full name, then the name without company suffixes
 * ("Lehman Brothers Holdings Inc." -> "Lehman Brothers"), then the closest in length. */
export function rankMatches<T extends { label: string }>(label: string, found: T[]): T[] {
  const terms = searchTerms(label).map((t) => t.toLowerCase())
  const score = (e: T) => {
    const name = e.label.trim().toLowerCase()
    const at = terms.indexOf(name)
    return at >= 0 ? -100 + at : Math.abs(e.label.length - label.length)
  }
  return [...found].sort((a, b) => score(a) - score(b) || a.label.localeCompare(b.label))
}

/** The graph entity a click may adopt WITHOUT asking: exactly one candidate whose name
 * equals the sandbox entity's name (ignoring case, and company suffixes such as
 * "Inc."). Anything fuzzier, or two equally good matches, is left for the user to pick. */
export function pickAutoMatch<T extends { label: string }>(label: string, found: T[]): T | null {
  const terms = new Set(searchTerms(label).map((t) => t.toLowerCase()))
  const exact = found.filter((e) => terms.has(e.label.trim().toLowerCase()))
  return exact.length === 1 ? exact[0] : null
}

/** Accept a graph entity as "the same thing" as a sandbox-only entity: the real entity
 * is copied in beside it (so its connections can be followed) and, when the ontology
 * has `linkType`, joined to it. Identity stays a hypothesis the user can delete. */
export function addGraphMatch(
  doc: SandboxDoc,
  newNodeId: string,
  entity: GraphEntity,
  linkType: string | null,
): SandboxDoc {
  const from = doc.nodes.find((n) => n.id === newNodeId)
  if (!from || doc.nodes.some((n) => n.id === entity.entity_id)) return doc
  const level = from.parent ?? null
  const nodes = [
    ...doc.nodes,
    {
      id: entity.entity_id,
      origin: 'graph' as const,
      label: entity.label,
      entity_subclass: entity.entity_subclass,
      x: from.x + 280,
      y: from.y,
      base: { label: entity.label, entity_subclass: entity.entity_subclass },
      parent: level,
    },
  ]
  const edges = linkType
    ? [
        ...doc.edges,
        {
          id: newId('E'),
          origin: 'new' as const,
          source: newNodeId,
          target: entity.entity_id,
          link_type: linkType,
          base_type: null,
          parent: level,
        },
      ]
    : doc.edges
  return { nodes, edges }
}
