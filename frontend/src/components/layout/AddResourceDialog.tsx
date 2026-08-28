import * as Dialog from '@radix-ui/react-dialog'
import * as Tabs from '@radix-ui/react-tabs'
import { Plus, Trash2, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, ApiError } from '../../lib/api'
import { ENTITY_CLASS_META } from '../../lib/entityClass'
import type { ClassDef, LinkDef } from '../../lib/types'
import { Button } from '../ui/Button'
import { Select } from '../ui/Select'

interface AddResourceDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Called after a successful create so the caller can jump the canvas to it. */
  onCreated: (entityId: string) => void
}

const ROOT_CLASSES = Object.keys(ENTITY_CLASS_META)
const ID_PREFIX: Record<string, string> = {
  PERSON: 'P',
  ORGANIZATION: 'O',
  LOCATION: 'L',
  FACILITY: 'F',
  VEHICLE: 'V',
  EQUIPMENT: 'EQ',
  EVENT: 'EV',
  INFORMATION_OBJECT: 'I',
  IDENTIFIER: 'ID',
}

function randomSuffix() {
  return Math.floor(1000 + Math.random() * 9000).toString()
}

function labelFor(text: string) {
  return (
    <span className="text-sm text-[var(--color-text-muted)]">{text}</span>
  )
}

/** Attribute rows editor: a small list of free-form key/value pairs, shared
 * by the entity and link forms — the graph's attrs are always an open map,
 * so there's no fixed schema to render a smarter form against. */
