import * as Dialog from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import { useState } from 'react'
import { api, ApiError } from '../../lib/api'
import { SAMPLE_SCENARIOS } from '../../lib/sampleScenarios'
import type { ScenarioExtract } from '../../lib/types'
import { Button } from '../ui/Button'

interface ScenarioDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** The sandbox currently open, if any: results can be added straight to it. */
  current: { name: string; add: (extract: ScenarioExtract) => string } | null
  /** Create a new sandbox holding the result; returns a confirmation message. */
  createNew: (name: string, extract: ScenarioExtract) => Promise<string>
}

type Phase = 'draft' | 'extracting' | 'preview' | 'adding' | 'done' | 'error'
type Target = 'current' | 'new'

const leaf = (key: string) => key.split('.').slice(1).join(' › ') || key

/** Paste a scenario -> the extraction agent proposes entities and links ->
 * they land in a sandbox you can rearrange. Nothing here is saved to the
 * real knowledge graph. */
export function ScenarioDialog({ open, onOpenChange, current, createNew }: ScenarioDialogProps) {
  const [text, setText] = useState('')
  const [phase, setPhase] = useState<Phase>('draft')
  const [extract, setExtract] = useState<ScenarioExtract | null>(null)
  const [error, setError] = useState('')
  const [target, setTarget] = useState<Target>('current')
  const [newName, setNewName] = useState('')
  const [doneMessage, setDoneMessage] = useState('')

  function reset() {
    setText('')
    setPhase('draft')
    setExtract(null)
    setError('')
    setNewName('')
    setDoneMessage('')
  }

  function handleOpenChange(next: boolean) {
    if (!next) reset()
    onOpenChange(next)
  }

  async function handleExtract() {
    setPhase('extracting')
    setError('')
    try {
      const result = await api.extractScenario(text.trim())
      setExtract(result)
      setTarget(current ? 'current' : 'new')
      setNewName(result.entities[0]?.label ? `Scenario: ${result.entities[0].label}`.slice(0, 100) : 'Scenario')
      setPhase('preview')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to reach the backend')
      setPhase('error')
    }
  }

  async function handleAdd() {
    if (!extract) return
    setPhase('adding')
    setError('')
    try {
      const message =
        target === 'current' && current ? current.add(extract) : await createNew(newName.trim(), extract)
      setDoneMessage(message)
      setPhase('done')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not add the scenario')
      setPhase('preview')
    }
  }

  const empty = extract !== null && extract.entities.length === 0 && extract.existing_entities.length === 0
  const rejected = (extract?.rejected_entities.length ?? 0) + (extract?.rejected_links.length ?? 0)
  const labelOf = (id: string) =>
    extract?.entities.find((e) => e.entity_id === id)?.label ??
    extract?.existing_entities.find((e) => e.entity_id === id)?.label ??
    id

  return (
    <Dialog.Root open={open} onOpenChange={handleOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/60" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 max-h-[85vh] w-[560px] max-w-[92vw] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-1)] p-5 shadow-2xl outline-none">
          <div className="mb-4 flex items-center justify-between">
            <Dialog.Title className="text-sm font-semibold text-[var(--color-text-primary)]">
              Ingest scenario into a sandbox
            </Dialog.Title>
            <Dialog.Close asChild>
              <button
                type="button"
                aria-label="Close"
                className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
              >
                <X size={16} />
              </button>
            </Dialog.Close>
          </div>
          <Dialog.Description className="sr-only">
            Extract entities and links from text and add them to a private sandbox.
          </Dialog.Description>

          {(phase === 'draft' || phase === 'extracting' || phase === 'error') && (
            <div className="flex flex-col gap-3">
              <label htmlFor="scenario-text" className="text-sm text-[var(--color-text-muted)]">
                Describe the scenario in free text. The extraction agent proposes entities and links, and
                they are added to a sandbox you can rearrange. Nothing is saved to the real graph.
              </label>
              {phase === 'draft' && (
                <div className="flex flex-col gap-1.5">
                  <span className="text-xs text-[var(--color-text-muted)]">Try a sample:</span>
                  <div className="flex flex-wrap gap-1.5">
                    {SAMPLE_SCENARIOS.map((sample) => (
                      <button
                        key={sample.title}
                        type="button"
                        onClick={() => setText(sample.text)}
                        className="rounded-full border border-[var(--color-border)] px-2.5 py-1 text-xs text-[var(--color-text-muted)] hover:border-[var(--color-border-strong)] hover:text-[var(--color-text-primary)]"
                      >
                        {sample.title}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              <textarea
                id="scenario-text"
                value={text}
                onChange={(e) => setText(e.target.value)}
                rows={8}
                maxLength={20000}
                disabled={phase === 'extracting'}
                placeholder="e.g. Major Ivan Petrov commands the 3rd Motor Rifle Battalion, based near..."
                className="rounded-md border border-[var(--color-border)] bg-[var(--color-surface-0)] p-3 text-sm text-[var(--color-text-primary)] placeholder:text-[var(--color-text-muted)] focus-visible:border-[var(--color-focus)]"
              />
              {phase === 'error' && (
                <p role="alert" className="text-sm text-[var(--color-status-destroyed)]">{error}</p>
              )}
              <Button variant="primary" onClick={handleExtract} disabled={!text.trim() || phase === 'extracting'}>
                {phase === 'extracting' ? 'Extracting…' : 'Extract entities & links'}
              </Button>
            </div>
          )}

          {(phase === 'preview' || phase === 'adding') && extract && (
            <div className="flex flex-col gap-3">
              {empty ? (
                <p className="text-sm text-[var(--color-text-primary)]">
                  No entities could be extracted from that text. Try describing who or what is involved and
                  how they are connected.
                </p>
              ) : (
                <>
                  <p className="text-sm text-[var(--color-text-primary)]">
                    Found {extract.entities.length} {extract.entities.length === 1 ? 'entity' : 'entities'} and{' '}
                    {extract.links.length} {extract.links.length === 1 ? 'link' : 'links'}
                    {extract.existing_entities.length > 0 &&
                      `, plus ${extract.existing_entities.length} existing graph ${extract.existing_entities.length === 1 ? 'entity' : 'entities'} it refers to`}
                    .
                  </p>
                  <ul className="max-h-48 overflow-y-auto rounded-md border border-[var(--color-border)] p-2 text-xs">
                    {extract.entities.map((e) => (
                      <li key={e.entity_id} className="flex justify-between gap-3 py-0.5">
                        <span className="text-[var(--color-text-primary)]">{e.label}</span>
                        <span className="truncate text-[var(--color-text-muted)]">{leaf(e.entity_subclass)}</span>
                      </li>
                    ))}
                    {extract.links.map((l, i) => (
                      <li key={i} className="py-0.5 text-[var(--color-text-muted)]">
                        {labelOf(l.source_entity)} <span className="font-mono">{l.link_type}</span>{' '}
                        {labelOf(l.target_entity)}
                      </li>
                    ))}
                  </ul>
                  {rejected > 0 && (
                    <p className="text-xs text-[var(--color-text-muted)]">
                      {rejected} {rejected === 1 ? 'item' : 'items'} did not fit the ontology and will not be
                      added.
                    </p>
                  )}

                  <fieldset className="flex flex-col gap-2" disabled={phase === 'adding'}>
                    <legend className="mb-1 text-xs text-[var(--color-text-muted)]">Add to</legend>
                    {current && (
                      <label className="flex items-center gap-2 text-sm text-[var(--color-text-primary)]">
                        <input type="radio" name="scenario-target" checked={target === 'current'} onChange={() => setTarget('current')} />
                        <span>
                          This sandbox: <span className="font-medium">{current.name}</span>
                        </span>
                      </label>
                    )}
                    <label className="flex items-center gap-2 text-sm text-[var(--color-text-primary)]">
                      <input type="radio" name="scenario-target" checked={target === 'new'} onChange={() => setTarget('new')} />
                      A new sandbox
                    </label>
                    {target === 'new' && (
                      <>
                        <label htmlFor="scenario-new-name" className="sr-only">New sandbox name</label>
                        <input
                          id="scenario-new-name"
                          value={newName}
                          maxLength={100}
                          onChange={(e) => setNewName(e.target.value)}
                          className="rounded-md border border-[var(--color-border)] bg-[var(--color-surface-0)] px-2 py-1.5 text-sm text-[var(--color-text-primary)] focus-visible:border-[var(--color-focus)]"
                        />
                      </>
                    )}
                  </fieldset>
                </>
              )}
              {error && <p role="alert" className="text-sm text-[var(--color-status-destroyed)]">{error}</p>}
              <div className="flex gap-2">
                {!empty && (
                  <Button
                    variant="primary"
                    onClick={handleAdd}
                    disabled={phase === 'adding' || (target === 'new' && !newName.trim())}
                  >
                    {phase === 'adding' ? 'Adding…' : 'Add to sandbox'}
                  </Button>
                )}
                <Button onClick={reset} disabled={phase === 'adding'}>Start over</Button>
              </div>
            </div>
          )}

          {phase === 'done' && (
            <div className="flex flex-col items-start gap-3">
              <p role="status" className="text-sm text-[var(--color-text-primary)]">{doneMessage}</p>
              <Button variant="primary" onClick={() => handleOpenChange(false)}>View sandbox</Button>
            </div>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
