import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../../lib/api'
import type { IngestBatch } from '../../lib/types'
import {
  parseRealtimeEvent,
  sessionUpdateMessage,
  toolOutputMessages,
  type VoiceEvent,
} from '../../lib/voiceEvents'

export type VoiceStatus = 'idle' | 'connecting' | 'live' | 'error'

export interface TranscriptLine {
  id: number
  role: 'user' | 'agent' | 'tool'
  text: string
}

export interface ScenarioRef {
  triggerEntityId: string
  hops: number
}

interface Callbacks {
  onBatch?: (batch: IngestBatch) => void
  onScenario?: (scenario: ScenarioRef) => void
  /** 'kb' (default) grounds the agent in the knowledge base; 'sandbox' gives it
   * the tools for editing the sandbox canvas. */
  mode?: 'kb' | 'sandbox'
  /** Handle a tool in the browser (e.g. canvas edits). Return `undefined` to
   * fall through to the backend's knowledge-base tools. */
  onTool?: (name: string, args: Record<string, unknown>) => Promise<unknown | undefined>
}

/** Owns one WebRTC voice session: mic -> (Vigory relays SDP to the voice
 * platform) -> OpenAI Realtime. Tool calls from the model are executed on
 * the Vigory backend; their `ui` effects are handed to the panel. */
export function useVoiceSession({ onBatch, onScenario, onTool, mode = 'kb' }: Callbacks) {
  const [status, setStatus] = useState<VoiceStatus>('idle')
  const [error, setError] = useState('')
  const [lines, setLines] = useState<TranscriptLine[]>([])

  const pcRef = useRef<RTCPeerConnection | null>(null)
  const dcRef = useRef<RTCDataChannel | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const startedAt = useRef(0)
  const sessionId = useRef('')
  const nextId = useRef(0)
  const lineLog = useRef<string[]>([])
  const cb = useRef({ onBatch, onScenario, onTool })
  cb.current = { onBatch, onScenario, onTool }

  const addLine = useCallback((role: TranscriptLine['role'], text: string) => {
    setLines((prev) => [...prev, { id: nextId.current++, role, text }])
    if (role !== 'tool') lineLog.current.push(`${role === 'user' ? 'User' : 'Agent'}: ${text}`)
  }, [])

  const handleToolCall = useCallback(
    async (ev: Extract<VoiceEvent, { kind: 'tool_call' }>) => {
      addLine('tool', `${ev.name}(${JSON.stringify(ev.args)})`)
      let output: unknown
      try {
        const local = cb.current.onTool ? await cb.current.onTool(ev.name, ev.args) : undefined
        if (local !== undefined) {
          output = local
        } else {
          const res = await api.callVoiceTool(ev.name, ev.args)
          output = res.output
          if (res.ui?.type === 'batch') cb.current.onBatch?.(res.ui.batch)
          if (res.ui?.type === 'scenario')
            cb.current.onScenario?.({ triggerEntityId: res.ui.trigger_entity_id, hops: res.ui.hops })
        }
      } catch (err) {
        output = { error: err instanceof ApiError ? err.message : 'Tool call failed' }
      }
      const dc = dcRef.current
      if (dc?.readyState === 'open') for (const m of toolOutputMessages(ev.callId, output)) dc.send(m)
    },
    [addLine],
  )

  const disconnect = useCallback(() => {
    const transcript = lineLog.current.join('\n')
    const duration = startedAt.current ? Math.round((Date.now() - startedAt.current) / 1000) : 0
    if (transcript && sessionId.current) {
      // Best effort: the platform keeps call history; a failure must not block hang-up.
      api.saveVoiceTranscript(sessionId.current, transcript, duration).catch(() => {})
    }
    dcRef.current?.close()
    pcRef.current?.close()
    streamRef.current?.getTracks().forEach((t) => t.stop())
    if (audioRef.current) audioRef.current.srcObject = null
    dcRef.current = pcRef.current = streamRef.current = null
    lineLog.current = []
    startedAt.current = 0
    setStatus('idle')
  }, [])

  const connect = useCallback(async () => {
    setError('')
    setLines([])
    lineLog.current = []
    setStatus('connecting')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      streamRef.current = stream
      const pc = new RTCPeerConnection()
      pcRef.current = pc

      const audio = audioRef.current ?? (audioRef.current = new Audio())
      audio.autoplay = true
      pc.ontrack = (e) => {
        audio.srcObject = e.streams[0]
      }
      pc.onconnectionstatechange = () => {
        if (pc.connectionState === 'failed' || pc.connectionState === 'disconnected') {
          setError('Voice connection lost')
          disconnect()
          setStatus('error')
        }
      }
      stream.getTracks().forEach((t) => pc.addTrack(t, stream))

      const dc = pc.createDataChannel('oai-events')
      dcRef.current = dc

      const offer = await pc.createOffer()
      await pc.setLocalDescription(offer)
      const session = await api.createVoiceSession(offer.sdp ?? '', mode)
      await pc.setRemoteDescription({ type: 'answer', sdp: session.sdp })

      dc.onopen = () => {
        dc.send(sessionUpdateMessage(session.instructions, session.tools))
        sessionId.current = crypto.randomUUID()
        startedAt.current = Date.now()
        setStatus('live')
        // Make the agent speak first so the user knows it's listening.
        dc.send(JSON.stringify({ type: 'response.create' }))
      }
      dc.onmessage = (m) => {
        const ev = parseRealtimeEvent(String(m.data))
        if (!ev) return
        if (ev.kind === 'user_transcript') addLine('user', ev.text)
        else if (ev.kind === 'agent_transcript') addLine('agent', ev.text)
        else if (ev.kind === 'tool_call') void handleToolCall(ev)
        else setError(ev.message)
      }
    } catch (err) {
      streamRef.current?.getTracks().forEach((t) => t.stop())
      pcRef.current?.close()
      pcRef.current = dcRef.current = streamRef.current = null
      setError(
        err instanceof ApiError
          ? err.message
          : err instanceof DOMException && err.name === 'NotAllowedError'
            ? 'Microphone permission denied'
            : 'Could not start the voice session',
      )
      setStatus('error')
    }
  }, [addLine, disconnect, handleToolCall, mode])

  useEffect(() => disconnect, [disconnect])

  return { status, error, lines, connect, disconnect }
}