function AttrsEditor({
  rows,
  onChange,
}: {
  rows: { key: string; value: string }[]
  onChange: (rows: { key: string; value: string }[]) => void
}) {
  function update(i: number, field: 'key' | 'value', value: string) {
    onChange(rows.map((r, idx) => (idx === i ? { ...r, [field]: value } : r)))
  }
  function remove(i: number) {
    onChange(rows.filter((_, idx) => idx !== i))
  }
  return (
    <div className="flex flex-col gap-2">
      {labelFor('Attributes (optional)')}
      {rows.map((row, i) => (
        <div key={i} className="flex items-center gap-2">
          <input
            type="text"
            value={row.key}
            onChange={(e) => update(i, 'key', e.target.value)}
            placeholder="key"
            className="w-1/3 rounded-md border border-[var(--color-border)] bg-[var(--color-surface-0)] px-2 py-1.5 text-sm text-[var(--color-text-primary)] placeholder:text-[var(--color-text-muted)] focus-visible:border-[var(--color-focus)]"
          />
          <input
            type="text"
            value={row.value}
            onChange={(e) => update(i, 'value', e.target.value)}
            placeholder="value"
            className="flex-1 rounded-md border border-[var(--color-border)] bg-[var(--color-surface-0)] px-2 py-1.5 text-sm text-[var(--color-text-primary)] placeholder:text-[var(--color-text-muted)] focus-visible:border-[var(--color-focus)]"
          />
          <button
            type="button"
            onClick={() => remove(i)}
            aria-label={`Remove attribute row ${i + 1}`}
            className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
          >
            <Trash2 size={14} />
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={() => onChange([...rows, { key: '', value: '' }])}
        className="flex w-fit items-center gap-1 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)]"
      >
        <Plus size={12} /> Add attribute
      </button>
    </div>
  )
}

function attrsToRecord(rows: { key: string; value: string }[]): Record<string, string> {
  const record: Record<string, string> = {}
  for (const { key, value } of rows) {
    if (key.trim()) record[key.trim()] = value
  }
  return record
}

function TextField({
  label,
  ...props
}: { label: string } & React.InputHTMLAttributes<HTMLInputElement>) {
  const id = `field-${label.toLowerCase().replace(/\s+/g, '-')}`
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm text-[var(--color-text-muted)]">
        {label}
      </label>
      <input
        id={id}
        type="text"
        {...props}
        className="rounded-md border border-[var(--color-border)] bg-[var(--color-surface-0)] px-3 py-1.5 text-sm text-[var(--color-text-primary)] placeholder:text-[var(--color-text-muted)] focus-visible:border-[var(--color-focus)]"
      />
    </div>
  )
}

function EntityForm({ onCreated }: { onCreated: (id: string) => void }) {
  const [classes, setClasses] = useState<ClassDef[]>([])
  const [entityClass, setEntityClass] = useState('PERSON')
  const [entitySubclass, setEntitySubclass] = useState('')
  const [entityId, setEntityId] = useState(`${ID_PREFIX.PERSON}-${randomSuffix()}`)
  const [label, setLabel] = useState('')
  const [status, setStatus] = useState('active')
  const [confidence, setConfidence] = useState('C3')
  const [sourceRef, setSourceRef] = useState('')
  const [aliases, setAliases] = useState('')
  const [attrRows, setAttrRows] = useState<{ key: string; value: string }[]>([])
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    api.getClasses().then(setClasses).catch(() => setClasses([]))
  }, [])

  useEffect(() => {
    setEntityId(`${ID_PREFIX[entityClass] ?? 'E'}-${randomSuffix()}`)
    setEntitySubclass('')
  }, [entityClass])

  const subclassOptions = classes
    .filter((c) => c.key === entityClass || c.key.startsWith(`${entityClass}.`))
    .map((c) => ({ value: c.key, label: c.key }))

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setSubmitting(true)
    try {
      const created = await api.createEntity({
        entity_id: entityId.trim(),
        entity_class: entityClass,
        entity_subclass: entitySubclass,
        label: label.trim(),
        aliases: aliases.split(',').map((a) => a.trim()).filter(Boolean),
        status: status as 'active' | 'inactive' | 'destroyed' | 'unknown',
        confidence: confidence.trim(),
        source_ref: sourceRef.trim(),
        attrs: attrsToRecord(attrRows),
      })
      onCreated(created.entity_id)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to reach the backend')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-3">
      <div className="grid grid-cols-2 gap-3">
        <div className="flex flex-col gap-1.5">
          {labelFor('Entity class')}
          <Select
            value={entityClass}
            onValueChange={setEntityClass}
            options={ROOT_CLASSES.map((c) => ({ value: c, label: c }))}
            aria-label="Entity class"
          />
        </div>
        <TextField label="Entity ID" value={entityId} onChange={(e) => setEntityId(e.target.value)} required />
      </div>

      <div className="flex flex-col gap-1.5">
        {labelFor('Entity subclass')}
        <Select
          value={entitySubclass}
          onValueChange={setEntitySubclass}
          options={subclassOptions}
          placeholder={subclassOptions.length ? 'Choose a subclass…' : 'Loading…'}
          aria-label="Entity subclass"
        />
      </div>

      <TextField label="Label" value={label} onChange={(e) => setLabel(e.target.value)} required />

      <div className="grid grid-cols-3 gap-3">
        <div className="flex flex-col gap-1.5">
          {labelFor('Status')}
          <Select
            value={status}
            onValueChange={setStatus}
            options={['active', 'inactive', 'destroyed', 'unknown'].map((s) => ({
              value: s,
              label: s,
            }))}
            aria-label="Status"
          />
        </div>
        <TextField
          label="Confidence"
          value={confidence}
          onChange={(e) => setConfidence(e.target.value)}
          placeholder="e.g. B2"
          required
        />
        <TextField
          label="Source ref"
          value={sourceRef}
          onChange={(e) => setSourceRef(e.target.value)}
          placeholder="e.g. D-1001"
          required
        />
      </div>

      <TextField
        label="Aliases (comma-separated, optional)"
        value={aliases}
        onChange={(e) => setAliases(e.target.value)}
      />

      <AttrsEditor rows={attrRows} onChange={setAttrRows} />

      {error && (
        <p role="alert" className="text-sm text-[var(--color-status-destroyed)]">
          {error}
        </p>
      )}

      <Button
        type="submit"
        variant="primary"
        disabled={submitting || !entitySubclass || !label.trim() || !confidence.trim() || !sourceRef.trim()}
      >
        {submitting ? 'Creating…' : 'Create entity'}
      </Button>
    </form>
  )
}

