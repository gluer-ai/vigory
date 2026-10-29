import type { DuplicateItem, DuplicateMatch, DuplicateReason } from './types'

const REASON_TEXT: Record<DuplicateReason, string> = {
  same_id: 'Same ID — committing would overwrite it',
  same_label: 'Same name',
  alias: 'Shares a name or alias',
  reordered: 'Same words, different order',
  similar: 'Very similar spelling',
}

export function describeReason(reason: DuplicateReason): string {
  return REASON_TEXT[reason]
}

export function describeWhere(where: DuplicateMatch['where']): string {
  return where === 'graph' ? 'already in the graph' : 'earlier in this batch'
}

/** The match a reviewer's "remove" should merge into: their pick, else the strongest. */
export function chosenTarget(item: DuplicateItem, picked: string | undefined): DuplicateMatch {
  return item.matches.find((m) => m.entity_id === picked) ?? item.matches[0]
}

/** Changes whenever the batch's entity set changes, so the duplicate list is re-fetched. */
export function entitySignature(entities: { entity_id: string }[]): string {
  return entities.map((e) => e.entity_id).join('|')
}
