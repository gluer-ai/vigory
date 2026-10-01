import { Mic, MicOff, Waypoints } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, ApiError } from '../../lib/api'
import type { IngestBatch } from '../../lib/types'
import { BatchReviewPanel } from '../layout/BatchReviewPanel'
import { Button } from '../ui/Button'
import { useVoiceSession, type ScenarioRef } from './useVoiceSession'

interface VoicePageProps {
  /** Jump the Scope view to a scenario the voice agent built. */
  onOpenScenario: (triggerEntityId: string, hops: number) => void
  /** Called after a proposal is committed so the graph can jump to it. */
  onCommitted: (firstEntityId: string) => void
}

interface Proposal {
  batch: IngestBatch
  committing: boolean
  committed: boolean
  error: string
}

const ROLE_STYLE = {
  user: 'text-[var(--color-text-primary)]',
  agent: 'text-[var(--color-focus)]',
  tool: 'font-mono text-xs text-[var(--color-text-muted)]',
} as const

/** Talk to the knowledge base. The agent searches the graph, builds
 * scenarios from it, and turns new facts into *proposals* that appear here
 * for a human to review and commit — it can never write to the graph. */
export function VoicePage({ onOpenScenario, onCommitted }: VoicePageProps) {
  const [configured, setConfigured] = useState<boolean | null>(null)
  const [proposals, setProposals] = useState<Proposal[]>([])
  const [scenarios, setScenarios] = useState<ScenarioRef[]>([])

  useEffect(() => {
    api.getVoiceStatus().then((s) => setConfigured(s.configured)).catch(() => setConfigured(false))
  }, [])

  const { status, error, lines, connect, disconnect } = useVoiceSession({
    onBatch: (batch) =>
      setProposals((p) => [{ batch, committing: false, committed: false, error: '' }, ...p]),
    onScenario: (s) =>
      setScenarios((prev) =>
        prev.some((x) => x.triggerEntityId === s.triggerEntityId && x.hops === s.hops)
          ? prev
          : [s, ...prev],
      ),
  })

  function patch(batchId: string, change: Partial<Proposal>) {
    setProposals((all) => all.map((p) => (p.batch.batch_id === batchId ? { ...p, ...change } : p)))
  }

  async function commit(batch: IngestBatch) {
    patch(batch.batch_id, { committing: true, error: '' })
    try {
      await api.commitBatch(batch.batch_id)
      patch(batch.batch_id, { committing: false, committed: true })
      const firstId = batch.entities[0]?.entity_id ?? batch.links[0]?.source_entity
      if (firstId) onCommitted(firstId)
    } catch (err) {
      patch(batch.batch_id, {
        committing: false,
        error: err instanceof ApiError ? err.message : 'Failed to reach the backend',
      })
    }
  }

  const live = status === 'live'
  return (
    <div className="grid h-full grid-cols-1 gap-4 overflow-y-auto p-6 lg:grid-cols-2">
      <section className="flex min-h-0 flex-col gap-3" aria-label="Voice conversation">
        <h2 className="text-base font-semibold text-[var(--color-text-primary)]">Voice agent</h2>
        <p className="text-sm text-[var(--color-text-muted)]">
          Ask about entities and how they connect, ask for a scenario, or state new facts. New
          facts become proposals you review on the right — nothing is saved until you commit.
        </p>

        {configured === false && (
          <p role="alert" className="text-sm text-[var(--color-status-destroyed)]">
            The voice agent isn't configured on the backend (VOICE_* variables).
          </p>
        )}

        <div className="flex items-center gap-3">
          {live || status === 'connecting' ? (
            <Button onClick={disconnect}>
              <MicOff size={14} /> End conversation
            </Button>
          ) : (
            <Button variant="primary" onClick={connect} disabled={configured !== true}>
              <Mic size={14} /> Start talking
            </Button>
          )}
          <span aria-live="polite" className="text-sm text-[var(--color-text-muted)]">
            {status === 'connecting' ? 'Connecting…' : live ? 'Listening' : ''}
          </span>
        </div>
        {error && (
          <p role="alert" className="text-sm text-[var(--color-status-destroyed)]">
            {error}
          </p>
        )}

        <div
          className="flex min-h-48 flex-1 flex-col gap-2 overflow-y-auto rounded-md border border-[var(--color-border)] p-3"
          aria-label="Transcript"
        >
          {lines.length === 0 && (
            <p className="text-sm text-[var(--color-text-muted)]">The transcript appears here.</p>
          )}
          {lines.map((l) => (
            <p key={l.id} className={`text-sm ${ROLE_STYLE[l.role]}`}>
              {l.role === 'user' ? 'You: ' : l.role === 'agent' ? 'Agent: ' : '↳ '}
              {l.text}
            </p>
          ))}
        </div>
      </section>

      <section className="flex flex-col gap-4" aria-label="Agent results">
        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">Scenarios</h3>
          {scenarios.length === 0 && (
            <p className="text-sm text-[var(--color-text-muted)]">
              Scenarios the agent builds from the graph show up here.
            </p>
          )}
          {scenarios.map((s) => (
            <Button key={`${s.triggerEntityId}-${s.hops}`} onClick={() => onOpenScenario(s.triggerEntityId, s.hops)}>
              <Waypoints size={14} /> Open {s.triggerEntityId} ({s.hops} hops) in Scope
            </Button>
          ))}
        </div>

        <div className="flex flex-col gap-3">
          <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">
            Proposed ingestions
          </h3>
          {proposals.length === 0 && (
            <p className="text-sm text-[var(--color-text-muted)]">
              Facts you tell the agent are extracted into a reviewable proposal here.
            </p>
          )}
          {proposals.map((p) => (
            <div
              key={p.batch.batch_id}
              className="rounded-md border border-[var(--color-border)] p-3"
            >
              {p.committed ? (
                <p className="text-sm text-[var(--color-status-active)]">
                  Batch {p.batch.batch_id} committed to the graph.
                </p>
              ) : (
                <BatchReviewPanel
                  batch={p.batch}
                  onCommit={() => commit(p.batch)}
                  committing={p.committing}
                  commitError={p.error}
                  onBatchUpdated={(batch) => patch(p.batch.batch_id, { batch })}
                />
              )}
            </div>
          ))}
        </div>
      </section>
    </div>
  )
}
