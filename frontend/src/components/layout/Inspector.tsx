import { Fragment } from 'react'
import { classMeta } from '../../lib/entityClass'
import type { Entity, RelevanceAnnotation } from '../../lib/types'
import { ConfidenceChip, StatusChip } from '../ui/Chip'

export function Inspector({
  entity,
  annotation,
}: {
  entity: Entity
  annotation?: RelevanceAnnotation
}) {
  const { icon: Icon, colorVar } = classMeta(entity.entity_class)
  return (
    <div className="flex flex-col gap-4 text-sm">
      <div className="flex items-center gap-2">
        <Icon size={18} style={{ color: `var(${colorVar})` }} aria-hidden="true" />
        <span className="text-base font-semibold text-[var(--color-text-primary)]">{entity.label}</span>
      </div>

      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-2">
        <dt className="text-[var(--color-text-muted)]">Entity ID</dt>
        <dd className="font-mono text-xs">{entity.entity_id}</dd>

        <dt className="text-[var(--color-text-muted)]">Class</dt>
        <dd className="font-mono text-xs">{entity.entity_class}</dd>

        <dt className="text-[var(--color-text-muted)]">Subclass</dt>
        <dd className="break-all font-mono text-xs">{entity.entity_subclass}</dd>

        <dt className="text-[var(--color-text-muted)]">Status</dt>
        <dd>
          <StatusChip status={entity.status} />
        </dd>

        <dt className="text-[var(--color-text-muted)]">Confidence</dt>
        <dd>
          <ConfidenceChip code={entity.confidence} />
        </dd>

        <dt className="text-[var(--color-text-muted)]">Source ref</dt>
        <dd className="font-mono text-xs">{entity.source_ref}</dd>

        {entity.aliases.length > 0 && (
          <>
            <dt className="text-[var(--color-text-muted)]">Aliases</dt>
            <dd>{entity.aliases.join(', ')}</dd>
          </>
        )}

        {entity.first_observed && (
          <>
            <dt className="text-[var(--color-text-muted)]">First observed</dt>
            <dd>{entity.first_observed}</dd>
          </>
        )}
        {entity.last_observed && (
          <>
            <dt className="text-[var(--color-text-muted)]">Last observed</dt>
            <dd>{entity.last_observed}</dd>
          </>
        )}
      </dl>

      {annotation && (
        <div className="rounded-md border border-[var(--color-border)] p-3">
          <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
            Agent assessment ({annotation.relevance} relevance)
          </h3>
          <p className="text-xs text-[var(--color-text-primary)]">{annotation.rationale}</p>
        </div>
      )}

      {Object.keys(entity.attrs).length > 0 && (
        <div>
          <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
            Attributes
          </h3>
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
            {Object.entries(entity.attrs).map(([k, v]) => (
              <Fragment key={k}>
                <dt className="font-mono text-xs text-[var(--color-text-muted)]">{k}</dt>
                <dd className="text-xs">{String(v)}</dd>
              </Fragment>
            ))}
          </dl>
        </div>
      )}
    </div>
  )
}
