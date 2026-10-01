import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../../lib/api'
import type { SandboxDoc } from '../../lib/sandboxModel'

export type SaveState = 'saved' | 'unsaved' | 'saving' | 'conflict' | 'rejected' | 'error'
export type LoadState = 'idle' | 'loading' | 'ready' | 'error'

const AUTOSAVE_MS = 800

/** Loads one sandbox and autosaves every edit (debounced). The server owns
 * `version`; a save from a stale tab gets a 409 and stops until reloaded, so
 * two tabs can never silently overwrite each other. */
export function useSandbox(id: string | null) {
  const [doc, setDoc] = useState<SandboxDoc>({ nodes: [], edges: [] })
  const [name, setName] = useState('')
  const [loadState, setLoadState] = useState<LoadState>('idle')
  const [saveState, setSaveState] = useState<SaveState>('saved')
  const [message, setMessage] = useState('')
  const [reloadKey, setReloadKey] = useState(0)

  // The latest canvas, updated synchronously by every edit (not only on render),
  // so back-to-back edits - e.g. several voice commands - each build on the last.
  const docRef = useRef(doc)
  const nameRef = useRef(name)
  nameRef.current = name
  const versionRef = useRef(0)
  const rev = useRef(0) // bumped on every edit
  const inFlight = useRef(false)
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined)
  const generation = useRef(0) // bumped on every (re)load; stale saves are ignored
  const stopped = useRef(false) // true after a conflict, until reload

  const flush = useCallback(
    (sandboxId: string) => {
      if (inFlight.current || stopped.current) return
      const gen = generation.current
      const startRev = rev.current
      const snapshot = { doc: docRef.current, name: nameRef.current, version: versionRef.current }
      inFlight.current = true
      setSaveState('saving')
      api
        .saveSandbox(sandboxId, {
          name: snapshot.name,
          nodes: snapshot.doc.nodes,
          edges: snapshot.doc.edges,
          version: snapshot.version,
        })
        .then((res) => {
          if (gen !== generation.current) return
          inFlight.current = false
          versionRef.current = res.version
          if (rev.current !== startRev) {
            timer.current = setTimeout(() => flush(sandboxId), 0)
          } else {
            setSaveState('saved')
            setMessage('')
          }
        })
        .catch((err) => {
          if (gen !== generation.current) return
          inFlight.current = false
          if (err instanceof ApiError && err.status === 409) {
            stopped.current = true
            setSaveState('conflict')
            setMessage(err.message)
          } else if (err instanceof ApiError && err.status === 422) {
            setSaveState('rejected')
            setMessage(err.message)
          } else {
            setSaveState('error')
            setMessage(err instanceof ApiError ? err.message : 'Could not reach the backend')
          }
        })
    },
    [],
  )

  const schedule = useCallback(() => {
    if (!id) return
    clearTimeout(timer.current)
    timer.current = setTimeout(() => flush(id), AUTOSAVE_MS)
  }, [id, flush])

  // (Re)load whenever the selected sandbox changes; save pending edits first.
  useEffect(() => {
    generation.current += 1
    stopped.current = false
    inFlight.current = false
    rev.current = 0
    setMessage('')
    setSaveState('saved')
    if (!id) {
      docRef.current = { nodes: [], edges: [] }
      setDoc(docRef.current)
      setName('')
      setLoadState('idle')
      return
    }
    setLoadState('loading')
    const gen = generation.current
    api
      .getSandbox(id)
      .then((sb) => {
        if (gen !== generation.current) return
        versionRef.current = sb.version
        docRef.current = { nodes: sb.nodes, edges: sb.edges }
        setDoc(docRef.current)
        setName(sb.name)
        setLoadState('ready')
      })
      .catch((err) => {
        if (gen !== generation.current) return
        setMessage(err instanceof ApiError ? err.message : 'Could not reach the backend')
        setLoadState('error')
      })
    return () => {
      clearTimeout(timer.current)
      // Leaving with unsaved edits: send them now rather than lose them.
      if (rev.current > 0 && !stopped.current && !inFlight.current && loadedRef.current === gen) {
        void api
          .saveSandbox(id, {
            name: nameRef.current,
            nodes: docRef.current.nodes,
            edges: docRef.current.edges,
            version: versionRef.current,
          })
          .catch(() => {})
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, reloadKey])

  // Tracks which generation finished loading, so the cleanup above never
  // saves an empty placeholder doc over a sandbox that failed to load.
  const loadedRef = useRef(0)
  useEffect(() => {
    if (loadState === 'ready') loadedRef.current = generation.current
  }, [loadState])

  const edit = useCallback(
    (fn: (d: SandboxDoc) => SandboxDoc) => {
      const next = fn(docRef.current)
      docRef.current = next
      setDoc(next)
      rev.current += 1
      if (!stopped.current) setSaveState('unsaved')
      schedule()
    },
    [schedule],
  )

  const rename = useCallback(
    (next: string) => {
      setName(next)
      nameRef.current = next
      if (!next.trim()) return // the server requires a name; keep the old one
      rev.current += 1
      if (!stopped.current) setSaveState('unsaved')
      schedule()
    },
    [schedule],
  )

  const retry = useCallback(() => {
    if (id) flush(id)
  }, [id, flush])

  const reload = useCallback(() => setReloadKey((k) => k + 1), [])
  const getDoc = useCallback(() => docRef.current, [])

  return { doc, getDoc, name, loadState, saveState, message, edit, rename, retry, reload }
}
