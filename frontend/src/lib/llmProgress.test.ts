import { describe, expect, it } from 'vitest'
import { describeLlm, describeStage, formatElapsed, progressPercent } from './llmProgress'
import type { LlmStatus } from './types'

const base: LlmStatus = {
  state: 'ready',
  provider: 'ollama',
  model: 'ollama_chat/qwen3:8b',
  local: true,
  reachable: true,
  model_installed: true,
  model_loaded: true,
  active: [],
  last_call: null,
}

describe('llmProgress', () => {
  it('formats elapsed time', () => {
    expect(formatElapsed(42.9)).toBe('42s')
    expect(formatElapsed(185)).toBe('3m 05s')
  })

  it('describes stage with part counter only for multi-part jobs', () => {
    expect(describeStage('Extracting', 2, 5)).toBe('Extracting (part 2/5)')
    expect(describeStage('Extracting', 1, 1)).toBe('Extracting')
    expect(describeStage(null, null, null)).toBe('Working')
  })

  it('computes percent from completed parts', () => {
    expect(progressPercent(1, 4)).toBe(0)
    expect(progressPercent(3, 4)).toBe(50)
    expect(progressPercent(null, 4)).toBeNull()
    expect(progressPercent(2, 0)).toBeNull()
  })

  it('summarises busy, cold and failure states', () => {
    const busy = describeLlm({ ...base, state: 'busy', active: [{ operation: 'Extracting', elapsed_seconds: 70 }] })
    expect(busy).toMatchObject({ tone: 'busy', text: 'qwen3:8b working · 1m 10s', detail: 'Extracting' })
    expect(describeLlm({ ...base, model_loaded: false }).detail).toMatch(/model loads/)
    expect(describeLlm({ ...base, state: 'unreachable' }).tone).toBe('error')
    expect(describeLlm({ ...base, state: 'downloading' }).tone).toBe('warn')
    expect(describeLlm(null).tone).toBe('warn')
  })
})
