import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { buildTree } from '../../lib/taxonomyTree'
import type { ClassDef } from '../../lib/types'
import { TreeRow, type TreeCtx } from './TaxonomyTree'

afterEach(cleanup)

const c = (key: string): ClassDef => ({
  key,
  parent_key: key.includes('.') ? key.slice(0, key.lastIndexOf('.')) : null,
  level: key.split('.').length,
  label: key,
  notes: null,
})
const classes = [c('A'), c('A.X'), c('B'), c('B.Y')]

function setup(over: Partial<TreeCtx> = {}) {
  const byKey = new Map(classes.map((k) => [k.key, k]))
  const ctx: TreeCtx = {
    expanded: new Set(['A', 'B']),
    forceOpen: false,
    selectedKey: null,
    byKey,
    keys: new Set(byKey.keys()),
    childCounts: new Map([['A', 1], ['B', 1]]),
    counts: {},
    addingKey: null,
    dragKey: null,
    dropKey: null,
    busy: false,
    onToggle: vi.fn(),
    onSelect: vi.fn(),
    onEdit: vi.fn(),
    onDelete: vi.fn(),
    onAddOpen: vi.fn(),
    onAdd: vi.fn(async () => true),
    onDragKey: vi.fn(),
    onDropKey: vi.fn(),
    onMove: vi.fn(),
    ...over,
  }
  const tree = buildTree(classes, {})
  render(
    <div role="tree">
      {tree.map((n) => (
        <TreeRow key={n.cls.key} node={n} depth={0} ctx={ctx} />
      ))}
    </div>,
  )
  return ctx
}

describe('TreeRow actions', () => {
  it('offers edit, add and delete icons on nodes and leaves', () => {
    const ctx = setup()
    for (const k of ['A', 'A.X']) {
      expect(screen.getByLabelText(`Edit ${k}`)).toBeTruthy()
      expect(screen.getByLabelText(`Add subclass to ${k}`)).toBeTruthy()
    }
    fireEvent.click(screen.getByLabelText('Edit A.X'))
    expect(ctx.onEdit).toHaveBeenCalledWith('A.X')
    fireEvent.click(screen.getByLabelText('Add subclass to A'))
    expect(ctx.onAddOpen).toHaveBeenCalledWith('A')
    fireEvent.click(screen.getByLabelText('Delete A.X'))
    expect(ctx.onDelete).toHaveBeenCalledOnce()
  })

  it('disables delete for roots and classes with children', () => {
    setup()
    expect((screen.getByLabelText('Delete A') as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByLabelText('Delete A.X') as HTMLButtonElement).disabled).toBe(false)
  })

  it('moves a dragged class when dropped on another class', () => {
    const ctx = setup({ dragKey: 'A.X' })
    const target = screen.getAllByRole('treeitem').find((el) => el.textContent?.startsWith('B') && !el.textContent.startsWith('B.'))!
    fireEvent.dragOver(target, { dataTransfer: { dropEffect: '' } })
    fireEvent.drop(target)
    expect(ctx.onMove).toHaveBeenCalledWith('A.X', 'B')
  })

  it('ignores a drop onto its own parent or descendant', () => {
    const ctx = setup({ dragKey: 'A.X' })
    const parent = screen.getAllByRole('treeitem').find((el) => el.getAttribute('aria-level') === '1' && el.textContent?.startsWith('A'))!
    fireEvent.drop(parent)
    expect(ctx.onMove).not.toHaveBeenCalled()
  })

  it('does not let a root be dragged', () => {
    setup()
    const root = screen.getAllByRole('treeitem')[0]
    expect(root.getAttribute('draggable')).toBe('false')
  })
})
