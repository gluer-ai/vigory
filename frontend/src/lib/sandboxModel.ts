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
