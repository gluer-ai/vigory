export type EntityStatus = 'active' | 'inactive' | 'destroyed' | 'unknown'

export interface Entity {
  entity_id: string
  entity_class: string
  entity_subclass: string
  label: string
  aliases: string[]
  status: EntityStatus
  confidence: string
  source_ref: string
  first_observed: string | null
  last_observed: string | null
  attrs: Record<string, unknown>
}

export interface Link {
  link_id: string
  link_type: string
  source_entity: string
  target_entity: string
  direction: 'directed' | 'symmetric'
  inverse_type: string | null
  valid_from: string | null
  valid_to: string | null
  assertion_status: 'reported' | 'assessed' | 'confirmed' | 'disputed'
  confidence: string
  source_ref: string
  attrs: Record<string, unknown>
}

export interface ClassDef {
  key: string
  parent_key: string | null
  level: number
  label: string
  notes: string | null
}

export interface LinkDef {
  type: string
  category: string | null
  domain: string
  range: string
  directionality: string | null
  inverse: string | null
  symmetric: string | null
  transitive: string | null
  notes: string | null
}

export interface ScopeResponse {
  trigger_entity_id: string
  nodes: Entity[]
  edges: Link[]
}

export interface RejectedRow {
  row: Record<string, unknown>
  reason: string
}

export interface IngestBatch {
  batch_id: string
  status: 'proposed' | 'committed'
  source_text: string
  entities: Entity[]
  links: Link[]
  rejected_entities: RejectedRow[]
  rejected_links: RejectedRow[]
}

export interface CommitResult {
  batch_id: string
  status: 'committed'
  entities: number
  links: number
}

export interface RelevanceAnnotation {
  entity_id: string
  relevance: 'high' | 'medium' | 'low'
  rationale: string
}

export interface ExplainResponse {
  trigger_entity_id: string
  annotations: RelevanceAnnotation[]
}

export interface EntityCreateInput {
  entity_id: string
  entity_class: string
  entity_subclass: string
  label: string
  aliases: string[]
  status: EntityStatus
  confidence: string
  source_ref: string
  attrs: Record<string, unknown>
}

export interface LinkCreateInput {
  link_id: string
  link_type: string
  source_entity: string
  target_entity: string
  direction: 'directed' | 'symmetric'
  assertion_status: 'reported' | 'assessed' | 'confirmed' | 'disputed'
  confidence: string
  source_ref: string
  attrs: Record<string, unknown>
}

export interface FeedPollResult {
  feed: string
  fetched?: number
  written?: number
  polled_at?: string
  error?: string | null
}

export interface FeedStatus {
  fetched?: number
  written?: number
  polled_at?: string
  error?: string | null
  schedule_interval_seconds: number | null
}
