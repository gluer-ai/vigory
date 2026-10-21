import { ChevronDown, ChevronRight, GripVertical, Pencil, Plus, Trash2 } from 'lucide-react'
import { useState, type DragEvent } from 'react'
import { deleteBlocker, moveBlocker, toKeySegment, type TreeNode } from '../../lib/taxonomyTree'
import type { ClassDef } from '../../lib/types'
import { Button } from '../ui/Button'

export const inputClass =
  'w-full rounded-md border border-[var(--color-border)] bg-[var(--color-surface-0)] px-2 py-1.5 text-sm text-[var(--color-text-primary)]'

/** Everything a row needs from the page: state to render and actions to call. */
export interface TreeCtx {
  expanded: Set<string>
  forceOpen: boolean
  selectedKey: string | null
  /** Every class, to validate drops and new names (unaffected by search filtering). */
  byKey: Map<string, ClassDef>
  keys: Set<string>
  childCounts: Map<string, number>
  counts: Record<string, number>
  /** Class whose inline "add subclass" form is open. */
  addingKey: string | null
  /** Class currently being dragged / hovered as a drop target. */
  dragKey: string | null
  dropKey: string | null
  busy: boolean
  onToggle: (key: string) => void
  onSelect: (key: string) => void
  onEdit: (key: string) => void
  onDelete: (node: TreeNode) => void
  onAddOpen: (key: string | null) => void
  onAdd: (parentKey: string, name: string, label: string) => Promise<boolean>
  onDragKey: (key: string | null) => void
  onDropKey: (key: string | null) => void
  onMove: (key: string, parentKey: string) => void
}

const iconButton =
  'rounded p-1 pointer-coarse:p-2 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text-primary)] disabled:pointer-events-none disabled:opacity-30'

