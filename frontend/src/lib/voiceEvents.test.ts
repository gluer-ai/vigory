import { describe, expect, it } from 'vitest'
import { parseRealtimeEvent, sessionUpdateMessage, toolOutputMessages } from './voiceEvents'

describe('parseRealtimeEvent', () => {
  it('parses a function call with JSON arguments', () => {
    const raw = JSON.stringify({
      type: 'response.output_item.done',
      item: { type: 'function_call', call_id: 'c1', name: 'build_scenario', arguments: '{"entity_id":"A"}' },
    })
    expect(parseRealtimeEvent(raw)).toEqual({
      kind: 'tool_call', callId: 'c1', name: 'build_scenario', args: { entity_id: 'A' },
    })
  })

  it('falls back to empty args when arguments are malformed or not an object', () => {
    for (const args of ['{oops', '[1,2]', '']) {
      const raw = JSON.stringify({
        type: 'response.output_item.done',
        item: { type: 'function_call', call_id: 'c', name: 'list_documents', arguments: args },
      })
      expect(parseRealtimeEvent(raw)).toMatchObject({ kind: 'tool_call', args: {} })
    }
  })

  it('ignores non-function output items and unknown events', () => {
    expect(parseRealtimeEvent(JSON.stringify({ type: 'response.output_item.done', item: { type: 'message' } }))).toBeNull()
    expect(parseRealtimeEvent(JSON.stringify({ type: 'session.created' }))).toBeNull()
    expect(parseRealtimeEvent('not json')).toBeNull()
  })

  it('parses transcripts (GA and beta names) and skips blanks', () => {
    expect(parseRealtimeEvent(JSON.stringify({ type: 'response.output_audio_transcript.done', transcript: ' hi ' })))
      .toEqual({ kind: 'agent_transcript', text: 'hi' })
    expect(parseRealtimeEvent(JSON.stringify({ type: 'response.audio_transcript.done', transcript: 'yo' })))
      .toEqual({ kind: 'agent_transcript', text: 'yo' })
    expect(parseRealtimeEvent(JSON.stringify({ type: 'conversation.item.input_audio_transcription.completed', transcript: '  ' })))
      .toBeNull()
  })

  it('surfaces realtime errors', () => {
    expect(parseRealtimeEvent(JSON.stringify({ type: 'error', error: { message: 'boom' } })))
      .toEqual({ kind: 'error', message: 'boom' })
  })
})

describe('outgoing messages', () => {
  it('session.update carries instructions and tools', () => {
    const msg = JSON.parse(sessionUpdateMessage('GROUND', [{ type: 'function', name: 't', description: 'd', parameters: {} }]))
    expect(msg.type).toBe('session.update')
    expect(msg.session.instructions).toBe('GROUND')
    expect(msg.session.tools[0].name).toBe('t')
  })

  it('tool output is a function_call_output followed by response.create', () => {
    const [a, b] = toolOutputMessages('c1', { ok: true }).map((m) => JSON.parse(m))
    expect(a.item).toEqual({ type: 'function_call_output', call_id: 'c1', output: '{"ok":true}' })
    expect(b.type).toBe('response.create')
  })
})
