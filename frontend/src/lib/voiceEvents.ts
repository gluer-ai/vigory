import type { VoiceTool } from './types'

/** The subset of OpenAI Realtime server events the voice panel acts on. */
export type VoiceEvent =
  | { kind: 'user_transcript'; text: string }
  | { kind: 'agent_transcript'; text: string }
  | { kind: 'tool_call'; callId: string; name: string; args: Record<string, unknown> }
  | { kind: 'error'; message: string }

export function parseRealtimeEvent(raw: string): VoiceEvent | null {
  let ev: Record<string, any>
  try {
    ev = JSON.parse(raw)
  } catch {
    return null
  }
  switch (ev.type) {
    case 'conversation.item.input_audio_transcription.completed':
      return typeof ev.transcript === 'string' && ev.transcript.trim()
        ? { kind: 'user_transcript', text: ev.transcript.trim() }
        : null
    // GA and beta names for the same event.
    case 'response.output_audio_transcript.done':
    case 'response.audio_transcript.done':
      return typeof ev.transcript === 'string' && ev.transcript.trim()
        ? { kind: 'agent_transcript', text: ev.transcript.trim() }
        : null
    case 'response.output_item.done': {
      const item = ev.item
      if (item?.type !== 'function_call' || !item.call_id || !item.name) return null
      let args: Record<string, unknown> = {}
      try {
        const parsed = JSON.parse(item.arguments || '{}')
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) args = parsed
      } catch {
        // malformed arguments -> call with none; the backend validates and the
        // model is told what was wrong.
      }
      return { kind: 'tool_call', callId: item.call_id, name: item.name, args }
    }
    case 'error':
      return { kind: 'error', message: ev.error?.message ?? 'Realtime error' }
    default:
      return null
  }
}

/** First message on the data channel: swap in the KB-grounded prompt and tools. */
export function sessionUpdateMessage(instructions: string, tools: VoiceTool[]): string {
  return JSON.stringify({
    type: 'session.update',
    session: {
      type: 'realtime',
      instructions,
      tools,
      tool_choice: 'auto',
      audio: { input: { transcription: { model: 'gpt-4o-mini-transcribe' } } },
    },
  })
}

export function toolOutputMessages(callId: string, output: unknown): string[] {
  return [
    JSON.stringify({
      type: 'conversation.item.create',
      item: { type: 'function_call_output', call_id: callId, output: JSON.stringify(output) },
    }),
    JSON.stringify({ type: 'response.create' }),
  ]
}
