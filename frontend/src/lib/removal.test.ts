import { describe, expect, it } from 'vitest'
import { describeKept, describeRemoval, nothingToRemove } from './removal'
import type { RemovalPreview } from './types'

const base: RemovalPreview = {
  document_id: 'D-1',
  filename: 'a.pdf',
  status: 'committed',
  entities: { remove: 0, sample: [], kept_shared: 0, untagged_import: 0 },
  links: { remove: 0, untagged_import: 0 },
  classes: { remove: [], kept: [] },
  link_types: { remove: [], kept: [] },
}

describe('removal helpers', () => {
  it('describes only what will be removed, with correct plurals', () => {
    const p = { ...base, entities: { ...base.entities, remove: 1 }, links: { remove: 3, untagged_import: 0 } }
    expect(describeRemoval(p)).toEqual(['1 entity', '3 links'])
    expect(nothingToRemove(base)).toBe(true)
    expect(nothingToRemove(p)).toBe(false)
  })

  it('explains what is kept and why', () => {
    const p = {
      ...base,
      entities: { ...base.entities, kept_shared: 2 },
      classes: { remove: [], kept: [{ key: 'PERSON.X', reason: '4 other entities use it' }] },
    }
    expect(describeKept(p)).toEqual([
      '2 entities kept: other data still links to them',
      'Class PERSON.X kept: 4 other entities use it',
    ])
  })
})
