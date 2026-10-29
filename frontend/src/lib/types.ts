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
  origin?: string | null
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
  status: 'processing' | 'proposed' | 'committed' | 'error'
  /** Live progress while status === 'processing' (null otherwise). */
  stage?: string | null
  progress_current?: number | null
  progress_total?: number | null
  error_message?: string | null
  source_text: string
  entities: Entity[]
  links: Link[]
  rejected_entities: RejectedRow[]
  rejected_links: RejectedRow[]
}

export interface SynonymMatch {
  entity_id: string
  label: string
  aliases: string[]
  entity_class: string
  entity_subclass: string
}

export interface SuggestEntityResponse {
  match: SynonymMatch | null
  reason: string | null
}

export interface EntityClassification {
  idx: number
  entity_subclass: string
  entity_class: string
}

export interface LinkTypeFix {
  idx: number
  link_type: string
}

export interface ClassifyEntitiesResponse {
  classifications: EntityClassification[]
  /** Link types the agent resolved (existing, or newly added to the ontology). */
  link_types: LinkTypeFix[]
  added_classes: string[]
  added_links: string[]
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

export type DocumentStatus = 'processing' | 'proposed' | 'committed' | 'error'
export type DocumentFileType = 'txt' | 'md' | 'pdf' | 'docx' | 'xlsx'

export interface Document {
  document_id: string
  filename: string
  file_type: DocumentFileType
  size_bytes: number
  file_path: string
  status: DocumentStatus
  error_message: string | null
  batch_id: string | null
  /** Live progress while status === 'processing'. */
  stage?: string | null
  progress_current?: number | null
  progress_total?: number | null
  uploaded_at: string
}

export type DuplicateReason = 'same_id' | 'same_label' | 'alias' | 'reordered' | 'similar'

export interface DuplicateMatch {
  entity_id: string
  label: string
  aliases: string[]
  entity_class: string
  entity_subclass: string
  /** Already committed to the graph, or earlier in this same batch. */
  where: 'graph' | 'batch'
  reason: DuplicateReason
  score: number
}

export interface DuplicateItem {
  entity_id: string
  label: string
  aliases: string[]
  entity_class: string
  entity_subclass: string
  matches: DuplicateMatch[]
}

export type DuplicateDecision =
  | { entity_id: string; action: 'keep_separate' }
  | { entity_id: string; action: 'remove'; target_id: string }

export interface RemovalPreview {
  document_id: string
  filename: string
  status: string
  entities: { remove: number; sample: string[]; kept_shared: number; untagged_import: number }
  links: { remove: number; untagged_import: number }
  classes: { remove: string[]; kept: { key: string; reason: string }[] }
  link_types: { remove: string[]; kept: { type: string; reason: string }[] }
}

export interface DeleteDocumentResult {
  document_id: string
  data_removed: boolean
  entities?: RemovalPreview['entities']
  links?: RemovalPreview['links']
  classes?: RemovalPreview['classes']
  link_types?: RemovalPreview['link_types']
}

export type LlmState = 'ready' | 'busy' | 'downloading' | 'unreachable' | 'unconfigured'

export interface LlmStatus {
  state: LlmState
  provider: string
  model: string
  /** Self-hosted (Ollama) model. */
  local: boolean
  reachable: boolean | null
  model_installed: boolean | null
  /** Model is resident in memory; false means the next call pays a cold-start load. */
  model_loaded: boolean | null
  active: { operation: string; elapsed_seconds: number }[]
  last_call: { operation: string; duration_seconds: number; ok: boolean } | null
}

export interface VoiceTool {
  type: 'function'
  name: string
  description: string
  parameters: Record<string, unknown>
}

export interface VoiceSessionResponse {
  sdp: string
  agent: { name?: string; voice?: string; initial_greeting?: string | null }
  instructions: string
  tools: VoiceTool[]
}

/** `output` is what the voice model hears; `ui` is only for the panel. */
export type VoiceUiEffect =
  | { type: 'batch'; batch: IngestBatch }
  | { type: 'scenario'; trigger_entity_id: string; hops: number }

export interface VoiceToolResult {
  output: Record<string, unknown>
  ui?: VoiceUiEffect
}

// ---- Sandbox: a private, editable working copy of part of the graph ----------

export interface SandboxNodeData {
  id: string
  origin: 'graph' | 'new'
  label: string
  entity_subclass: string
  x: number
  y: number
  /** What a graph-origin node looked like when copied (for "what changed"). */
  base: { label: string; entity_subclass: string } | null
  /** The entity or link this one sits inside (null = top level). */
  parent: string | null
}

export interface SandboxEdgeData {
  id: string
  origin: 'graph' | 'new'
  source: string
  target: string
  link_type: string
  base_type: string | null
  parent: string | null
}

export interface SandboxSummary {
  sandbox_id: string
  name: string
  version: number
  node_count: number
  edge_count: number
  updated_at: string
}

export interface Sandbox extends SandboxSummary {
  nodes: SandboxNodeData[]
  edges: SandboxEdgeData[]
}

// ---- Sandbox assistant (chat / voice edits) -----------------------------------

export interface AgentOpResult {
  op: string
  ok: boolean
  message: string
}

export interface AgentEditResponse {
  nodes: SandboxNodeData[]
  edges: SandboxEdgeData[]
  results: AgentOpResult[]
  changed: boolean
}

export interface AgentChatResponse extends AgentEditResponse {
  reply: string
}

export interface AgentOpsResponse extends AgentEditResponse {
  summary: string
}

export interface AgentOp {
  op: string
  [field: string]: unknown
}

/** Result of extracting a scenario for the sandbox (nothing is saved server-side). */
export interface ScenarioExtract {
  entities: Pick<Entity, 'entity_id' | 'label' | 'entity_subclass'>[]
  links: Pick<Link, 'link_type' | 'source_entity' | 'target_entity'>[]
  rejected_entities: unknown[]
  rejected_links: unknown[]
  /** Real graph entities the text referred to; copied in so links can reach them. */
  existing_entities: Pick<Entity, 'entity_id' | 'label' | 'entity_subclass'>[]
}

/** A sandbox turned into a proposed ingest batch for review (nothing is saved yet). */
export interface SandboxProposal {
  batch: IngestBatch
  /** New sandbox entities treated as an existing real entity (so not duplicated). */
  merged: { label: string; into_id: string; into_label: string }[]
  skipped: { reason: string; count: number }[]
  notes: string[]
  /** Real entities the new links attach to (so the picture has no dangling lines). */
  existing_entities: { entity_id: string; label: string; entity_subclass: string }[]
}