function LinkForm({ onCreated }: { onCreated: (id: string) => void }) {
  const [linkDefs, setLinkDefs] = useState<LinkDef[]>([])
  const [linkId, setLinkId] = useState(`L-${randomSuffix()}`)
  const [linkType, setLinkType] = useState('')
  const [sourceEntity, setSourceEntity] = useState('')
  const [targetEntity, setTargetEntity] = useState('')
  const [direction, setDirection] = useState('directed')
  const [assertionStatus, setAssertionStatus] = useState('reported')
  const [confidence, setConfidence] = useState('C3')
  const [sourceRef, setSourceRef] = useState('')
  const [attrRows, setAttrRows] = useState<{ key: string; value: string }[]>([])
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    api.getLinkDefs().then(setLinkDefs).catch(() => setLinkDefs([]))
  }, [])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setSubmitting(true)
    try {
      const created = await api.createLink({
        link_id: linkId.trim(),
        link_type: linkType,
        source_entity: sourceEntity.trim(),
        target_entity: targetEntity.trim(),
        direction: direction as 'directed' | 'symmetric',
        assertion_status: assertionStatus as 'reported' | 'assessed' | 'confirmed' | 'disputed',
        confidence: confidence.trim(),
        source_ref: sourceRef.trim(),
        attrs: attrsToRecord(attrRows),
      })
      onCreated(created.source_entity)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to reach the backend')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-3">
      <div className="grid grid-cols-2 gap-3">
        <div className="flex flex-col gap-1.5">
          {labelFor('Link type')}
          <Select
            value={linkType}
            onValueChange={setLinkType}
            options={linkDefs.map((l) => ({ value: l.type, label: l.type }))}
            placeholder={linkDefs.length ? 'Choose a link type…' : 'Loading…'}
            aria-label="Link type"
          />
        </div>
        <TextField label="Link ID" value={linkId} onChange={(e) => setLinkId(e.target.value)} required />
      </div>

      <div className="grid grid-cols-2 gap-3">
        <TextField
          label="Source entity ID"
          value={sourceEntity}
          onChange={(e) => setSourceEntity(e.target.value)}
          placeholder="e.g. P-1042"
          required
        />
        <TextField
          label="Target entity ID"
          value={targetEntity}
          onChange={(e) => setTargetEntity(e.target.value)}
          placeholder="e.g. O-233"
          required
        />
      </div>

      <div className="grid grid-cols-3 gap-3">
        <div className="flex flex-col gap-1.5">
          {labelFor('Direction')}
          <Select
            value={direction}
            onValueChange={setDirection}
            options={[
              { value: 'directed', label: 'directed' },
              { value: 'symmetric', label: 'symmetric' },
            ]}
            aria-label="Direction"
          />
        </div>
        <div className="flex flex-col gap-1.5">
          {labelFor('Assertion status')}
          <Select
            value={assertionStatus}
            onValueChange={setAssertionStatus}
            options={['reported', 'assessed', 'confirmed', 'disputed'].map((s) => ({
              value: s,
              label: s,
            }))}
            aria-label="Assertion status"
          />
        </div>
        <TextField
          label="Confidence"
          value={confidence}
          onChange={(e) => setConfidence(e.target.value)}
          placeholder="e.g. B2"
          required
        />
      </div>

      <TextField
        label="Source ref"
        value={sourceRef}
        onChange={(e) => setSourceRef(e.target.value)}
        placeholder="e.g. D-1001"
        required
      />

      <AttrsEditor rows={attrRows} onChange={setAttrRows} />

      {error && (
        <p role="alert" className="text-sm text-[var(--color-status-destroyed)]">
          {error}
        </p>
      )}

      <Button
        type="submit"
        variant="primary"
        disabled={
          submitting ||
          !linkType ||
          !sourceEntity.trim() ||
          !targetEntity.trim() ||
          !confidence.trim() ||
          !sourceRef.trim()
        }
      >
        {submitting ? 'Creating…' : 'Create link'}
      </Button>
    </form>
  )
}

/** Manual entity/link/attribute creation — the direct-entry counterpart to
 * the LLM extraction agent. Everything here goes through the same
 * POST /entities and POST /links validation as an ingest commit, so a
 * manually added resource can never bypass the ontology either. */
export function AddResourceDialog({ open, onOpenChange, onCreated }: AddResourceDialogProps) {
  function handleCreated(entityId: string) {
    onCreated(entityId)
    onOpenChange(false)
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/60" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 max-h-[85vh] w-[560px] max-w-[92vw] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-1)] p-5 shadow-2xl outline-none">
          <div className="mb-4 flex items-center justify-between">
            <Dialog.Title className="text-sm font-semibold text-[var(--color-text-primary)]">
              Add to graph
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

          <Tabs.Root defaultValue="entity">
            <Tabs.List className="mb-4 flex gap-1 border-b border-[var(--color-border)]" aria-label="Resource type">
              <Tabs.Trigger
                value="entity"
                className="border-b-2 border-transparent px-3 py-2 text-sm text-[var(--color-text-muted)] data-[state=active]:border-[var(--color-focus)] data-[state=active]:text-[var(--color-text-primary)]"
              >
                Entity
              </Tabs.Trigger>
              <Tabs.Trigger
                value="link"
                className="border-b-2 border-transparent px-3 py-2 text-sm text-[var(--color-text-muted)] data-[state=active]:border-[var(--color-focus)] data-[state=active]:text-[var(--color-text-primary)]"
              >
                Link
              </Tabs.Trigger>
            </Tabs.List>
            <Tabs.Content value="entity">
              <EntityForm onCreated={handleCreated} />
            </Tabs.Content>
            <Tabs.Content value="link">
              <LinkForm onCreated={handleCreated} />
            </Tabs.Content>
          </Tabs.Root>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
