import type {
  ClassDef,
  CommitResult,
  Entity,
  EntityCreateInput,
  ExplainResponse,
  FeedPollResult,
  FeedStatus,
  IngestBatch,
  Link,
  LinkCreateInput,
  LinkDef,
  ScopeResponse,
} from './types'

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: { 'content-type': 'application/json', ...init?.headers },
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }))
    throw new ApiError(res.status, body.detail ?? res.statusText)
  }
  return res.json()
}

export const api = {
  getClasses: () => request<ClassDef[]>('/schema/classes'),
  getLinkDefs: () => request<LinkDef[]>('/schema/links'),
  getEntity: (id: string) => request<Entity>(`/entities/${encodeURIComponent(id)}`),
  searchEntities: (q: string) =>
    request<Entity[]>(`/entities/search?q=${encodeURIComponent(q)}`),
  listEntities: (opts: { limit: number; offset: number; entityClass?: string; bbox?: string }) => {
    const params = new URLSearchParams({
      limit: String(opts.limit),
      offset: String(opts.offset),
    })
    if (opts.entityClass) params.set('entity_class', opts.entityClass)
    if (opts.bbox) params.set('bbox', opts.bbox)
    return request<Entity[]>(`/entities?${params}`)
  },
  listLinks: (opts: { limit: number; offset: number; linkType?: string }) => {
    const params = new URLSearchParams({
      limit: String(opts.limit),
      offset: String(opts.offset),
    })
    if (opts.linkType) params.set('link_type', opts.linkType)
    return request<Link[]>(`/links?${params}`)
  },
  getScope: (entityId: string, hops: number, linkTypes?: string[]) => {
    const params = new URLSearchParams({ hops: String(hops) })
    if (linkTypes?.length) params.set('link_types', linkTypes.join(','))
    return request<ScopeResponse>(`/scenarios/${encodeURIComponent(entityId)}/scope?${params}`)
  },
  ingestText: (text: string) =>
    request<IngestBatch>('/ingest', { method: 'POST', body: JSON.stringify({ text }) }),
  commitBatch: (batchId: string) =>
    request<CommitResult>(`/ingest/${encodeURIComponent(batchId)}/commit`, { method: 'POST' }),
  explainScope: (entityId: string, hops: number) =>
    request<ExplainResponse>(
      `/scenarios/${encodeURIComponent(entityId)}/explain?hops=${hops}`,
      { method: 'POST' },
    ),
  createEntity: (entity: EntityCreateInput) =>
    request<Entity>('/entities', { method: 'POST', body: JSON.stringify(entity) }),
  createLink: (link: LinkCreateInput) =>
    request<Link>('/links', { method: 'POST', body: JSON.stringify(link) }),
  getFeedsStatus: () => request<Record<string, FeedStatus>>('/feeds/status'),
  pollFeed: (name: string) => request<FeedPollResult>(`/feeds/${encodeURIComponent(name)}/poll`, { method: 'POST' }),
  setFeedSchedule: (name: string, intervalSeconds: number | null) =>
    request<{ feed: string; schedule_interval_seconds: number | null }>(
      `/feeds/${encodeURIComponent(name)}/schedule`,
      { method: 'PUT', body: JSON.stringify({ interval_seconds: intervalSeconds }) },
    ),
}

export { ApiError }
