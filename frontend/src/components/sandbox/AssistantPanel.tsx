import { Check, ChevronDown, ChevronUp, Mic, MicOff, Send, Sparkles, Undo2, X } from 'lucide-react'
import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { api, ApiError } from '../../lib/api'
import { createSerialQueue, handleSandboxTool } from '../../lib/sandboxVoiceTools'
import type { AgentOp, AgentOpResult, SandboxEdgeData, SandboxNodeData } from '../../lib/types'
import { Button } from '../ui/Button'
import { useVoiceSession } from '../voice/useVoiceSession'

interface Canvas {
  nodes: SandboxNodeData[]
  edges: SandboxEdgeData[]
}

interface Message {
  id: number
  role: 'user' | 'assistant'
  text: string
  voice?: boolean
  results?: AgentOpResult[]
  error?: boolean
}

interface AssistantPanelProps {
  /** The latest canvas, read synchronously. Its object identity changes on every edit. */
  getCanvas: () => Canvas
  /** Replace the canvas (goes through autosave like any other edit). */
  applyCanvas: (canvas: Canvas) => void
  /** The level on screen: new entities land here, and names resolve here first. */
  container: string | null
  disabled: boolean
}

const MAX_UNDO = 10
const STALE =
  'The canvas changed while I was working, so I did not apply my changes. Please ask again.'

/** Chat + voice for editing the sandbox. Both end in the same server-side
 * validation; the panel only applies the canvas the server returns, and only
 * if the user has not edited it meanwhile. */
