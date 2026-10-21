import { describe, expect, it } from 'vitest'
import {
  ancestorKeys,
  buildTree,
  deleteBlocker,
  filterTree,
  moveBlocker,
  movedKey,
  toKeySegment,
} from './taxonomyTree'
import type { ClassDef } from './types'

const c = (key: string, label = key): ClassDef => ({
  key,
  parent_key: key.includes('.') ? key.slice(0, key.lastIndexOf('.')) : null,
  level: key.split('.').length,
  label,
  notes: null,
})

const classes = [c('B'), c('A.Y'), c('A'), c('A.X'), c('A.X.Z')]

describe('buildTree', () => {
  it('nests by parent_key, sorted by key, and rolls counts up', () => {
    const tree = buildTree(classes, { 'A.X.Z': 3, 'A.Y': 2, A: 1 })
    expect(tree.map((n) => n.cls.key)).toEqual(['A', 'B'])
    expect(tree[0].children.map((n) => n.cls.key)).toEqual(['A.X', 'A.Y'])
    expect(tree[0].own).toBe(1)
    expect(tree[0].total).toBe(6)
    expect(tree[0].children[0].total).toBe(3)
    expect(tree[1].total).toBe(0)
  })

  it('keeps a class whose parent is missing as a root', () => {
    const tree = buildTree([c('A.X.Z')], {})
    expect(tree.map((n) => n.cls.key)).toEqual(['A.X.Z'])
  })
})

describe('filterTree', () => {
  it('keeps matches with their ancestors and drops the rest', () => {
    const tree = buildTree(classes, {})
    const out = filterTree(tree, (k) => k.key === 'A.X.Z')
    expect(out.map((n) => n.cls.key)).toEqual(['A'])
    expect(out[0].children.map((n) => n.cls.key)).toEqual(['A.X'])
    expect(out[0].children[0].children.map((n) => n.cls.key)).toEqual(['A.X.Z'])
  })
})

describe('ancestorKeys', () => {
  it('lists every key above the given one', () => {
    expect(ancestorKeys('A.X.Z')).toEqual(['A', 'A.X'])
    expect(ancestorKeys('A')).toEqual([])
  })
})

describe('toKeySegment', () => {
  it.each([
    ['Law firm', 'LAW_FIRM'],
    ['bank-run', 'BANK_RUN'],
    ['  CIA ', 'CIA'],
    ['', null],
    ['a.b', null],
    ['1abc', null],
    ['drop;table', null],
  ])('%s -> %s', (input, expected) => {
    expect(toKeySegment(input)).toBe(expected)
  })
})

describe('movedKey', () => {
  it('keeps the last segment under the new parent', () => {
    expect(movedKey('A.X.Z', 'B')).toBe('B.Z')
  })
})

describe('moveBlocker', () => {
  const keys = new Set([...classes.map((k) => k.key), 'B.Z'])
  const byKey = (k: string) => classes.find((x) => x.key === k)!

  it('allows a move to a different parent', () => {
    expect(moveBlocker(byKey('A.Y'), 'B', keys)).toBeNull()
    expect(moveBlocker(byKey('A.X.Z'), 'A', keys)).toBeNull()
  })

  it('refuses roots, itself, descendants, the current parent and name clashes', () => {
    expect(moveBlocker(byKey('A'), 'B', keys)).not.toBeNull()
    expect(moveBlocker(byKey('A.X'), 'A.X', keys)).not.toBeNull()
    expect(moveBlocker(byKey('A.X'), 'A.X.Z', keys)).not.toBeNull()
    expect(moveBlocker(byKey('A.X'), 'A', keys)).not.toBeNull()
    expect(moveBlocker(byKey('A.X.Z'), 'B', keys)).not.toBeNull() // B.Z exists
    expect(moveBlocker(byKey('A.Y'), 'NOPE', keys)).not.toBeNull()
  })
})

describe('deleteBlocker', () => {
  it('allows only unused non-root leaves', () => {
    expect(deleteBlocker(c('A.Y'), 0, 0)).toBeNull()
    expect(deleteBlocker(c('A'), 0, 0)).not.toBeNull()
    expect(deleteBlocker(c('A.X'), 1, 0)).not.toBeNull()
    expect(deleteBlocker(c('A.Y'), 0, 2)).not.toBeNull()
  })
})
