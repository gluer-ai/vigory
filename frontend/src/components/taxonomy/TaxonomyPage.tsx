import { ChevronDown, ChevronRight } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, ApiError } from '../../lib/api'
import { ancestorKeys, buildTree, filterTree, toKeySegment, type TreeNode } from '../../lib/taxonomyTree'
import type { ClassDef } from '../../lib/types'
import { Button } from '../ui/Button'

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.message : 'Failed to reach the backend'
}

const inputClass =
  'w-full rounded-md border border-[var(--color-border)] bg-[var(--color-surface-0)] px-2 py-1.5 text-sm text-[var(--color-text-primary)]'

/** Taxonomy editor: the class ontology as a collapsible tree, with edit,
 * add-child, merge and (guarded) delete. Renaming/moving is not offered — keys
 * are paths that entities reference. */
export function TaxonomyPage() {
  const [classes, setClasses] = useState<ClassDef[]>([])
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [status, setStatus] = useState<'loading' | 'error' | 'ready'>('loading')
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [onlyAi, setOnlyAi] = useState(false)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [selectedKey, setSelectedKey] = useState<string | null>(null)
  // Success notice lives here, not in ClassPanel: add/merge/delete re-select a
  // different class, which remounts the panel and would drop a local message.
  const [notice, setNotice] = useState<string | null>(null)

  // Bumped to refetch after an edit or a Retry. Loading happens inside the
  // effect (cancel-flag pattern, as in FeedsPage) so no state is set
  // synchronously; the tree stays visible while a refetch is in flight.
  const [reloadToken, setReloadToken] = useState(0)
  const load = useCallback(() => setReloadToken((n) => n + 1), [])

  useEffect(() => {
    let cancelled = false
    Promise.all([api.getClasses(), api.getClassCounts()])
      .then(([cls, cnt]) => {
        if (cancelled) return
        setClasses(cls)
        setCounts(cnt)
        setStatus('ready')
      })
      .catch((err) => {
        if (cancelled) return
        setError(errorText(err))
        setStatus('error')
      })
    return () => {
      cancelled = true
    }
  }, [reloadToken])

  const tree = useMemo(() => buildTree(classes, counts), [classes, counts])
  const term = search.trim().toLowerCase()
  const visible = useMemo(() => {
    if (!term && !onlyAi) return tree
    return filterTree(
      tree,
      (c) =>
        (!onlyAi || c.origin === 'extraction') &&
        (!term || c.key.toLowerCase().includes(term) || c.label.toLowerCase().includes(term)),
    )
  }, [tree, term, onlyAi])

  const filtering = term !== '' || onlyAi
  const byKey = useMemo(() => new Map(classes.map((c) => [c.key, c])), [classes])
  const selected = selectedKey ? (byKey.get(selectedKey) ?? null) : null
  const aiCount = classes.filter((c) => c.origin === 'extraction').length

  function toggle(key: string) {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  function reveal(key: string) {
    setExpanded((prev) => new Set([...prev, ...ancestorKeys(key), key]))
    setSelectedKey(key)
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* pl-16 below lg clears AppShell's floating menu button. */}
      <div className="border-b border-[var(--color-border)] py-4 pl-16 pr-6 lg:pl-6">
        <h1 className="text-base font-semibold text-[var(--color-text-primary)]">Taxonomy</h1>
        <p className="mt-0.5 text-sm text-[var(--color-text-muted)]">
          {classes.length} classes. Edit labels and notes, add subclasses, merge duplicates, or
          delete unused classes.
        </p>
      </div>

      {notice && (
        <p
          role="status"
          className="border-b border-[var(--color-border)] px-6 py-2 text-sm text-[var(--color-status-active)]"
        >
          {notice}
        </p>
      )}

      {status === 'loading' && (
        <p className="p-6 text-sm text-[var(--color-text-muted)]">Loading…</p>
      )}
      {status === 'error' && (
        <div className="p-6">
          <p role="alert" className="text-sm text-[var(--color-status-destroyed)]">
            {error}
          </p>
          <Button
            className="mt-3"
            onClick={() => {
              setStatus('loading')
              load()
            }}
          >
            Retry
          </Button>
        </div>
      )}

      {status === 'ready' && (
        <div className="flex flex-1 flex-col overflow-hidden md:flex-row">
          <section
            aria-label="Class tree"
            className="flex min-h-0 flex-1 flex-col border-b border-[var(--color-border)] md:border-b-0 md:border-r"
          >
            <div className="flex flex-wrap items-center gap-3 border-b border-[var(--color-border)] px-4 py-3">
              <input
                type="search"
                aria-label="Search classes"
                placeholder="Search by name or key…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className={`${inputClass} max-w-xs`}
              />
              <label className="flex items-center gap-2 text-sm text-[var(--color-text-muted)]">
                <input type="checkbox" checked={onlyAi} onChange={(e) => setOnlyAi(e.target.checked)} />
                AI-added only ({aiCount})
              </label>
            </div>
            <div role="tree" aria-label="Taxonomy" className="flex-1 overflow-y-auto p-2">
              {visible.length === 0 ? (
                <p className="p-4 text-sm text-[var(--color-text-muted)]">No classes match.</p>
              ) : (
                visible.map((n) => (
                  <TreeRow
                    key={n.cls.key}
                    node={n}
                    depth={0}
                    expanded={expanded}
                    forceOpen={filtering}
                    selectedKey={selectedKey}
                    onToggle={toggle}
                    onSelect={(key) => {
                      setNotice(null)
                      setSelectedKey(key)
                    }}
                  />
                ))
              )}
            </div>
          </section>

          <aside
            aria-label="Class details"
            className="max-h-[50%] w-full shrink-0 overflow-y-auto md:max-h-none md:w-[380px]"
          >
            {selected ? (
              <ClassPanel
                key={selected.key}
                cls={selected}
                classes={classes}
                counts={counts}
                onChanged={(keyToSelect, text) => {
                  setNotice(text)
                  load()
                  if (keyToSelect) reveal(keyToSelect)
                  else setSelectedKey(null)
                }}
              />
            ) : (
              <p className="p-6 text-sm text-[var(--color-text-muted)]">
                Select a class to see its details.
              </p>
            )}
          </aside>
        </div>
      )}
    </div>
  )
}

function TreeRow({
  node,
  depth,
  expanded,
  forceOpen,
  selectedKey,
  onToggle,
  onSelect,
}: {
  node: TreeNode
  depth: number
  expanded: Set<string>
  forceOpen: boolean
  selectedKey: string | null
  onToggle: (key: string) => void
  onSelect: (key: string) => void
}) {
  const { cls } = node
  const hasChildren = node.children.length > 0
  const open = hasChildren && (forceOpen || expanded.has(cls.key))
  const isSelected = selectedKey === cls.key
  return (
    <div role="none">
      <div
        role="treeitem"
        aria-level={depth + 1}
        aria-selected={isSelected}
        aria-expanded={hasChildren ? open : undefined}
        className={`flex items-center gap-1 rounded-md pr-2 ${
          isSelected ? 'bg-[var(--color-surface-hover)]' : 'hover:bg-[var(--color-surface-2)]'
        }`}
        style={{ paddingLeft: depth * 16 + 4 }}
      >
        {hasChildren ? (
          <button
            type="button"
            aria-label={`${open ? 'Collapse' : 'Expand'} ${cls.label}`}
            onClick={() => onToggle(cls.key)}
            className="rounded p-1 text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)]"
          >
            {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          </button>
        ) : (
          <span className="inline-block w-[22px]" aria-hidden="true" />
        )}
        <button
          type="button"
          onClick={() => onSelect(cls.key)}
          className="flex min-w-0 flex-1 items-center gap-2 py-1 text-left text-sm text-[var(--color-text-primary)]"
        >
          <span className="truncate">{cls.label}</span>
          {cls.origin === 'extraction' && (
            <span className="shrink-0 rounded border border-dashed border-[var(--color-border-strong)] px-1 text-xs text-[var(--color-text-muted)]">
              AI-added
            </span>
          )}
          {node.total > 0 && (
            <span className="ml-auto shrink-0 font-mono text-xs text-[var(--color-text-muted)]">
              {node.total}
            </span>
          )}
        </button>
      </div>
      {open && (
        <div role="group">
          {node.children.map((child) => (
            <TreeRow
              key={child.cls.key}
              node={child}
              depth={depth + 1}
              expanded={expanded}
              forceOpen={forceOpen}
              selectedKey={selectedKey}
              onToggle={onToggle}
              onSelect={onSelect}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function ClassPanel({
  cls,
  classes,
  counts,
  onChanged,
}: {
  cls: ClassDef
  classes: ClassDef[]
  counts: Record<string, number>
  /** Called after any change: the key to select next (null = none) and a success notice. */
  onChanged: (keyToSelect: string | null, notice: string) => void
}) {
  const [label, setLabel] = useState(cls.label)
  const [notes, setNotes] = useState(cls.notes ?? '')
  const [childName, setChildName] = useState('')
  const [childLabel, setChildLabel] = useState('')
  const [mergeInto, setMergeInto] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  const childCount = classes.filter((c) => c.parent_key === cls.key).length
  const own = counts[cls.key] ?? 0
  const isRoot = cls.parent_key === null
  const dirty = label.trim() !== cls.label || notes.trim() !== (cls.notes ?? '')
  const segment = toKeySegment(childName)
  const newKey = segment ? `${cls.key}.${segment}` : null
  const keyTaken = newKey !== null && classes.some((c) => c.key === newKey)
  const mergeTarget = classes.find((c) => c.key === mergeInto.trim())
  const mergeBlocked = isRoot
    ? 'Root classes cannot be merged.'
    : childCount > 0
      ? 'Merge or delete the child classes first.'
      : null
  const deleteBlocked = isRoot
    ? 'Root classes cannot be deleted.'
    : childCount > 0
      ? `Has ${childCount} child class(es).`
      : own > 0
        ? `${own} entities use this class — merge it into another class instead.`
        : null

  async function run(action: () => Promise<string | null>) {
    setBusy(true)
    setMessage(null)
    try {
      return await action()
    } catch (err) {
      setMessage(errorText(err))
      return undefined
    } finally {
      setBusy(false)
    }
  }

  async function save() {
    const changes = {
      label: label.trim(),
      notes: notes.trim() === '' ? null : notes.trim(),
    }
    const r = await run(async () => {
      await api.updateClass(cls.key, changes)
      return cls.key
    })
    if (r !== undefined) onChanged(r, 'Saved.')
  }

  async function addChild() {
    if (!newKey || keyTaken || !childLabel.trim()) return
    const r = await run(async () => {
      await api.createClass({ key: newKey, parent_key: cls.key, label: childLabel.trim() })
      return newKey
    })
    if (r !== undefined) onChanged(r, 'Subclass added.')
  }

  async function merge() {
    if (!mergeTarget) return
    if (!window.confirm(`Merge "${cls.key}" into "${mergeTarget.key}"? This cannot be undone.`)) return
    let moved = 0
    const r = await run(async () => {
      const res = await api.mergeClass(cls.key, mergeTarget.key)
      moved = res.entities_moved
      return mergeTarget.key
    })
    if (r) onChanged(r, `Merged. ${moved} entities moved.`)
  }

  async function remove() {
    if (!window.confirm(`Delete "${cls.key}"? This cannot be undone.`)) return
    const r = await run(async () => {
      await api.deleteClass(cls.key)
      return cls.parent_key
    })
    if (r !== undefined) onChanged(r, 'Deleted.')
  }

  return (
    <div className="flex flex-col gap-5 p-4">
      <div>
        <h2 className="text-sm font-semibold text-[var(--color-text-primary)]">{cls.label}</h2>
        <p className="mt-1 break-all font-mono text-xs text-[var(--color-text-muted)]">{cls.key}</p>
        <p className="mt-1 text-xs text-[var(--color-text-muted)]">
          Level {cls.level} · {own} entities here · {childCount} subclasses
          {cls.origin === 'extraction' ? ' · added by the AI' : ''}
        </p>
      </div>

      {message && (
        <p role="alert" className="text-sm text-[var(--color-status-destroyed)]">
          {message}
        </p>
      )}

      <section className="flex flex-col gap-2">
        <label htmlFor="tx-label" className="text-sm text-[var(--color-text-muted)]">
          Label
        </label>
        <input id="tx-label" className={inputClass} value={label} onChange={(e) => setLabel(e.target.value)} />
        <label htmlFor="tx-notes" className="text-sm text-[var(--color-text-muted)]">
          Notes
        </label>
        <textarea
          id="tx-notes"
          rows={3}
          className={inputClass}
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
        />
        <Button variant="primary" disabled={busy || !dirty || label.trim() === ''} onClick={save}>
          Save changes
        </Button>
      </section>

      <section className="flex flex-col gap-2 border-t border-[var(--color-border)] pt-4">
        <h3 className="text-sm font-medium text-[var(--color-text-primary)]">Add subclass</h3>
        <input
          aria-label="New subclass name"
          placeholder="Name, e.g. Law firm"
          className={inputClass}
          value={childName}
          onChange={(e) => setChildName(e.target.value)}
        />
        <input
          aria-label="New subclass label"
          placeholder="Display label"
          className={inputClass}
          value={childLabel}
          onChange={(e) => setChildLabel(e.target.value)}
        />
        {childName.trim() !== '' && (
          <p className="break-all font-mono text-xs text-[var(--color-text-muted)]">
            {newKey ?? 'Use letters, digits and spaces; start with a letter.'}
            {keyTaken ? ' — already exists' : ''}
          </p>
        )}
        <Button disabled={busy || !newKey || keyTaken || !childLabel.trim()} onClick={addChild}>
          Add subclass
        </Button>
      </section>

      <section className="flex flex-col gap-2 border-t border-[var(--color-border)] pt-4">
        <h3 className="text-sm font-medium text-[var(--color-text-primary)]">Merge into another class</h3>
        <p className="text-xs text-[var(--color-text-muted)]">
          Moves this class's entities to the target, then removes this class.
        </p>
        <input
          aria-label="Merge target key"
          list="tx-merge-targets"
          placeholder="Type to find the target class…"
          className={inputClass}
          value={mergeInto}
          disabled={mergeBlocked !== null}
          onChange={(e) => setMergeInto(e.target.value)}
        />
        <datalist id="tx-merge-targets">
          {classes
            .filter((c) => c.key !== cls.key && !c.key.startsWith(`${cls.key}.`))
            .map((c) => (
              <option key={c.key} value={c.key}>
                {c.label}
              </option>
            ))}
        </datalist>
        {mergeBlocked && <p className="text-xs text-[var(--color-text-muted)]">{mergeBlocked}</p>}
        <Button disabled={busy || mergeBlocked !== null || !mergeTarget} onClick={merge}>
          Merge…
        </Button>
      </section>

      <section className="flex flex-col gap-2 border-t border-[var(--color-border)] pt-4">
        <h3 className="text-sm font-medium text-[var(--color-text-primary)]">Delete</h3>
        {deleteBlocked && <p className="text-xs text-[var(--color-text-muted)]">{deleteBlocked}</p>}
        <Button disabled={busy || deleteBlocked !== null} onClick={remove}>
          Delete class…
        </Button>
      </section>
    </div>
  )
}
