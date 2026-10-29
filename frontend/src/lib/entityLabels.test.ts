import { describe, expect, it } from 'vitest'
import { subclassName } from './entityClass'
import { missingIds, resolveLabel } from './entityLabels'

describe('entity names', () => {
  const known = new Map([['P-1', 'Ivan Petrov']])

  it('prefers a locally known name, then a fetched one, then the id', () => {
    expect(resolveLabel('P-1', known, { 'P-1': 'stale' })).toBe('Ivan Petrov')
    expect(resolveLabel('O-1', known, { 'O-1': '3rd Battalion' })).toBe('3rd Battalion')
    expect(resolveLabel('X-9', known, {})).toBe('X-9')
  })

  it('only looks up ids that are unknown, unfetched and untried, once each', () => {
    const fetched = { 'O-1': '3rd Battalion' }
    const tried = new Set(['Z-1'])
    expect(missingIds(['P-1', 'O-1', 'Z-1', 'B-2', 'B-2', 'A-1', ''], known, fetched, tried)).toEqual(['A-1', 'B-2'])
  })

  it('turns a taxonomy key into a readable class name', () => {
    expect(subclassName('PERSON.MILITARY_PERSONNEL')).toBe('Military Personnel')
    expect(subclassName('VEHICLE.AIR_VEHICLE.FIGHTER')).toBe('Fighter')
    expect(subclassName('PERSON')).toBe('Person')
    expect(subclassName(undefined)).toBe('')
  })
})
