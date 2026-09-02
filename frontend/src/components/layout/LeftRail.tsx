import { LayoutGrid, MapIcon, PlusCircle, RadioTower, Sparkles, Waypoints } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../../lib/api'
import type { LinkDef } from '../../lib/types'
import { Button } from '../ui/Button'
import { Select } from '../ui/Select'
import { Stepper } from '../ui/Stepper'
import { EntitySearch } from './EntitySearch'

interface LeftRailProps {
  page: 'scope' | 'browse' | 'map' | 'feeds'
  onPageChange: (page: 'scope' | 'browse' | 'map' | 'feeds') => void
  triggerEntityId: string
  onTriggerChange: (id: string) => void
  hops: number
  onHopsChange: (hops: number) => void
  view: 'canvas' | 'list'
  onViewChange: (view: 'canvas' | 'list') => void
  linkTypeFilter: string[]
  onLinkTypeFilterChange: (types: string[]) => void
  onOpenIngest: () => void
  onOpenAddResource: () => void
  onExplain: () => void
  explainStatus: 'idle' | 'loading' | 'error' | 'done'
  explainError: string
  hasScope: boolean
}

export function LeftRail({
  page,
  onPageChange,
  triggerEntityId,
  onTriggerChange,
  hops,
  onHopsChange,
  view,
  onViewChange,
  linkTypeFilter,
  onLinkTypeFilterChange,
  onOpenIngest,
  onOpenAddResource,
  onExplain,
  explainStatus,
  explainError,
  hasScope,
}: LeftRailProps) {
  function toggleLinkType(type: string) {
    onLinkTypeFilterChange(
      linkTypeFilter.includes(type)
        ? linkTypeFilter.filter((t) => t !== type)
        : [...linkTypeFilter, type],
    )
  }
  const [linkDefs, setLinkDefs] = useState<LinkDef[]>([])

  useEffect(() => {
    api.getLinkDefs().then(setLinkDefs).catch(() => setLinkDefs([]))
  }, [])

  return (
    <>
      <div>
        <h1 className="text-sm font-semibold text-[var(--color-text-primary)]">Vigory.ai</h1>
        <p className="mt-0.5 text-xs text-[var(--color-text-muted)]">Scenario scoping</p>
      </div>

      <div className="grid grid-cols-2 gap-1 rounded-md border border-[var(--color-border)] p-1" role="tablist">
        {(
          [
            ['scope', Waypoints, 'Scope'],
            ['browse', LayoutGrid, 'Browse all'],
            ['map', MapIcon, 'Map'],
            ['feeds', RadioTower, 'Feeds'],
          ] as const
        ).map(([id, Icon, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={page === id}
            onClick={() => onPageChange(id)}
            className={`flex items-center justify-center gap-1.5 rounded px-2 py-1.5 text-sm font-medium transition-colors duration-[var(--duration-fast)] ${
              page === id
                ? 'bg-[var(--color-surface-hover)] text-[var(--color-text-primary)]'
                : 'text-[var(--color-text-muted)]'
            }`}
          >
            <Icon size={14} /> {label}
          </button>
        ))}
      </div>

      <div className="flex flex-col gap-2 rounded-md border border-[var(--color-border)] p-3">
        <span className="flex items-center gap-1.5 text-sm font-medium text-[var(--color-text-primary)]">
          <Sparkles size={14} className="text-[var(--color-focus)]" />
          Extraction agent
        </span>
        <p className="text-xs text-[var(--color-text-muted)]">
          Describe a scenario in plain text — the agent proposes entities and links for you to
          review and commit.
        </p>
        <Button variant="primary" onClick={onOpenIngest}>
          Ingest scenario…
        </Button>
      </div>

      <div className="flex flex-col gap-2 rounded-md border border-[var(--color-border)] p-3">
        <span className="flex items-center gap-1.5 text-sm font-medium text-[var(--color-text-primary)]">
          <PlusCircle size={14} className="text-[var(--color-text-muted)]" />
          Manual entry
        </span>
        <p className="text-xs text-[var(--color-text-muted)]">
          Add a single entity, link, or attribute yourself — validated against the same
          ontology as the extraction agent.
        </p>
        <Button onClick={onOpenAddResource}>Add entity or link…</Button>
      </div>

      <EntitySearch onSelect={onTriggerChange} />

      <div className="flex flex-col gap-2">
        <label htmlFor="trigger-input" className="text-sm text-[var(--color-text-muted)]">
          Trigger entity ID
        </label>
        <input
          id="trigger-input"
          type="text"
          value={triggerEntityId}
          onChange={(e) => onTriggerChange(e.target.value)}
          placeholder="e.g. P-1042"
          className="rounded-md border border-[var(--color-border)] bg-[var(--color-surface-1)] px-3 py-1.5 font-mono text-sm text-[var(--color-text-primary)] placeholder:text-[var(--color-text-muted)] focus-visible:border-[var(--color-focus)]"
        />
        <p className="text-xs text-[var(--color-text-muted)]">
          Set automatically once the agent commits a scenario, or type an existing ID directly.
        </p>
      </div>

      <Stepper label="Hop depth" value={hops} onChange={onHopsChange} min={1} max={5} />

      <div className="flex flex-col gap-2 rounded-md border border-[var(--color-border)] p-3">
        <span className="flex items-center gap-1.5 text-sm font-medium text-[var(--color-text-primary)]">
          <Sparkles size={14} className="text-[var(--color-focus)]" />
          Relevance agent
        </span>
        <p className="text-xs text-[var(--color-text-muted)]">
          Asks the agent to assess and explain each scoped entity's relevance to the trigger.
        </p>
        <Button onClick={onExplain} disabled={!hasScope || explainStatus === 'loading'}>
          {explainStatus === 'loading' ? 'Explaining…' : 'Explain relevance'}
        </Button>
        {explainStatus === 'error' && (
          <p role="alert" className="text-xs text-[var(--color-status-destroyed)]">
            {explainError}
          </p>
        )}
        {explainStatus === 'done' && (
          <p className="text-xs text-[var(--color-status-active)]">
            Relevance annotations applied to the graph.
          </p>
        )}
      </div>

      <div className="flex flex-col gap-2">
        <span className="text-sm text-[var(--color-text-muted)]">View</span>
        <Select
          value={view}
          onValueChange={(v) => onViewChange(v as 'canvas' | 'list')}
          options={[
            { value: 'canvas', label: 'Graph canvas' },
            { value: 'list', label: 'List (keyboard-accessible)' },
          ]}
          aria-label="View mode"
        />
      </div>

      <div className="flex flex-col gap-2">
        <span className="text-sm text-[var(--color-text-muted)]">
          Link type filter {linkTypeFilter.length > 0 ? `(${linkTypeFilter.length})` : '(all)'}
        </span>
        <div
          className="flex max-h-64 flex-wrap gap-1.5 overflow-y-auto rounded-md border border-[var(--color-border)] p-2"
          role="group"
          aria-label="Filter scope by link type"
        >
          {linkDefs.length === 0 && <p className="text-xs text-[var(--color-text-muted)]">Loading…</p>}
          {linkDefs.map((l) => {
            const active = linkTypeFilter.includes(l.type)
            return (
              <button
                key={l.type}
                type="button"
                onClick={() => toggleLinkType(l.type)}
                aria-pressed={active}
                className={`rounded-full border px-2 py-0.5 font-mono text-[11px] transition-colors duration-[var(--duration-fast)] ${
                  active
                    ? 'border-[var(--color-focus)] bg-[var(--color-focus)] text-[var(--color-text-inverse)]'
                    : 'border-[var(--color-border)] text-[var(--color-text-muted)] hover:border-[var(--color-border-strong)]'
                }`}
              >
                {l.type}
              </button>
            )
          })}
        </div>
      </div>
    </>
  )
}
