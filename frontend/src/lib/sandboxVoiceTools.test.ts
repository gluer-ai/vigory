import { describe, expect, it, vi } from 'vitest'
import { createSerialQueue, handleSandboxTool } from './sandboxVoiceTools'

const deps = () => ({
  describe: vi.fn().mockResolvedValue({ summary: 'CANVAS' }),
  edit: vi.fn().mockResolvedValue({ results: [] }),
})

describe('handleSandboxTool', () => {
  it('reads the canvas for get_sandbox', async () => {
    const d = deps()
    expect(await handleSandboxTool('get_sandbox', {}, d)).toEqual({ summary: 'CANVAS' })
    expect(d.describe).toHaveBeenCalledOnce()
  })

  it('passes valid operations to edit_sandbox', async () => {
    const d = deps()
    const ops = [{ op: 'rename_entity', entity: 'A', label: 'B' }]
    await handleSandboxTool('edit_sandbox', { ops }, d)
    expect(d.edit).toHaveBeenCalledWith(ops)
  })

  it('drops malformed operations and refuses an empty or non-array list', async () => {
    const d = deps()
    await handleSandboxTool('edit_sandbox', { ops: [null, 'x', { nope: 1 }, { op: 'add_link' }] }, d)
    expect(d.edit).toHaveBeenCalledWith([{ op: 'add_link' }])
    for (const ops of [[], 'rename', undefined, {}, [null]]) {
      expect(await handleSandboxTool('edit_sandbox', { ops }, d)).toEqual({ error: 'No valid operations were given.' })
    }
    expect(d.edit).toHaveBeenCalledOnce()
  })

  it('returns undefined for other tools so the backend handles them', async () => {
    const d = deps()
    expect(await handleSandboxTool('search_knowledge_graph', { query: 'x' }, d)).toBeUndefined()
    expect(d.describe).not.toHaveBeenCalled()
    expect(d.edit).not.toHaveBeenCalled()
  })
})

describe('createSerialQueue', () => {
  it('runs tasks strictly one after another, in order', async () => {
    const enqueue = createSerialQueue()
    const log: string[] = []
    const task = (name: string, ms: number) => () =>
      new Promise<string>((resolve) => {
        log.push(`start ${name}`)
        setTimeout(() => {
          log.push(`end ${name}`)
          resolve(name)
        }, ms)
      })
    const results = await Promise.all([enqueue(task('a', 30)), enqueue(task('b', 5)), enqueue(task('c', 1))])
    expect(results).toEqual(['a', 'b', 'c'])
    expect(log).toEqual(['start a', 'end a', 'start b', 'end b', 'start c', 'end c'])
  })

  it('keeps going after a failed task and still reports that failure to its caller', async () => {
    const enqueue = createSerialQueue()
    const failing = enqueue(() => Promise.reject(new Error('boom')))
    const after = enqueue(() => Promise.resolve('ok'))
    await expect(failing).rejects.toThrow('boom')
    await expect(after).resolves.toBe('ok')
  })
})
