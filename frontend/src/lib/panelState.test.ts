import { beforeEach, describe, expect, it, vi } from 'vitest'
import { isWide, readFlag, writeFlag } from './panelState'

describe('panel flags', () => {
  beforeEach(() => localStorage.clear())

  it('falls back when nothing is stored, then remembers what was written', () => {
    expect(readFlag('x', false)).toBe(false)
    expect(readFlag('x', true)).toBe(true)
    writeFlag('x', true)
    expect(readFlag('x', false)).toBe(true)
    writeFlag('x', false)
    expect(readFlag('x', true)).toBe(false)
  })

  it('keeps flags independent of each other', () => {
    writeFlag('a', true)
    expect(readFlag('b', false)).toBe(false)
  })

  it('survives storage being unavailable', () => {
    const boom = () => {
      throw new Error('denied')
    }
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(boom)
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(boom)
    expect(readFlag('x', true)).toBe(true)
    expect(() => writeFlag('x', true)).not.toThrow()
    vi.restoreAllMocks()
  })
})

describe('isWide', () => {
  it('is true only when every panel is collapsed', () => {
    expect(isWide([true, true, true])).toBe(true)
    expect(isWide([true, false, true])).toBe(false)
    expect(isWide([false, false, false])).toBe(false)
  })
})
