import * as Tabs from '@radix-ui/react-tabs'
import { useEffect, useState } from 'react'
import { api, ApiError } from '../../lib/api'
import { classMeta } from '../../lib/entityClass'
import type { Entity, Link } from '../../lib/types'
import { Button } from '../ui/Button'
import { ConfidenceChip, StatusChip } from '../ui/Chip'

const PAGE_SIZE = 25

interface BrowsePageProps {
  onSelectEntity: (entityId: string) => void
}

/** Full browse-all view: every entity and every link in the graph, paged,
 * independent of any trigger/scope — the counterpart to the scoped canvas. */
export function BrowsePage({ onSelectEntity }: BrowsePageProps) {
  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="border-b border-[var(--color-border)] px-6 py-4">
        <h1 className="text-base font-semibold text-[var(--color-text-primary)]">
          Browse graph
        </h1>
        <p className="mt-0.5 text-sm text-[var(--color-text-muted)]">
          Every entity and link currently in the graph, independent of any scope.
        </p>
      </div>
      <Tabs.Root defaultValue="entities" className="flex flex-1 flex-col overflow-hidden">
        <Tabs.List className="flex gap-4 border-b border-[var(--color-border)] px-6">
          <Tabs.Trigger
            value="entities"
            className="border-b-2 border-transparent py-2.5 text-sm font-medium text-[var(--color-text-muted)] data-[state=active]:border-[var(--color-focus)] data-[state=active]:text-[var(--color-text-primary)]"
          >
            Entities
          </Tabs.Trigger>
          <Tabs.Trigger
            value="links"
            className="border-b-2 border-transparent py-2.5 text-sm font-medium text-[var(--color-text-muted)] data-[state=active]:border-[var(--color-focus)] data-[state=active]:text-[var(--color-text-primary)]"
          >
            Links
          </Tabs.Trigger>
        </Tabs.List>
        <Tabs.Content value="entities" className="flex-1 overflow-y-auto">
          <EntitiesTab onSelectEntity={onSelectEntity} />
        </Tabs.Content>
        <Tabs.Content value="links" className="flex-1 overflow-y-auto">
          <LinksTab onSelectEntity={onSelectEntity} />
        </Tabs.Content>
      </Tabs.Root>
    </div>
  )
}

