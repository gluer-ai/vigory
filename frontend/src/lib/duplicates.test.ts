import { describe, expect, it } from 'vitest'
import { chosenTarget, describeReason, describeWhere, entitySignature } from './duplicates'
import type { DuplicateItem } from './types'

const item: DuplicateItem = {
  entity_id: 'P-2',
  label: 'Anna',
  aliases: [],
  entity_class: 'PERSON',
  entity_subclass: 'PERSON.X',
  matches: [
    { entity_id: 'P-9', label: 'Anna P', aliases: [], entity_class: 'PERSON', entity_subclass: 'PERSON.X', where: 'graph', reason: 'same_label', score: 1 },
    { entity_id: 'P-1', label: 'Anna', aliases: [], entity_class: 'PERSON', entity_subclass: 'PERSON.X', where: 'batch', reason: 'similar', score: 0.9 },
  ],
}

describe('duplicates helpers', () => {
  it('warns when an id collision would overwrite', () => {
    expect(describeReason('same_id')).toMatch(/overwrite/)
    expect(describeWhere('graph')).toBe('already in the graph')
    expect(describeWhere('batch')).toBe('earlier in this batch')
  })

  it('merges into the picked match, defaulting to the strongest', () => {
    expect(chosenTarget(item, undefined).entity_id).toBe('P-9')
    expect(chosenTarget(item, 'P-1').entity_id).toBe('P-1')
    expect(chosenTarget(item, 'gone').entity_id).toBe('P-9')
  })

  it('signature changes when entities are removed or renamed', () => {
    expect(entitySignature([{ entity_id: 'a' }, { entity_id: 'b' }])).toBe('a|b')
    expect(entitySignature([{ entity_id: 'a' }])).not.toBe(entitySignature([{ entity_id: 'a' }, { entity_id: 'b' }]))
  })
})
