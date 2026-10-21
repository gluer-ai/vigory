import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, ApiError } from '../../lib/api'
import {
  ancestorKeys,
  buildTree,
  deleteBlocker,
  filterTree,
  moveBlocker,
  movedKey,
  toKeySegment,
} from '../../lib/taxonomyTree'
import type { ClassDef } from '../../lib/types'
import { Button } from '../ui/Button'
import { inputClass, TreeRow, type TreeCtx } from './TaxonomyTree'

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.message : 'Failed to reach the backend'
}

/** Taxonomy editor: the class ontology as a collapsible tree, with edit, add,
 * (guarded) delete and merge, and drag-and-drop re-parenting. Moving a class
 * rewrites its key (a path that entities reference) server-side. */
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
  const [actionError, setActionError] = useState<string | null>(null)
  const [addingKey, setAddingKey] = useState<string | null>(null)
  const [dragKey, setDragKey] = useState<string | null>(null)
  const [dropKey, setDropKey] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // Edit icon: which class's label field to focus (n makes repeat clicks distinct).
  const [editRequest, setEditRequest] = useState<{ key: string; n: number } | null>(null)

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
  const keys = useMemo(() => new Set(byKey.keys()), [byKey])
  const childCounts = useMemo(() => {
    const m = new Map<string, number>()
    for (const c of classes) if (c.parent_key) m.set(c.parent_key, (m.get(c.parent_key) ?? 0) + 1)
    return m
  }, [classes])

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

  function select(key: string) {
    setNotice(null)
    setActionError(null)
    setEditRequest(null)
    setSelectedKey(key)
  }

  /** Run a tree-level action, surfacing a failure in the page banner. */
  async function act(action: () => Promise<void>) {
    setBusy(true)
    setNotice(null)
    setActionError(null)
    try {
      await action()
    } catch (err) {
      setActionError(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  /** Re-parent `key` under `parentKey` after confirming; the key (and every
   * key beneath it) changes, so the selection is remapped to follow it. */
  async function moveClass(key: string, parentKey: string) {
    const cls = byKey.get(key)
    if (!cls || moveBlocker(cls, parentKey, keys) !== null) return
    const newKey = movedKey(key, parentKey)
    const affected = classes.filter((c) => c.key === key || c.key.startsWith(`${key}.`))
    const entities = affected.reduce((sum, c) => sum + (counts[c.key] ?? 0), 0)
    const detail =
      `Its key becomes "${newKey}"` +
      (affected.length > 1 ? `, along with ${affected.length - 1} subclass(es)` : '') +
      (entities > 0 ? `, and ${entities} entities are re-pointed` : '') +
      '.'
    if (!window.confirm(`Move "${key}" under "${parentKey}"? ${detail}`)) return
    await act(async () => {
      await api.moveClass(key, parentKey)
      setNotice(`Moved to ${newKey}.`)
      load()
      const followed =
        selectedKey === key || selectedKey?.startsWith(`${key}.`)
          ? newKey + (selectedKey as string).slice(key.length)
          : (selectedKey ?? newKey)
      reveal(followed)
      setExpanded((prev) => new Set([...prev, parentKey, ...ancestorKeys(newKey)]))
    })
  }

  const treeCtx: TreeCtx = {
    expanded,
    forceOpen: filtering,
    selectedKey,
    byKey,
    keys,
    childCounts,
    counts,
    addingKey,
    dragKey,
    dropKey,
    busy,
    onToggle: toggle,
    onSelect: select,
    onEdit: (key) => {
      select(key)
      setEditRequest({ key, n: Date.now() })
    },
    onAddOpen: setAddingKey,
    onAdd: async (parentKey, name, label) => {
      const segment = toKeySegment(name)
      if (!segment) return false
      const newKey = `${parentKey}.${segment}`
      let ok = false
      await act(async () => {
        await api.createClass({ key: newKey, parent_key: parentKey, label })
        ok = true
        setAddingKey(null)
        setNotice('Subclass added.')
        load()
        reveal(newKey)
      })
      return ok
    },
    onDelete: (node) => {
      const { cls } = node
      if (!window.confirm(`Delete "${cls.key}"? This cannot be undone.`)) return
      void act(async () => {
        await api.deleteClass(cls.key)
        setNotice('Deleted.')
        load()
        if (selectedKey === cls.key) setSelectedKey(cls.parent_key)
      })
    },
    onDragKey: setDragKey,
    onDropKey: setDropKey,
    onMove: (key, parentKey) => void moveClass(key, parentKey),
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* pl-16 below lg clears AppShell's floating menu button. */}
      <div className="border-b border-[var(--color-border)] py-4 pl-16 pr-6 lg:pl-6">
        <h1 className="text-base font-semibold text-[var(--color-text-primary)]">Taxonomy</h1>
        <p className="mt-0.5 text-sm text-[var(--color-text-muted)]">
          {classes.length} classes. Add, edit or delete classes from each row, and drag a class onto
          another to move it.
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

      {actionError && (
        <p
          role="alert"
          className="border-b border-[var(--color-border)] px-6 py-2 text-sm text-[var(--color-status-destroyed)]"
        >
          {actionError}
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
                visible.map((n) => <TreeRow key={n.cls.key} node={n} depth={0} ctx={treeCtx} />)
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
                focusLabel={editRequest?.key === selected.key ? editRequest.n : 0}
                onMove={(parentKey) => void moveClass(selected.key, parentKey)}
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

function ClassPanel({
  cls,
  classes,
  counts,
  focusLabel,
  onMove,
  onChanged,
}: {
  cls: ClassDef
  classes: ClassDef[]
  counts: Record<string, number>
  /** Non-zero when the row's edit icon asked for the label field to be focused. */
  focusLabel: number
  onMove: (parentKey: string) => void
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
  const [moveTo, setMoveTo] = useState('')
  const labelRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (focusLabel) labelRef.current?.focus()
  }, [focusLabel])

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
  const deleteBlocked = deleteBlocker(cls, childCount, own)
  const keys = new Set(classes.map((c) => c.key))
  const moveTarget = classes.find((c) => c.key === moveTo.trim())
  const moveBlocked = isRoot
    ? 'Root classes cannot be moved.'
    : moveTarget
      ? moveBlocker(cls, moveTarget.key, keys)
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
        <input id="tx-label" ref={labelRef} className={inputClass} value={label} onChange={(e) => setLabel(e.target.value)} />
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
        <h3 className="text-sm font-medium text-[var(--color-text-primary)]">Move under another class</h3>
        <p className="text-xs text-[var(--color-text-muted)]">
          Same as dragging in the tree. The class and its subclasses get new keys.
        </p>
        <input
          aria-label="Move target key"
          list="tx-move-targets"
          placeholder="Type to find the new parent…"
          className={inputClass}
          value={moveTo}
          disabled={isRoot}
          onChange={(e) => setMoveTo(e.target.value)}
        />
        <datalist id="tx-move-targets">
          {classes
            .filter((c) => c.key !== cls.key && !c.key.startsWith(`${cls.key}.`) && c.key !== cls.parent_key)
            .map((c) => (
              <option key={c.key} value={c.key}>
                {c.label}
              </option>
            ))}
        </datalist>
        {(isRoot || moveBlocked) && (
          <p className="text-xs text-[var(--color-text-muted)]">
            {isRoot ? 'Root classes cannot be moved.' : moveBlocked}
          </p>
        )}
        <Button
          disabled={busy || isRoot || !moveTarget || moveBlocked !== null}
          onClick={() => moveTarget && onMove(moveTarget.key)}
        >
          Move…
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