export function TreeRow({ node, depth, ctx }: { node: TreeNode; depth: number; ctx: TreeCtx }) {
  const { cls } = node
  const hasChildren = node.children.length > 0
  const open = hasChildren && (ctx.forceOpen || ctx.expanded.has(cls.key))
  const isSelected = ctx.selectedKey === cls.key
  const isRoot = cls.parent_key === null

  // Child counts come from the full class list: a search can prune node.children.
  const delBlocked = deleteBlocker(cls, ctx.childCounts.get(cls.key) ?? 0, ctx.counts[cls.key] ?? 0)

  const dragged = ctx.dragKey
  const dragCls = dragged ? ctx.byKey.get(dragged) : undefined
  const canDrop = dragCls !== undefined && moveBlocker(dragCls, cls.key, ctx.keys) === null
  const isDropTarget = canDrop && ctx.dropKey === cls.key

  function onDragStart(e: DragEvent) {
    e.dataTransfer.effectAllowed = 'move'
    e.dataTransfer.setData('text/plain', cls.key)
    ctx.onDragKey(cls.key)
  }
  function onDragOver(e: DragEvent) {
    if (!canDrop) return
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
    if (ctx.dropKey !== cls.key) ctx.onDropKey(cls.key)
  }
  function onDrop(e: DragEvent) {
    if (!canDrop || !dragged) return
    e.preventDefault()
    ctx.onDropKey(null)
    ctx.onDragKey(null)
    ctx.onMove(dragged, cls.key)
  }

  return (
    <div role="none">
      <div
        role="treeitem"
        aria-level={depth + 1}
        aria-selected={isSelected}
        aria-expanded={hasChildren ? open : undefined}
        draggable={!isRoot && !ctx.busy}
        onDragStart={onDragStart}
        onDragEnd={() => {
          ctx.onDragKey(null)
          ctx.onDropKey(null)
        }}
        onDragOver={onDragOver}
        onDragLeave={() => {
          if (ctx.dropKey === cls.key) ctx.onDropKey(null)
        }}
        onDrop={onDrop}
        className={`group flex items-center gap-1 rounded-md pr-2 ${
          isDropTarget
            ? 'bg-[var(--color-surface-hover)] outline outline-1 outline-[var(--color-focus)]'
            : isSelected
              ? 'bg-[var(--color-surface-hover)]'
              : 'hover:bg-[var(--color-surface-2)]'
        } ${ctx.dragKey === cls.key ? 'opacity-50' : ''}`}
        style={{ paddingLeft: depth * 16 + 4 }}
      >
        {hasChildren ? (
          <button
            type="button"
            aria-label={`${open ? 'Collapse' : 'Expand'} ${cls.label}`}
            onClick={() => ctx.onToggle(cls.key)}
            className="rounded p-1 text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)]"
          >
            {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          </button>
        ) : (
          <span className="inline-block w-[22px]" aria-hidden="true" />
        )}
        <button
          type="button"
          onClick={() => ctx.onSelect(cls.key)}
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
        {/* Hover-revealed on pointer devices; always visible on touch and for the
            selected row. Still focusable by keyboard while hidden. */}
        <div
          className={`flex shrink-0 items-center ${
            isSelected ? '' : 'opacity-0 focus-within:opacity-100 group-hover:opacity-100 pointer-coarse:opacity-100'
          }`}
        >
          <button
            type="button"
            aria-label={`Add subclass to ${cls.label}`}
            title="Add subclass"
            onClick={() => ctx.onAddOpen(ctx.addingKey === cls.key ? null : cls.key)}
            className={iconButton}
          >
            <Plus size={14} />
          </button>
          <button
            type="button"
            aria-label={`Edit ${cls.label}`}
            title="Edit"
            onClick={() => ctx.onEdit(cls.key)}
            className={iconButton}
          >
            <Pencil size={14} />
          </button>
          <button
            type="button"
            aria-label={`Delete ${cls.label}`}
            title={delBlocked ?? 'Delete'}
            disabled={delBlocked !== null || ctx.busy}
            onClick={() => ctx.onDelete(node)}
            className={iconButton}
          >
            <Trash2 size={14} />
          </button>
          {isRoot ? (
            // Roots are fixed (see the backend), so no grip; keep the space so
            // the counts line up with the other rows.
            <span aria-hidden="true" className="inline-block w-[22px] pointer-coarse:w-[30px]" />
          ) : (
            <span
              aria-hidden="true"
              title="Drag onto another class to move"
              className="cursor-grab p-1 pointer-coarse:p-2 text-[var(--color-text-muted)]"
            >
              <GripVertical size={14} />
            </span>
          )}
        </div>
      </div>
      {ctx.addingKey === cls.key && (
        <InlineAdd parent={cls.key} depth={depth + 1} ctx={ctx} />
      )}
      {open && (
        <div role="group">
          {node.children.map((child) => (
            <TreeRow key={child.cls.key} node={child} depth={depth + 1} ctx={ctx} />
          ))}
        </div>
      )}
    </div>
  )
}

function InlineAdd({ parent, depth, ctx }: { parent: string; depth: number; ctx: TreeCtx }) {
  const [name, setName] = useState('')
  const [label, setLabel] = useState('')
  const segment = toKeySegment(name)
  const newKey = segment ? `${parent}.${segment}` : null
  const taken = newKey !== null && ctx.keys.has(newKey)
  const canAdd = newKey !== null && !taken && label.trim() !== '' && !ctx.busy

  async function submit() {
    if (!canAdd) return
    await ctx.onAdd(parent, name, label.trim())
  }

  return (
    <form
      aria-label={`Add subclass to ${parent}`}
      className="my-1 flex flex-col gap-2 rounded-md border border-[var(--color-border)] bg-[var(--color-surface-1)] p-2"
      style={{ marginLeft: depth * 16 + 4 }}
      onSubmit={(e) => {
        e.preventDefault()
        void submit()
      }}
      onKeyDown={(e) => {
        if (e.key === 'Escape') ctx.onAddOpen(null)
      }}
    >
      <input
        autoFocus
        aria-label="New subclass name"
        placeholder="Name, e.g. Law firm"
        className={inputClass}
        value={name}
        onChange={(e) => setName(e.target.value)}
      />
      <input
        aria-label="New subclass label"
        placeholder="Display label"
        className={inputClass}
        value={label}
        onChange={(e) => setLabel(e.target.value)}
      />
      {name.trim() !== '' && (
        <p className="break-all font-mono text-xs text-[var(--color-text-muted)]">
          {newKey ?? 'Use letters, digits and spaces; start with a letter.'}
          {taken ? ' — already exists' : ''}
        </p>
      )}
      <div className="flex gap-2">
        <Button type="submit" variant="primary" disabled={!canAdd}>
          Add
        </Button>
        <Button type="button" onClick={() => ctx.onAddOpen(null)}>
          Cancel
        </Button>
      </div>
    </form>
  )
}