export function AssistantPanel({ getCanvas, applyCanvas, container, disabled }: AssistantPanelProps) {
  const [open, setOpen] = useState(true)
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [undoCount, setUndoCount] = useState(0)
  const [voiceConfigured, setVoiceConfigured] = useState(false)

  const nextId = useRef(0)
  const undoStack = useRef<Canvas[]>([])
  const enqueue = useRef(createSerialQueue()).current // serialises edits (voice can fire several at once)
  const listRef = useRef<HTMLDivElement>(null)
  const containerRef = useRef(container)
  containerRef.current = container
  const voiceSeen = useRef(0)

  useEffect(() => {
    api.getVoiceStatus().then((s) => setVoiceConfigured(s.configured)).catch(() => {})
  }, [])

  const push = useCallback((m: Omit<Message, 'id'>) => {
    setMessages((prev) => [...prev, { ...m, id: nextId.current++ }])
  }, [])

  /** Apply a server-returned canvas unless the user edited meanwhile. */
  const commit = useCallback(
    (before: Canvas, next: Canvas): boolean => {
      if (getCanvas() !== before) return false
      undoStack.current = [...undoStack.current.slice(-(MAX_UNDO - 1)), before]
      setUndoCount(undoStack.current.length)
      applyCanvas(next)
      return true
    },
    [getCanvas, applyCanvas],
  )

  // ---- chat ----
  async function send(e: FormEvent) {
    e.preventDefault()
    const text = input.trim()
    if (!text || busy || disabled) return
    setInput('')
    setBusy(true)
    const history = messages
      .filter((m) => !m.error)
      .slice(-6)
      .map((m) => ({ role: m.role, text: m.text }))
    push({ role: 'user', text })
    const before = getCanvas()
    try {
      const res = await api.agentChat({ ...before, container: containerRef.current }, text, history)
      let applied = true
      if (res.changed) applied = commit(before, { nodes: res.nodes, edges: res.edges })
      if (applied) push({ role: 'assistant', text: res.reply || (res.changed ? 'Done.' : ''), results: res.results })
      else push({ role: 'assistant', text: STALE, error: true })
    } catch (err) {
      push({
        role: 'assistant',
        text: err instanceof ApiError ? err.message : 'Could not reach the assistant.',
        error: true,
      })
    } finally {
      setBusy(false)
    }
  }

  function undo() {
    const previous = undoStack.current.pop()
    setUndoCount(undoStack.current.length)
    if (!previous) return
    applyCanvas(previous)
    push({ role: 'assistant', text: 'Undid the last assistant change.' })
  }

  // ---- voice ----
  const runEdit = useCallback(
    (ops: AgentOp[]) =>
      enqueue(async () => {
        const before = getCanvas()
        const res = await api.agentOps({ ...before, container: containerRef.current }, ops)
        if (res.changed && !commit(before, { nodes: res.nodes, edges: res.edges })) {
          return { error: 'The canvas changed while editing; nothing was applied. Read it again and retry.' }
        }
        push({ role: 'assistant', text: 'Voice edit', voice: true, results: res.results })
        return { results: res.results, canvas: res.summary }
      }),
    [enqueue, getCanvas, commit, push],
  )

  const voice = useVoiceSession({
    mode: 'sandbox',
    onTool: (name, args) =>
      handleSandboxTool(name, args, {
        describe: () => api.agentDescribe({ ...getCanvas(), container: containerRef.current }),
        edit: runEdit,
      }),
  })

  // Mirror the spoken transcript into the same message list, in order.
  useEffect(() => {
    if (voice.lines.length < voiceSeen.current) voiceSeen.current = 0 // a new call started
    for (const line of voice.lines.slice(voiceSeen.current)) {
      if (line.role === 'tool') continue
      push({ role: line.role === 'user' ? 'user' : 'assistant', text: line.text, voice: true })
    }
    voiceSeen.current = voice.lines.length
  }, [voice.lines, push])

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight })
  }, [messages, open])

  const listening = voice.status === 'live'
  const connecting = voice.status === 'connecting'

  return (
    <section
      aria-label="Sandbox assistant"
      className="flex shrink-0 flex-col border-t border-[var(--color-border)] bg-[var(--color-surface-1)]"
    >
      <div className="flex items-center gap-2 px-3 py-1.5">
        <Sparkles size={14} className="text-[var(--color-focus)]" aria-hidden="true" />
        <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">Assistant</h3>
        <span className="text-xs text-[var(--color-text-muted)]">
          Ask in writing or by voice; edits stay in this sandbox.
        </span>
        <span className="flex-1" />
        <Button onClick={undo} disabled={undoCount === 0} aria-label="Undo last assistant change" className="!px-2 !py-0.5 !text-xs">
          <Undo2 size={12} aria-hidden="true" /> Undo{undoCount > 0 ? ` (${undoCount})` : ''}
        </Button>
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          aria-label={open ? 'Collapse assistant' : 'Expand assistant'}
          className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
        >
          {open ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronUp size={14} aria-hidden="true" />}
        </button>
      </div>

      {open && (
        <>
          <div
            ref={listRef}
            role="log"
            aria-live="polite"
            aria-label="Assistant conversation"
            className="flex max-h-48 min-h-[56px] flex-col gap-2 overflow-y-auto px-3 py-1"
          >
            {messages.length === 0 && (
              <p className="text-xs text-[var(--color-text-muted)]">
                Try: &ldquo;Rename Acme to Acme Corp&rdquo;, &ldquo;Add an organization called Nordic Bank and link
                it to Oslo&rdquo;, &ldquo;Change the commands link to affiliated_with&rdquo;, or &ldquo;Copy Fjord
                Port from the knowledge graph&rdquo;.
              </p>
            )}
            {messages.map((m) => (
              <div key={m.id} className={`flex flex-col gap-0.5 text-sm ${m.role === 'user' ? 'items-end' : 'items-start'}`}>
                <span
                  className={`max-w-[85%] whitespace-pre-wrap rounded-lg px-2.5 py-1.5 ${
                    m.role === 'user'
                      ? 'bg-[var(--color-surface-hover)] text-[var(--color-text-primary)]'
                      : m.error
                        ? 'border border-[var(--color-status-destroyed)] text-[var(--color-status-destroyed)]'
                        : 'border border-[var(--color-border)] text-[var(--color-text-primary)]'
                  }`}
                >
                  {m.voice && <Mic size={11} className="me-1 inline text-[var(--color-text-muted)]" aria-label="spoken" />}
                  {m.text}
                </span>
                {m.results && m.results.length > 0 && (
                  <ul className="max-w-[85%] text-xs">
                    {m.results.map((r, i) => (
                      <li key={i} className={`flex items-start gap-1 ${r.ok ? 'text-[var(--color-text-muted)]' : 'text-[var(--color-status-destroyed)]'}`}>
                        {r.ok ? <Check size={12} className="mt-0.5 shrink-0" aria-label="done" /> : <X size={12} className="mt-0.5 shrink-0" aria-label="failed" />}
                        <span>{r.message}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
            {busy && <p className="text-xs text-[var(--color-text-muted)]">Working…</p>}
          </div>

          {voice.error && (
            <p role="alert" className="px-3 text-xs text-[var(--color-status-destroyed)]">{voice.error}</p>
          )}

          <form onSubmit={send} className="flex items-center gap-2 px-3 pb-2 pt-1">
            <label htmlFor="assistant-input" className="sr-only">Ask the assistant to change the sandbox</label>
            <input
              id="assistant-input"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              maxLength={2000}
              disabled={disabled}
              placeholder={disabled ? 'Open a sandbox first' : 'Ask me to add, rename, link or remove…'}
              autoComplete="off"
              className="min-w-0 flex-1 rounded-md border border-[var(--color-border)] bg-[var(--color-surface-0)] px-3 py-1.5 text-sm text-[var(--color-text-primary)] focus-visible:border-[var(--color-focus)] disabled:opacity-50"
            />
            <Button variant="primary" type="submit" disabled={disabled || busy || !input.trim()} aria-label="Send">
              <Send size={14} aria-hidden="true" /> Send
            </Button>
            <Button
              type="button"
              onClick={listening || connecting ? voice.disconnect : voice.connect}
              disabled={disabled || !voiceConfigured}
              aria-pressed={listening}
              title={voiceConfigured ? undefined : 'Voice is not configured on this server'}
              className={listening ? '!border-[var(--color-focus)] !text-[var(--color-focus)]' : ''}
            >
              {listening ? <MicOff size={14} aria-hidden="true" /> : <Mic size={14} aria-hidden="true" />}
              {connecting ? 'Connecting…' : listening ? 'Stop voice' : 'Voice'}
            </Button>
          </form>
        </>
      )}
    </section>
  )
}
