import type { RemovalPreview } from './types'

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`

/** Plain-language lines describing what "remove source and data" will delete. */
export function describeRemoval(p: RemovalPreview): string[] {
  const lines: string[] = []
  if (p.entities.remove) lines.push(plural(p.entities.remove, 'entity', 'entities'))
  if (p.links.remove) lines.push(plural(p.links.remove, 'link', 'links'))
  if (p.classes.remove.length) lines.push(plural(p.classes.remove.length, 'ontology class', 'ontology classes'))
  if (p.link_types.remove.length) lines.push(plural(p.link_types.remove.length, 'link type', 'link types'))
  return lines
}

/** True when removing the data would change nothing (nothing was imported / traceable). */
export function nothingToRemove(p: RemovalPreview): boolean {
  return describeRemoval(p).length === 0
}

/** What stays behind, and why - so a reviewer is not surprised. */
export function describeKept(p: RemovalPreview): string[] {
  const kept: string[] = []
  if (p.entities.kept_shared) {
    kept.push(
      `${plural(p.entities.kept_shared, 'entity', 'entities')} kept: other data still links to ${p.entities.kept_shared === 1 ? 'it' : 'them'}`,
    )
  }
  for (const c of p.classes.kept) kept.push(`Class ${c.key} kept: ${c.reason}`)
  for (const t of p.link_types.kept) kept.push(`Link type ${t.type} kept: ${t.reason}`)
  return kept
}