function EntitiesTab({ onSelectEntity }: { onSelectEntity: (id: string) => void }) {
  const [entities, setEntities] = useState<Entity[]>([])
  const [offset, setOffset] = useState(0)
  const [status, setStatus] = useState<'loading' | 'error' | 'ready'>('loading')
  const [errorMessage, setErrorMessage] = useState('')

  useEffect(() => {
    setStatus('loading')
    api
      .listEntities({ limit: PAGE_SIZE, offset })
      .then((rows) => {
        setEntities(rows)
        setStatus('ready')
      })
      .catch((err) => {
        setErrorMessage(err instanceof ApiError ? err.message : 'Failed to reach the backend')
        setStatus('error')
      })
  }, [offset])

  if (status === 'loading' && entities.length === 0)
    return <p className="p-6 text-sm text-[var(--color-text-muted)]">Loading…</p>
  if (status === 'error')
    return (
      <p role="alert" className="p-6 text-sm text-[var(--color-status-destroyed)]">
        {errorMessage}
      </p>
    )

  return (
    <div className="p-6">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-[var(--color-border)] text-left text-[var(--color-text-muted)]">
            <th className="py-2 pe-3 font-medium">Label</th>
            <th className="py-2 pe-3 font-medium">Class</th>
            <th className="py-2 pe-3 font-medium">Subclass</th>
            <th className="py-2 pe-3 font-medium">Status</th>
            <th className="py-2 pe-3 font-medium">Confidence</th>
          </tr>
        </thead>
        <tbody>
          {entities.map((e) => {
            const { icon: Icon, colorVar } = classMeta(e.entity_class, e.entity_subclass)
            return (
              <tr key={e.entity_id} className="border-b border-[var(--color-border)]">
                <td className="py-2 pe-3">
                  <button
                    type="button"
                    onClick={() => onSelectEntity(e.entity_id)}
                    className="flex items-center gap-2 text-start text-[var(--color-text-primary)] hover:underline"
                  >
                    <Icon size={14} style={{ color: `var(${colorVar})` }} aria-hidden="true" />
                    {e.label}
                    <span className="font-mono text-xs text-[var(--color-text-muted)]">
                      {e.entity_id}
                    </span>
                  </button>
                </td>
                <td className="py-2 pe-3 font-mono text-xs text-[var(--color-text-muted)]">
                  {e.entity_class}
                </td>
                <td className="py-2 pe-3 font-mono text-xs text-[var(--color-text-muted)]">
                  {e.entity_subclass}
                </td>
                <td className="py-2 pe-3">
                  <StatusChip status={e.status} />
                </td>
                <td className="py-2 pe-3">
                  <ConfidenceChip code={e.confidence} />
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      {entities.length === 0 && (
        <p className="py-6 text-sm text-[var(--color-text-muted)]">No entities yet.</p>
      )}
      <Pager offset={offset} count={entities.length} pageSize={PAGE_SIZE} onChange={setOffset} />
    </div>
  )
}

function LinksTab({ onSelectEntity }: { onSelectEntity: (id: string) => void }) {
  const [links, setLinks] = useState<Link[]>([])
  const [offset, setOffset] = useState(0)
  const [status, setStatus] = useState<'loading' | 'error' | 'ready'>('loading')
  const [errorMessage, setErrorMessage] = useState('')

  useEffect(() => {
    setStatus('loading')
    api
      .listLinks({ limit: PAGE_SIZE, offset })
      .then((rows) => {
        setLinks(rows)
        setStatus('ready')
      })
      .catch((err) => {
        setErrorMessage(err instanceof ApiError ? err.message : 'Failed to reach the backend')
        setStatus('error')
      })
  }, [offset])

  if (status === 'loading' && links.length === 0)
    return <p className="p-6 text-sm text-[var(--color-text-muted)]">Loading…</p>
  if (status === 'error')
    return (
      <p role="alert" className="p-6 text-sm text-[var(--color-status-destroyed)]">
        {errorMessage}
      </p>
    )

  return (
    <div className="p-6">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-[var(--color-border)] text-left text-[var(--color-text-muted)]">
            <th className="py-2 pe-3 font-medium">Type</th>
            <th className="py-2 pe-3 font-medium">Source</th>
            <th className="py-2 pe-3 font-medium">Target</th>
            <th className="py-2 pe-3 font-medium">Assertion</th>
            <th className="py-2 pe-3 font-medium">Confidence</th>
          </tr>
        </thead>
        <tbody>
          {links.map((l) => (
            <tr key={l.link_id} className="border-b border-[var(--color-border)]">
              <td className="py-2 pe-3 font-mono text-xs">{l.link_type}</td>
              <td className="py-2 pe-3">
                <button
                  type="button"
                  onClick={() => onSelectEntity(l.source_entity)}
                  className="font-mono text-xs text-[var(--color-text-primary)] hover:underline"
                >
                  {l.source_entity}
                </button>
              </td>
              <td className="py-2 pe-3">
                <button
                  type="button"
                  onClick={() => onSelectEntity(l.target_entity)}
                  className="font-mono text-xs text-[var(--color-text-primary)] hover:underline"
                >
                  {l.target_entity}
                </button>
              </td>
              <td className="py-2 pe-3 text-xs text-[var(--color-text-muted)]">
                {l.assertion_status}
              </td>
              <td className="py-2 pe-3">
                <ConfidenceChip code={l.confidence} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {links.length === 0 && (
        <p className="py-6 text-sm text-[var(--color-text-muted)]">No links yet.</p>
      )}
      <Pager offset={offset} count={links.length} pageSize={PAGE_SIZE} onChange={setOffset} />
    </div>
  )
}

function Pager({
  offset,
  count,
  pageSize,
  onChange,
}: {
  offset: number
  count: number
  pageSize: number
  onChange: (offset: number) => void
}) {
  return (
    <div className="mt-4 flex items-center justify-between">
      <span className="text-xs text-[var(--color-text-muted)]">
        Showing {offset + 1}–{offset + count}
      </span>
      <div className="flex gap-2">
        <Button onClick={() => onChange(Math.max(0, offset - pageSize))} disabled={offset === 0}>
          Previous
        </Button>
        <Button onClick={() => onChange(offset + pageSize)} disabled={count < pageSize}>
          Next
        </Button>
      </div>
    </div>
  )
}
