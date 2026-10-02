import {
  Background,
  ConnectionMode,
  Controls,
  ReactFlow,
  ReactFlowProvider,
  useNodesState,
  useReactFlow,
  type Connection,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import {
  ChevronRight,
  CornerUpLeft,
  FlaskConical,
  Maximize,
  Maximize2,
  Minimize,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Plus,
  Upload,
  Search,
  ShieldCheck,
  Trash2,
} from 'lucide-react'
import { Fragment, useCallback, useContext, useEffect, useMemo, useRef, useState, type DragEvent } from 'react'
import { api, ApiError } from '../../lib/api'
import { isWide, RailContext, usePersistedFlag } from '../../lib/panelState'
import {
  addEdge,
  addGraphMatch,
  addNewEntity,
  addScenario,
  brokenLinksIfReclassified,
  childCount,
  containerExists,
  copyEntityIntoSandbox,
  edgeStatus,
  expandAround,
  explodeInto,
  levelOf,
  rankMatches,
  nodeStatus,
  pickAutoMatch,
  pathTo,
  removeEdge,
  removeNode,
  revertNode,
  searchTerms,
  summarize,
  updateEdge,
  updateNode,
  validLinkTypes,
  type SandboxDoc,
} from '../../lib/sandboxModel'
import type {
  ClassDef,
  Entity,
  LinkDef,
  SandboxEdgeData,
  SandboxNodeData,
  SandboxSummary,
  ScenarioExtract,
} from '../../lib/types'
import { RelationEdge, type RelationEdgeData } from '../graph/RelationEdge'
import { EntitySearch } from '../layout/EntitySearch'
import { Button } from '../ui/Button'
import { Select } from '../ui/Select'
import { AssistantPanel } from './AssistantPanel'
import { SaveToGraphDialog } from './SaveToGraphDialog'
import { ScenarioDialog } from './ScenarioDialog'
import { SandboxNode, type SandboxNodeViewData } from './SandboxNode'
import { useSandbox, type SaveState } from './useSandbox'

const nodeTypes = { sandbox: SandboxNode }
const edgeTypes = { relation: RelationEdge }
const DRAG_TYPE = 'application/x-vigory-entity'

const SAVE_LABEL: Record<SaveState, string> = {
  saved: 'Saved',
  unsaved: 'Unsaved changes…',
  saving: 'Saving…',
  conflict: 'Changed elsewhere',
  rejected: 'Not saved',
  error: 'Save failed',
}

type Selection = { kind: 'node' | 'edge'; id: string } | null
const inputCls =
  'w-full rounded-md border border-[var(--color-border)] bg-[var(--color-surface-0)] px-2 py-1.5 text-sm text-[var(--color-text-primary)] focus-visible:border-[var(--color-focus)]'

const ACTIVE_KEY = 'vigory.sandbox.active'

/** Summarise what an added scenario did, for the confirmation line. */
function describeAddition(r: { entities: number; copied: number; links: number; skippedLinks: number }) {
  const parts = [
    `${r.entities} ${r.entities === 1 ? 'entity' : 'entities'}`,
    `${r.links} ${r.links === 1 ? 'link' : 'links'}`,
  ]
  let msg = `Added ${parts.join(' and ')}`
  if (r.copied > 0) msg += `, plus ${r.copied} existing graph ${r.copied === 1 ? 'entity' : 'entities'}`
  msg += '.'
  if (r.skippedLinks > 0) msg += ` ${r.skippedLinks} ${r.skippedLinks === 1 ? 'link' : 'links'} could not be placed.`
  return msg
}

type Adder = { name: string; add: (extract: ScenarioExtract) => string }

interface SandboxPageProps {
  /** The "Ingest scenario" dialog is controlled by the app so the left-rail button can open it. */
  ingestOpen: boolean
  onIngestOpenChange: (open: boolean) => void
}

export function SandboxPage({ ingestOpen, onIngestOpenChange }: SandboxPageProps) {
  const [list, setList] = useState<SandboxSummary[]>([])
  const [listLoaded, setListLoaded] = useState(false)
  const [listError, setListError] = useState('')
  // Remembered for the tab session, so leaving and coming back (or being sent here
  // by "Ingest scenario") returns to the sandbox you were working in.
  const [activeId, setActiveIdState] = useState<string | null>(() => sessionStorage.getItem(ACTIVE_KEY))
  const [adder, setAdder] = useState<Adder | null>(null)
  const [listCollapsed, setListCollapsed] = usePersistedFlag('sandbox.list.collapsed', false)
  const [classes, setClasses] = useState<ClassDef[]>([])
  const [linkDefs, setLinkDefs] = useState<LinkDef[]>([])

  const setActiveId = useCallback((id: string | null) => {
    setActiveIdState(id)
    if (id) sessionStorage.setItem(ACTIVE_KEY, id)
    else sessionStorage.removeItem(ACTIVE_KEY)
  }, [])

  const refreshList = useCallback(async () => {
    try {
      setList(await api.listSandboxes())
      setListError('')
      setListLoaded(true)
    } catch (err) {
      setListError(err instanceof ApiError ? err.message : 'Could not reach the backend')
    }
  }, [])

  useEffect(() => {
    void refreshList()
    api.getClasses().then(setClasses).catch(() => {})
    api.getLinkDefs().then(setLinkDefs).catch(() => {})
  }, [refreshList])

  // A remembered sandbox that no longer exists (deleted elsewhere) is dropped -
  // checked once, against the first list. Later changes to the list must not
  // clear a sandbox that was just created and is not in it yet.
  const rememberedChecked = useRef(false)
  useEffect(() => {
    if (!listLoaded || rememberedChecked.current) return
    rememberedChecked.current = true
    if (activeId && !list.some((s) => s.sandbox_id === activeId)) setActiveId(null)
  }, [listLoaded, list, activeId, setActiveId])

  /** Create a sandbox holding an extracted scenario. An unsaved empty
   * sandbox is not left behind if filling it fails. */
  const createFromScenario = useCallback(
    async (name: string, extract: ScenarioExtract) => {
      const sb = await api.createSandbox(name)
      try {
        const added = addScenario({ nodes: sb.nodes, edges: sb.edges }, extract, null)
        await api.saveSandbox(sb.sandbox_id, {
          name,
          nodes: added.doc.nodes,
          edges: added.doc.edges,
          version: sb.version,
        })
        setActiveId(sb.sandbox_id)
        void refreshList()
        return describeAddition(added)
      } catch (err) {
        await api.deleteSandbox(sb.sandbox_id).catch(() => {})
        throw err
      }
    },
    [refreshList, setActiveId],
  )

  return (
    <div className="flex h-full min-h-0 w-full">
      <SandboxList
        collapsed={listCollapsed}
        onToggle={() => setListCollapsed(!listCollapsed)}
        list={list}
        error={listError}
        activeId={activeId}
        onSelect={setActiveId}
        onChanged={refreshList}
        onCreated={(id) => {
          setActiveId(id)
          void refreshList()
        }}
        onDeleted={(id) => {
          if (id === activeId) setActiveId(null)
          void refreshList()
        }}
      />
      {activeId ? (
        <ReactFlowProvider key={activeId}>
          <Editor
            sandboxId={activeId}
            classes={classes}
            linkDefs={linkDefs}
            onSaved={refreshList}
            onAdderChange={setAdder}
            listCollapsed={listCollapsed}
            onListCollapsedChange={setListCollapsed}
          />
        </ReactFlowProvider>
      ) : (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 p-8 text-center">
          <FlaskConical size={28} className="text-[var(--color-focus)]" aria-hidden="true" />
          <p className="max-w-sm text-sm text-[var(--color-text-muted)]">
            Create a sandbox to rearrange entities and change links on a private copy. Nothing you do
            here touches the real knowledge graph, and nobody else can see it.
          </p>
        </div>
      )}
      <ScenarioDialog
        open={ingestOpen}
        onOpenChange={onIngestOpenChange}
        current={activeId ? adder : null}
        createNew={createFromScenario}
      />
    </div>
  )
}

/** Small icon button for collapsing/expanding a side panel. */
function PanelToggle(props: {
  label: string
  expanded: boolean
  onClick: () => void
  controls?: string
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={props.onClick}
      aria-label={props.label}
      aria-expanded={props.expanded}
      aria-controls={props.controls}
      title={props.label}
      className="rounded-md p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text-primary)]"
    >
      {props.children}
    </button>
  )
}

// ---- left: your sandboxes ----------------------------------------------------

function SandboxList(props: {
  collapsed: boolean
  onToggle: () => void
  list: SandboxSummary[]
  error: string
  activeId: string | null
  onSelect: (id: string) => void
  onChanged: () => void
  onCreated: (id: string) => void
  onDeleted: (id: string) => void
}) {
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')
  const [trigger, setTrigger] = useState('')
  const [hops, setHops] = useState('2')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)

  async function create() {
    setBusy(true)
    setError('')
    try {
      const sb = await api.createSandbox(name.trim(), trigger.trim() || undefined, Number(hops))
      setCreating(false)
      setName('')
      setTrigger('')
      props.onCreated(sb.sandbox_id)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not reach the backend')
    } finally {
      setBusy(false)
    }
  }

  async function remove(id: string) {
    try {
      await api.deleteSandbox(id)
      setConfirmDelete(null)
      props.onDeleted(id)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not delete')
    }
  }

  if (props.collapsed) {
    return (
      <aside
        aria-label="Your sandboxes (hidden)"
        className="flex w-10 shrink-0 flex-col items-center gap-2 border-e border-[var(--color-border)] py-2"
      >
        <PanelToggle label="Show your sandboxes" expanded={false} onClick={props.onToggle}>
          <PanelLeftOpen size={16} aria-hidden="true" />
        </PanelToggle>
        <FlaskConical size={14} className="text-[var(--color-text-muted)]" aria-hidden="true" />
        <span className="text-[10px] text-[var(--color-text-muted)]" aria-hidden="true">{props.list.length}</span>
      </aside>
    )
  }

  return (
    <aside
      id="sandbox-list"
      className="flex w-60 shrink-0 flex-col gap-3 overflow-y-auto border-e border-[var(--color-border)] p-3"
      aria-label="Your sandboxes"
    >
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-[var(--color-text-primary)]">Your sandboxes</h2>
        <div className="flex items-center gap-1">
          <Button onClick={() => setCreating((v) => !v)} aria-expanded={creating}>
            <Plus size={14} aria-hidden="true" /> New
          </Button>
          <PanelToggle label="Hide your sandboxes" expanded onClick={props.onToggle} controls="sandbox-list">
            <PanelLeftClose size={16} aria-hidden="true" />
          </PanelToggle>
        </div>
      </div>
      <p className="flex items-start gap-1.5 text-xs text-[var(--color-text-muted)]">
        <ShieldCheck size={14} className="mt-0.5 shrink-0 text-[var(--color-focus)]" aria-hidden="true" />
        Private to you. Edits never change the real graph.
      </p>

      {creating && (
        <div className="flex flex-col gap-2 rounded-md border border-[var(--color-border)] p-2">
          <label htmlFor="sb-name" className="text-xs text-[var(--color-text-muted)]">Name</label>
          <input id="sb-name" className={inputCls} value={name} maxLength={100} onChange={(e) => setName(e.target.value)} />
          <span className="text-xs text-[var(--color-text-muted)]">
            Start from an entity (optional) — its neighbourhood is copied in
          </span>
          <EntitySearch onSelect={setTrigger} />
          {trigger && <p className="font-mono text-xs text-[var(--color-text-primary)]">{trigger}</p>}
          <Select
            aria-label="Hops"
            value={hops}
            onValueChange={setHops}
            options={[1, 2, 3, 4].map((h) => ({ value: String(h), label: `${h} hop${h > 1 ? 's' : ''}` }))}
          />
          {error && <p role="alert" className="text-xs text-[var(--color-status-destroyed)]">{error}</p>}
          <Button variant="primary" onClick={create} disabled={!name.trim() || busy}>
            {busy ? 'Creating…' : 'Create sandbox'}
          </Button>
        </div>
      )}

      {props.error && <p role="alert" className="text-xs text-[var(--color-status-destroyed)]">{props.error}</p>}
      {!props.error && props.list.length === 0 && !creating && (
        <p className="text-xs text-[var(--color-text-muted)]">No sandboxes yet.</p>
      )}
      <ul className="flex flex-col gap-1">
        {props.list.map((s) => (
          <li key={s.sandbox_id}>
            <div
              className={`flex items-start justify-between gap-2 rounded-md border p-2 ${
                s.sandbox_id === props.activeId
                  ? 'border-[var(--color-focus)] bg-[var(--color-surface-hover)]'
                  : 'border-[var(--color-border)]'
              }`}
            >
              <button
                type="button"
                className="min-w-0 flex-1 text-start"
                onClick={() => props.onSelect(s.sandbox_id)}
                aria-current={s.sandbox_id === props.activeId}
              >
                <span className="block truncate text-sm font-medium text-[var(--color-text-primary)]">{s.name}</span>
                <span className="text-xs text-[var(--color-text-muted)]">
                  {s.node_count} entities · {s.edge_count} links
                </span>
              </button>
              {confirmDelete === s.sandbox_id ? (
                <span className="flex flex-col gap-1">
                  <button type="button" className="text-xs text-[var(--color-status-destroyed)]" onClick={() => remove(s.sandbox_id)}>
                    Delete
                  </button>
                  <button type="button" className="text-xs text-[var(--color-text-muted)]" onClick={() => setConfirmDelete(null)}>
                    Keep
                  </button>
                </span>
              ) : (
                <button
                  type="button"
                  aria-label={`Delete sandbox ${s.name}`}
                  className="text-[var(--color-text-muted)] hover:text-[var(--color-status-destroyed)]"
                  onClick={() => setConfirmDelete(s.sandbox_id)}
                >
                  <Trash2 size={14} />
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>
    </aside>
  )
}

// ---- centre + right: the editable canvas ---------------------------------------

function Editor({
  sandboxId,
  classes,
  linkDefs,
  onSaved,
  onAdderChange,
  listCollapsed,
  onListCollapsedChange,
}: {
  listCollapsed: boolean
  onListCollapsedChange: (collapsed: boolean) => void
  sandboxId: string
  classes: ClassDef[]
  linkDefs: LinkDef[]
  onSaved: () => void
  /** Tells the page how to add an extracted scenario to THIS sandbox (null when unmounted). */
  onAdderChange: (adder: Adder | null) => void
}) {
  const { doc, getDoc, name, loadState, saveState, message, edit, rename, retry, reload } =
    useSandbox(sandboxId)
  const { screenToFlowPosition, fitView } = useReactFlow()
  const [selected, setSelected] = useState<Selection>(null)
  const [pending, setPending] = useState<{ source: string; target: string } | null>(null)
  const [notice, setNotice] = useState('')
  const [container, setContainer] = useState<string | null>(null)
  const [exploding, setExploding] = useState(false)
  const [expanding, setExpanding] = useState<string | null>(null)
  const [fitPending, setFitPending] = useState(false)
  const [saveOpen, setSaveOpen] = useState(false)
  const [toolsCollapsed, setToolsCollapsed] = usePersistedFlag('sandbox.tools.collapsed', false)
  const rail = useContext(RailContext)
  const wide = isWide([rail.collapsed, listCollapsed, toolsCollapsed])

  /** One click to hide (or restore) every side panel around the canvas. */
  function toggleWide() {
    const next = !wide
    rail.setCollapsed(next)
    onListCollapsedChange(next)
    setToolsCollapsed(next)
    // Once the canvas has its new width, fit the drawing to it.
    setTimeout(() => void fitView({ duration: 300, padding: 0.15 }), 300)
  }
  const wrapper = useRef<HTMLDivElement>(null)

  // Keep the list's counts fresh once a save lands.
  useEffect(() => {
    if (saveState === 'saved') onSaved()
  }, [saveState, onSaved])

  const nodeById = useMemo(() => new Map(doc.nodes.map((n) => [n.id, n])), [doc.nodes])

  // Which level of the hierarchy is on screen. If the container we are inside
  // gets removed (e.g. from another tab's reload), fall back to the top level.
  const here = containerExists(doc, container) ? container : null
  const level = useMemo(() => levelOf(doc, here), [doc, here])
  const trail = useMemo(() => pathTo(doc, here), [doc, here])

  const go = useCallback((id: string | null) => {
    setContainer(id)
    setSelected(null)
    setPending(null)
    setNotice('')
  }, [])
  const goUp = useCallback(() => go(trail.length >= 2 ? trail[trail.length - 2].id : null), [go, trail])

  // Offer "add a scenario to this sandbox" to the page's ingest dialog. New items
  // land on the level currently on screen.
  const addScenarioHere = useCallback(
    (extract: ScenarioExtract) => {
      const added = addScenario(getDoc(), extract, here)
      edit(() => added.doc)
      return describeAddition(added)
    },
    [getDoc, edit, here],
  )
  useEffect(() => {
    if (loadState !== 'ready') return
    onAdderChange({ name, add: addScenarioHere })
    return () => onAdderChange(null)
  }, [loadState, name, addScenarioHere, onAdderChange])

  // Escape = come back out one level (unless something else, like an open
  // dropdown, already used the key).
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key !== 'Escape' || e.defaultPrevented || here === null) return
      const t = e.target as HTMLElement | null
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return
      goUp()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [here, goUp])

  // React Flow measures each node and reports it through onNodesChange. Those
  // measurements must live in the node objects we hand back, or every re-render
  // makes the nodes look unmeasured and they flicker invisible (swallowing the
  // second click of a double-click). So the rendered nodes are kept in state and
  // rebuilt from the sandbox while carrying their measurements over.
  const [rfNodes, setRfNodes, applyRfChanges] = useNodesState<Node<SandboxNodeViewData>>([])
  useEffect(() => {
    setRfNodes((current) => {
      const previous = new Map(current.map((n) => [n.id, n]))
      return level.nodes.map((n) => ({
        ...previous.get(n.id),
        id: n.id,
        type: 'sandbox',
        position: { x: n.x, y: n.y },
        selected: selected?.kind === 'node' && selected.id === n.id,
        data: {
          label: n.label,
          entity_subclass: n.entity_subclass,
          status: nodeStatus(n),
          inside: childCount(doc, n.id),
        },
      }))
    })
  }, [level.nodes, doc, selected, setRfNodes])
  // After an expansion, zoom to show everything - but only once React Flow has
  // measured the new nodes, or it would fit to the old layout.
  useEffect(() => {
    if (!fitPending || rfNodes.length === 0) return
    if (rfNodes.every((n) => n.measured?.width)) {
      void fitView({ duration: 300, padding: 0.15 })
      setFitPending(false)
    }
  }, [fitPending, rfNodes, fitView])

  const rfEdges: Edge<RelationEdgeData>[] = useMemo(
    () =>
      level.edges.map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        type: 'relation',
        selected: selected?.kind === 'edge' && selected.id === e.id,
        data: { link_type: e.link_type, proposed: edgeStatus(e) !== 'original', inside: childCount(doc, e.id) },
      })),
    [level.edges, doc, selected],
  )

  const onNodesChange = useCallback(
    (changes: NodeChange[]) => {
      const measured = changes.filter((c) => c.type === 'dimensions')
      if (measured.length > 0) applyRfChanges(measured)
      const moves = new Map<string, { x: number; y: number }>()
      const removed = new Set<string>()
      for (const c of changes) {
        if (c.type === 'position' && c.position) moves.set(c.id, c.position)
        if (c.type === 'remove') removed.add(c.id)
      }
      if (moves.size === 0 && removed.size === 0) return
      edit((d) => {
        let next = d
        if (moves.size > 0) {
          next = {
            ...next,
            nodes: next.nodes.map((n) => {
              const p = moves.get(n.id)
              return p ? { ...n, x: p.x, y: p.y } : n
            }),
          }
        }
        for (const id of removed) next = removeNode(next, id)
        return next
      })
      if (selected?.kind === 'node' && removed.has(selected.id)) setSelected(null)
    },
    [edit, selected, applyRfChanges],
  )

  const onEdgesChange = useCallback(
    (changes: EdgeChange[]) => {
      const removed = changes.filter((c) => c.type === 'remove').map((c) => c.id)
      if (removed.length === 0) return
      edit((d) => removed.reduce((acc, id) => removeEdge(acc, id), d))
      if (selected?.kind === 'edge' && removed.includes(selected.id)) setSelected(null)
    },
    [edit, selected],
  )

  const onConnect = useCallback((c: Connection) => {
    if (c.source && c.target && c.source !== c.target) {
      setPending({ source: c.source, target: c.target })
      setSelected(null)
    }
  }, [])

  const viewportCentre = useCallback(() => {
    const r = wrapper.current?.getBoundingClientRect()
    return screenToFlowPosition({
      x: (r?.left ?? 0) + (r?.width ?? 600) / 2,
      y: (r?.top ?? 0) + (r?.height ?? 400) / 2,
    })
  }, [screenToFlowPosition])

  const addExisting = useCallback(
    async (entity: Entity, at: { x: number; y: number }) => {
      const existing = nodeById.get(entity.entity_id)
      if (existing) {
        const sameLevel = (existing.parent ?? null) === here
        setNotice(
          sameLevel
            ? `"${entity.label}" is already on this level.`
            : `"${entity.label}" is already in this sandbox, on another level.`,
        )
        if (sameLevel) setSelected({ kind: 'node', id: entity.entity_id })
        return
      }
      setNotice('')
      let realLinks: Awaited<ReturnType<typeof api.getScope>>['edges'] = []
      try {
        // Read-only: pull real links between this entity and what's already here.
        realLinks = (await api.getScope(entity.entity_id, 1)).edges
      } catch {
        // Still add the entity; links can be drawn by hand.
      }
      edit((d) => copyEntityIntoSandbox(d, entity, at, realLinks, here))
      setSelected({ kind: 'node', id: entity.entity_id })
    },
    [edit, nodeById, here],
  )

  /** Follow an entity's connections in the knowledge graph: copy the connected entities
   * around it and draw the real links. Click again (or "Show more") to keep going. */
  const expand = useCallback(
    async (nodeId: string, limit?: number) => {
      const start = getDoc().nodes.find((n) => n.id === nodeId)
      if (!start || start.origin !== 'graph') return
      setExpanding(nodeId)
      try {
        const scope = await api.getScope(nodeId, 1)
        // Read the canvas again after the wait: the user (or the assistant) may have edited it.
        const result = expandAround(getDoc(), nodeId, scope.nodes, scope.edges, limit)
        if (result.added.length > 0 || result.links > 0) edit(() => result.doc)
        if (result.added.length > 0) setFitPending(true) // zoom out to show them once laid out
        const bits: string[] = []
        if (result.added.length > 0)
          bits.push(`Added ${result.added.length} connected ${result.added.length === 1 ? 'entity' : 'entities'}`)
        if (result.links > 0) bits.push(`${result.links} ${result.links === 1 ? 'link' : 'links'}`)
        const shown = result.total - result.hidden
        setNotice(
          result.total === 0
            ? `"${start.label}" has no connections in the knowledge graph.`
            : bits.length > 0
              ? `${bits.join(', ')}. ${
                  result.hidden > 0
                    ? `${result.hidden} more connected — click it again or use Show more.`
                    : `Showing all ${result.total} connected.`
                }`
              : result.hidden > 0
                ? `Canvas is full — ${result.hidden} connected entities not shown. Remove some to make room.`
                : `Already showing all ${shown} connected. Click one of them to keep going.`,
        )
      } catch (err) {
        setNotice(err instanceof ApiError ? err.message : 'Could not look up connections')
      } finally {
        setExpanding(null)
      }
    },
    [getDoc, edit],
  )

  /** The user confirmed that a sandbox-only entity is this knowledge-graph entity. */
  const adoptMatch = useCallback(
    (newNodeId: string, entity: Entity) => {
      const from = getDoc().nodes.find((n) => n.id === newNodeId)
      if (!from) return
      const types = validLinkTypes(linkDefs, from.entity_subclass, entity.entity_subclass, classes)
      const linkType = types.some((t) => t.type === 'same_as') ? 'same_as' : null
      edit((d) => addGraphMatch(d, newNodeId, entity, linkType))
      setSelected({ kind: 'node', id: entity.entity_id })
      void expand(entity.entity_id)
    },
    [getDoc, edit, linkDefs, classes, expand],
  )

  /** Click on a sandbox-only entity: look it up in the graph. An unambiguous exact name
   * match is adopted (as a visible, deletable "same as" hypothesis) and its connections
   * followed; anything else is left to the side panel, where the user picks. */
  const followNew = useCallback(
    async (nodeId: string) => {
      const from = getDoc().nodes.find((n) => n.id === nodeId)
      if (!from || from.origin !== 'new') return
      setExpanding(nodeId)
      try {
        const seen = new Map<string, Entity>()
        for (const term of searchTerms(from.label)) {
          for (const e of await api.searchEntities(term)) seen.set(e.entity_id, e)
        }
        const found = rankMatches(from.label, [...seen.values()])
        const pick = pickAutoMatch(from.label, found)
        if (pick) {
          if (getDoc().nodes.some((n) => n.id === pick.entity_id)) {
            setExpanding(null)
            void expand(pick.entity_id) // matched before: just keep following it
          } else {
            adoptMatch(nodeId, pick)
          }
          return
        }
        if (found.length > 0) setToolsCollapsed(false) // the user has to pick: show where
        setNotice(
          found.length === 0
            ? `"${from.label}" is not in the knowledge graph, so there are no connections to follow.`
            : `${found.length} possible ${found.length === 1 ? 'match' : 'matches'} for "${from.label}" - pick one in the side panel.`,
        )
      } catch (err) {
        setNotice(err instanceof ApiError ? err.message : 'Could not look up the knowledge graph')
      } finally {
        setExpanding(null)
      }
    },
    [getDoc, expand, adoptMatch, setToolsCollapsed],
  )

  const explode = useCallback(
    async (nodeId: string) => {
      setExploding(true)
      setNotice('')
      try {
        // Read-only: look up the real, directly-connected entities and copy them inside.
        const scope = await api.getScope(nodeId, 1)
        const added = explodeInto(doc, nodeId, scope.nodes, scope.edges).nodes.length - doc.nodes.length
        if (added > 0) edit((d) => explodeInto(d, nodeId, scope.nodes, scope.edges))
        go(nodeId)
        setNotice(
          added > 0
            ? `Pulled in ${added} connected ${added === 1 ? 'entity' : 'entities'}.`
            : 'Nothing new to pull in: everything connected is already in this sandbox.',
        )
      } catch (err) {
        setNotice(err instanceof ApiError ? err.message : 'Could not look up connected entities')
      } finally {
        setExploding(false)
      }
    },
    [doc, edit, go],
  )

  function onDrop(e: DragEvent) {
    e.preventDefault()
    const raw = e.dataTransfer.getData(DRAG_TYPE)
    if (!raw) return
    try {
      const entity = JSON.parse(raw) as Entity
      void addExisting(entity, screenToFlowPosition({ x: e.clientX, y: e.clientY }))
    } catch {
      /* not one of ours */
    }
  }

  if (loadState === 'loading' || loadState === 'idle')
    return <p className="flex-1 p-6 text-sm text-[var(--color-text-muted)]">Loading sandbox…</p>
  if (loadState === 'error')
    return (
      <div className="flex flex-1 flex-col items-start gap-3 p-6">
        <p role="alert" className="text-sm text-[var(--color-status-destroyed)]">{message}</p>
        <Button onClick={reload}>Retry</Button>
      </div>
    )

  const counts = summarize(doc)
  const selNode = selected?.kind === 'node' ? nodeById.get(selected.id) : undefined
  const selEdge = selected?.kind === 'edge' ? doc.edges.find((e) => e.id === selected.id) : undefined

  return (
    <>
      <section className="flex min-w-0 flex-1 flex-col">
        <div className="flex flex-wrap items-center gap-3 border-b border-[var(--color-border)] px-3 py-2">
          <label htmlFor="sb-title" className="sr-only">Sandbox name</label>
          <input
            id="sb-title"
            className="w-56 rounded-md border border-transparent bg-transparent px-2 py-1 text-sm font-semibold text-[var(--color-text-primary)] hover:border-[var(--color-border)] focus-visible:border-[var(--color-focus)]"
            value={name}
            maxLength={100}
            onChange={(e) => rename(e.target.value)}
          />
          <span
            role="status"
            className={`text-xs ${
              saveState === 'saved' || saveState === 'saving' || saveState === 'unsaved'
                ? 'text-[var(--color-text-muted)]'
                : 'text-[var(--color-status-destroyed)]'
            }`}
          >
            {SAVE_LABEL[saveState]}
          </span>
          <span className="flex-1" />
          <Button
            onClick={() => setSaveOpen(true)}
            disabled={saveState !== 'saved' || counts.newEntities + counts.newLinks === 0}
            title={
              counts.newEntities + counts.newLinks === 0
                ? 'Nothing new to save: add entities or links first'
                : saveState !== 'saved'
                  ? 'Waiting for your changes to be saved'
                  : 'Review and save the new entities and links to the real knowledge graph'
            }
          >
            <Upload size={14} aria-hidden="true" /> Save to graph
          </Button>
          <Button onClick={toggleWide} aria-pressed={wide} title={wide ? 'Show the side panels again' : 'Hide the side panels to widen the canvas'}>
            {wide ? <Minimize size={14} aria-hidden="true" /> : <Maximize size={14} aria-hidden="true" />}
            {wide ? 'Show panels' : 'Wide canvas'}
          </Button>
          {(counts.newEntities + counts.editedEntities + counts.newLinks + counts.editedLinks > 0) && (
            <span className="text-xs text-[var(--color-text-muted)]">
              {[
                counts.newEntities && `${counts.newEntities} new`,
                counts.editedEntities && `${counts.editedEntities} edited`,
                counts.newLinks && `${counts.newLinks} new links`,
                counts.editedLinks && `${counts.editedLinks} changed links`,
              ].filter(Boolean).join(' · ')}
            </span>
          )}
        </div>

        {(saveState === 'conflict' || saveState === 'rejected' || saveState === 'error') && (
          <div role="alert" className="flex items-center gap-3 border-b border-[var(--color-border)] bg-[var(--color-surface-1)] px-3 py-2 text-sm text-[var(--color-status-destroyed)]">
            <span className="flex-1">{message}</span>
            {saveState === 'conflict' ? (
              <Button onClick={reload}>Reload latest</Button>
            ) : (
              <Button onClick={retry}>Retry save</Button>
            )}
          </div>
        )}
        <nav
          aria-label="Level"
          className="flex flex-wrap items-center gap-1 border-b border-[var(--color-border)] px-3 py-1.5 text-xs"
        >
          {here !== null && (
            <Button onClick={goUp} aria-label="Go up one level" className="!px-2 !py-0.5 !text-xs">
              <CornerUpLeft size={12} aria-hidden="true" /> Up
            </Button>
          )}
          <button
            type="button"
            onClick={() => go(null)}
            aria-current={here === null ? 'page' : undefined}
            className={here === null ? 'font-semibold text-[var(--color-text-primary)]' : 'text-[var(--color-focus)] hover:underline'}
          >
            Top level
          </button>
          {trail.map((p, i) => (
            <Fragment key={p.id}>
              <ChevronRight size={12} className="text-[var(--color-text-muted)]" aria-hidden="true" />
              <button
                type="button"
                onClick={() => go(p.id)}
                aria-current={i === trail.length - 1 ? 'page' : undefined}
                className={`max-w-[16rem] truncate ${
                  i === trail.length - 1
                    ? 'font-semibold text-[var(--color-text-primary)]'
                    : 'text-[var(--color-focus)] hover:underline'
                }`}
              >
                {p.name}
              </button>
            </Fragment>
          ))}
          <span className="ms-auto text-[var(--color-text-muted)]">
            Double-click an entity or link to open inside it{here !== null && ' · Esc to go back'}
          </span>
        </nav>
        {notice && <p role="status" className="px-3 py-1 text-xs text-[var(--color-text-muted)]">{notice}</p>}

        <div
          ref={wrapper}
          className="relative min-h-0 flex-1"
          onDragOver={(e) => {
            e.preventDefault()
            e.dataTransfer.dropEffect = 'copy'
          }}
          onDrop={onDrop}
        >
          {here !== null && level.nodes.length === 0 && (
            <p className="pointer-events-none absolute inset-x-0 top-1/3 z-10 mx-auto max-w-xs text-center text-sm text-[var(--color-text-muted)]">
              Nothing inside yet. Add entities from the panel on the right, or drag a search result
              here.
            </p>
          )}
          <ReactFlow
            key={here ?? 'top'}
            nodes={rfNodes}
            edges={rfEdges}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            deleteKeyCode={['Backspace', 'Delete']}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onNodeClick={(_, n) => {
              setSelected({ kind: 'node', id: n.id })
              setPending(null)
              // Clicking ANY entity follows its connections (again on every click): a copied
              // graph entity directly, a sandbox-only one by first finding it in the graph.
              if (expanding !== null) return
              const origin = nodeById.get(n.id)?.origin
              if (origin === 'graph') void expand(n.id)
              else if (origin === 'new') void followNew(n.id)
            }}
            onEdgeClick={(_, e) => {
              setSelected({ kind: 'edge', id: e.id })
              setPending(null)
            }}
            onNodeDoubleClick={(_, n) => go(n.id)}
            onEdgeDoubleClick={(_, e) => go(e.id)}
            onPaneClick={() => {
              setSelected(null)
              setPending(null)
            }}
            connectionMode={ConnectionMode.Loose}
            fitView
            proOptions={{ hideAttribution: true }}
            colorMode="dark"
          >
            <Background color="var(--color-border)" gap={24} />
            <Controls />
          </ReactFlow>
        </div>
        <AssistantPanel
          getCanvas={getDoc}
          applyCanvas={(c) => edit(() => c)}
          container={here}
          disabled={loadState !== 'ready'}
        />
      </section>

      <SaveToGraphDialog open={saveOpen} onOpenChange={setSaveOpen} sandboxId={sandboxId} sandboxName={name} />
      {toolsCollapsed ? (
        <aside
          aria-label="Sandbox tools (hidden)"
          className="flex w-10 shrink-0 flex-col items-center gap-2 border-s border-[var(--color-border)] py-2"
        >
          <PanelToggle label="Show sandbox tools" expanded={false} onClick={() => setToolsCollapsed(false)}>
            <PanelRightOpen size={16} aria-hidden="true" />
          </PanelToggle>
          {selected && (
            <span
              role="img"
              aria-label="Something is selected: open the tools to edit it"
              title="Something is selected: open the tools to edit it"
              className="h-2 w-2 rounded-full bg-[var(--color-focus)]"
            />
          )}
        </aside>
      ) : (
      <aside id="sandbox-tools" className="flex w-72 shrink-0 flex-col gap-4 overflow-y-auto border-s border-[var(--color-border)] p-3" aria-label="Sandbox tools">
        <div className="-mb-2 flex justify-end">
          <PanelToggle label="Hide sandbox tools" expanded onClick={() => setToolsCollapsed(true)} controls="sandbox-tools">
            <PanelRightClose size={16} aria-hidden="true" />
          </PanelToggle>
        </div>
        <AddPanel
          classes={classes}
          onAddExisting={(entity) => addExisting(entity, viewportCentre())}
          onAddNew={(label, subclass) => {
            const { doc: next, id } = addNewEntity(doc, label, subclass, viewportCentre(), here)
            edit(() => next)
            setSelected({ kind: 'node', id })
          }}
        />
        {pending && (
          <ConnectPanel
            doc={doc}
            pending={pending}
            linkDefs={linkDefs}
            classes={classes}
            onCreate={(type) => {
              edit((d) => addEdge(d, pending.source, pending.target, type))
              setPending(null)
            }}
            onCancel={() => setPending(null)}
          />
        )}
        {selNode && (
          <NodePanel
            key={selNode.id}
            node={selNode}
            doc={doc}
            classes={classes}
            linkDefs={linkDefs}
            inside={childCount(doc, selNode.id)}
            onOpen={() => go(selNode.id)}
            onExplode={selNode.origin === 'graph' ? () => void explode(selNode.id) : undefined}
            exploding={exploding}
            onExpand={selNode.origin === 'graph' ? () => void expand(selNode.id) : undefined}
            onShowMore={selNode.origin === 'graph' ? () => void expand(selNode.id) : undefined}
            expanding={expanding === selNode.id}
            onUseMatch={(entity) => adoptMatch(selNode.id, entity)}
            existingIds={new Set(doc.nodes.map((n) => n.id))}
            onChange={(patch) => edit((d) => updateNode(d, selNode.id, patch))}
            onRevert={() => edit((d) => revertNode(d, selNode.id))}
            onRemove={() => {
              edit((d) => removeNode(d, selNode.id))
              setSelected(null)
            }}
          />
        )}
        {selEdge && (
          <EdgePanel
            key={selEdge.id}
            edge={selEdge}
            doc={doc}
            classes={classes}
            linkDefs={linkDefs}
            inside={childCount(doc, selEdge.id)}
            onOpen={() => go(selEdge.id)}
            onChange={(type) => edit((d) => updateEdge(d, selEdge.id, { link_type: type }))}
            onRevert={() => edit((d) => updateEdge(d, selEdge.id, { link_type: selEdge.base_type ?? selEdge.link_type }))}
            onRemove={() => {
              edit((d) => removeEdge(d, selEdge.id))
              setSelected(null)
            }}
          />
        )}
        {!pending && !selNode && !selEdge && (
          <p className="text-xs text-[var(--color-text-muted)]">
            Drag entities to move them. Drag from one entity&apos;s dot to another to add a link. Click an
            entity or link to edit it. Press Delete or Backspace to remove the selection.
          </p>
        )}
      </aside>
      )}
    </>
  )
}

// ---- right panels --------------------------------------------------------------

const heading = 'text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)]'

function AddPanel({
  classes,
  onAddExisting,
  onAddNew,
}: {
  classes: ClassDef[]
  onAddExisting: (e: Entity) => void
  onAddNew: (label: string, subclass: string) => void
}) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<Entity[]>([])
  const [searching, setSearching] = useState(false)
  const [showNew, setShowNew] = useState(false)
  const [label, setLabel] = useState('')
  const [subclass, setSubclass] = useState('')
  const leaves = useLeafClasses(classes)

  useEffect(() => {
    const q = query.trim()
    if (q.length < 2) {
      setResults([])
      return
    }
    setSearching(true)
    const t = setTimeout(() => {
      api.searchEntities(q).then(setResults).catch(() => setResults([])).finally(() => setSearching(false))
    }, 250)
    return () => clearTimeout(t)
  }, [query])

  return (
    <div className="flex flex-col gap-2">
      <h3 className={heading}>Add to sandbox</h3>
      <div className="relative">
        <Search size={14} className="pointer-events-none absolute start-2 top-2.5 text-[var(--color-text-muted)]" aria-hidden="true" />
        <input
          className={`${inputCls} ps-7`}
          placeholder="Search the knowledge graph…"
          aria-label="Search the knowledge graph"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>
      {searching && <p className="text-xs text-[var(--color-text-muted)]">Searching…</p>}
      <ul className="flex max-h-48 flex-col gap-1 overflow-y-auto">
        {results.map((r) => (
          <li
            key={r.entity_id}
            draggable
            onDragStart={(e) => {
              e.dataTransfer.setData(DRAG_TYPE, JSON.stringify(r))
              e.dataTransfer.effectAllowed = 'copy'
            }}
            className="flex cursor-grab items-center justify-between gap-2 rounded-md border border-[var(--color-border)] px-2 py-1 active:cursor-grabbing"
          >
            <span className="min-w-0">
              <span className="block truncate text-sm text-[var(--color-text-primary)]">{r.label}</span>
              <span className="block truncate text-xs text-[var(--color-text-muted)]">{r.entity_subclass}</span>
            </span>
            <button
              type="button"
              aria-label={`Add ${r.label} to the sandbox`}
              className="text-[var(--color-focus)]"
              onClick={() => onAddExisting(r)}
            >
              <Plus size={16} />
            </button>
          </li>
        ))}
      </ul>
      {results.length > 0 && <p className="text-xs text-[var(--color-text-muted)]">Drag a result onto the canvas, or press +.</p>}

      <Button onClick={() => setShowNew((v) => !v)} aria-expanded={showNew}>
        <Plus size={14} aria-hidden="true" /> New entity
      </Button>
      {showNew && (
        <div className="flex flex-col gap-2 rounded-md border border-[var(--color-border)] p-2">
          <label htmlFor="new-label" className="text-xs text-[var(--color-text-muted)]">Name</label>
          <input id="new-label" className={inputCls} value={label} maxLength={200} onChange={(e) => setLabel(e.target.value)} />
          <span className="text-xs text-[var(--color-text-muted)]">Class</span>
          <Select aria-label="Class" value={subclass} onValueChange={setSubclass} options={leaves} placeholder="Choose a class" />
          <Button
            variant="primary"
            disabled={!label.trim() || !subclass}
            onClick={() => {
              onAddNew(label.trim(), subclass)
              setLabel('')
              setShowNew(false)
            }}
          >
            Add to canvas
          </Button>
        </div>
      )}
    </div>
  )
}

function useLeafClasses(classes: ClassDef[]) {
  return useMemo(() => {
    const parents = new Set(classes.map((c) => c.parent_key).filter((k): k is string => k !== null))
    return classes.filter((c) => !parents.has(c.key)).map((c) => ({ value: c.key, label: c.key }))
  }, [classes])
}

function ConnectPanel({
  doc, pending, linkDefs, classes, onCreate, onCancel,
}: {
  doc: { nodes: { id: string; label: string; entity_subclass: string }[] }
  pending: { source: string; target: string }
  linkDefs: LinkDef[]
  classes: ClassDef[]
  onCreate: (type: string) => void
  onCancel: () => void
}) {
  const src = doc.nodes.find((n) => n.id === pending.source)
  const tgt = doc.nodes.find((n) => n.id === pending.target)
  const options = src && tgt ? validLinkTypes(linkDefs, src.entity_subclass, tgt.entity_subclass, classes) : []
  const [type, setType] = useState('')
  return (
    <div className="flex flex-col gap-2 rounded-md border border-[var(--color-focus)] p-2">
      <h3 className={heading}>New link</h3>
      <p className="text-sm text-[var(--color-text-primary)]">
        {src?.label} → {tgt?.label}
      </p>
      {options.length === 0 ? (
        <p role="alert" className="text-xs text-[var(--color-status-destroyed)]">
          The ontology has no link type between these two kinds of entity.
        </p>
      ) : (
        <Select
          aria-label="Link type"
          value={type}
          onValueChange={setType}
          placeholder="Choose a link type"
          options={options.map((o) => ({ value: o.type, label: o.type }))}
        />
      )}
      <div className="flex gap-2">
        <Button variant="primary" disabled={!type} onClick={() => onCreate(type)}>Create link</Button>
        <Button onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  )
}

function NodePanel({
  node, doc, classes, linkDefs, inside, onOpen, onExplode, exploding, onExpand, onShowMore, expanding,
  onUseMatch, existingIds, onChange, onRevert, onRemove,
}: {
  node: SandboxNodeData
  doc: SandboxDoc
  classes: ClassDef[]
  linkDefs: LinkDef[]
  inside: number
  onOpen: () => void
  onExplode?: () => void
  exploding: boolean
  onExpand?: () => void
  onShowMore?: () => void
  expanding: boolean
  onUseMatch: (entity: Entity) => void
  existingIds: Set<string>
  onChange: (patch: Partial<SandboxNodeData>) => void
  onRevert: () => void
  onRemove: () => void
}) {
  const leaves = useLeafClasses(classes)
  const options = leaves.some((o) => o.value === node.entity_subclass)
    ? leaves
    : [{ value: node.entity_subclass, label: node.entity_subclass }, ...leaves]
  const [blocked, setBlocked] = useState('')
  const status = nodeStatus(node)

  function reclassify(next: string) {
    const broken = brokenLinksIfReclassified(doc, node.id, next, linkDefs, classes)
    if (broken.length > 0) {
      setBlocked(
        `That class would invalidate ${broken.length} link${broken.length > 1 ? 's' : ''} (${broken
          .map((b) => b.link_type)
          .join(', ')}). Change or remove them first.`,
      )
      return
    }
    setBlocked('')
    onChange({ entity_subclass: next })
  }

  return (
    <div className="flex flex-col gap-2">
      <h3 className={heading}>Entity {status !== 'original' && `· ${status}`}</h3>
      <label htmlFor="node-label" className="text-xs text-[var(--color-text-muted)]">Name</label>
      <input
        id="node-label"
        className={inputCls}
        value={node.label}
        maxLength={200}
        onChange={(e) => e.target.value.trim() && onChange({ label: e.target.value })}
      />
      <span className="text-xs text-[var(--color-text-muted)]">Class</span>
      <Select aria-label="Class" value={node.entity_subclass} onValueChange={reclassify} options={options} />
      {blocked && <p role="alert" className="text-xs text-[var(--color-status-destroyed)]">{blocked}</p>}
      {node.origin === 'graph' && <p className="font-mono text-xs text-[var(--color-text-muted)]">Copy of {node.id}</p>}
      {onExpand && (
        <div className="flex flex-col gap-1.5 rounded-md border border-[var(--color-border)] p-2">
          <span className="text-xs text-[var(--color-text-muted)]">
            Click this entity on the canvas to follow its connections in the knowledge graph.
          </span>
          <div className="flex gap-2">
            <Button onClick={onExpand} disabled={expanding}>
              {expanding ? 'Looking up…' : 'Show connections'}
            </Button>
            {onShowMore && (
              <Button onClick={onShowMore} disabled={expanding}>
                Show more
              </Button>
            )}
          </div>
        </div>
      )}
      {node.origin === 'new' && (
        <GraphMatches label={node.label} existingIds={existingIds} onUse={onUseMatch} />
      )}
      <OpenInside inside={inside} onOpen={onOpen} onExplode={onExplode} exploding={exploding} />
      <div className="flex gap-2">
        {status === 'edited' && <Button onClick={onRevert}>Revert</Button>}
        <Button onClick={onRemove}><Trash2 size={14} aria-hidden="true" /> Remove</Button>
      </div>
      {inside > 0 && (
        <p className="text-xs text-[var(--color-text-muted)]">
          Removing this also removes the {inside} item{inside > 1 ? 's' : ''} inside it.
        </p>
      )}
    </div>
  )
}

/** For a sandbox-only entity: find entities in the knowledge graph that may be the same
 * thing, so its connections can be followed. Nothing is merged automatically - the
 * user picks a match, which is added next to it (and linked) as a hypothesis. */
function GraphMatches(props: {
  label: string
  existingIds: Set<string>
  onUse: (entity: Entity) => void
}) {
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading')
  const [matches, setMatches] = useState<Entity[]>([])

  useEffect(() => {
    let cancelled = false
    setState('loading')
    ;(async () => {
      try {
        const seen = new Map<string, Entity>()
        for (const term of searchTerms(props.label)) {
          for (const e of await api.searchEntities(term)) seen.set(e.entity_id, e)
          if (seen.size >= 5) break
        }
        if (!cancelled) {
          setMatches(rankMatches(props.label, [...seen.values()]).slice(0, 5))
          setState('ready')
        }
      } catch {
        if (!cancelled) setState('error')
      }
    })()
    return () => {
      cancelled = true
    }
  }, [props.label])

  return (
    <div className="flex flex-col gap-1.5 rounded-md border border-[var(--color-border)] p-2">
      <span className="text-xs font-medium text-[var(--color-text-primary)]">In the knowledge graph</span>
      {state === 'loading' && <span className="text-xs text-[var(--color-text-muted)]">Searching…</span>}
      {state === 'error' && <span role="alert" className="text-xs text-[var(--color-status-destroyed)]">Search failed.</span>}
      {state === 'ready' && matches.length === 0 && (
        <span className="text-xs text-[var(--color-text-muted)]">
          No entity with a similar name, so there are no connections to follow. Add links yourself, or ask the assistant.
        </span>
      )}
      {state === 'ready' && matches.length > 0 && (
        <>
          <span className="text-xs text-[var(--color-text-muted)]">
            Is it one of these? Pick one to add it here and see what it is connected to.
          </span>
          <ul aria-label="Knowledge graph matches" className="flex flex-col gap-1">
            {matches.map((m) => {
              const there = props.existingIds.has(m.entity_id)
              return (
                <li key={m.entity_id}>
                  <button
                    type="button"
                    disabled={there}
                    onClick={() => props.onUse(m)}
                    className="w-full rounded border border-[var(--color-border)] px-2 py-1 text-start text-xs hover:border-[var(--color-focus)] disabled:opacity-50"
                  >
                    <span className="block truncate text-[var(--color-text-primary)]">{m.label}</span>
                    <span className="block truncate text-[var(--color-text-muted)]">
                      {there ? 'Already on the canvas' : m.entity_subclass}
                    </span>
                  </button>
                </li>
              )
            })}
          </ul>
        </>
      )}
    </div>
  )
}

/** Drill into an entity/relation; for a copied graph entity, optionally fill
 * its inside with the real entities it is connected to. */
function OpenInside(props: {
  inside: number
  onOpen: () => void
  onExplode?: () => void
  exploding: boolean
}) {
  return (
    <div className="flex flex-wrap gap-2">
      <Button onClick={props.onOpen}>
        <Maximize2 size={14} aria-hidden="true" /> Open inside{props.inside > 0 ? ` (${props.inside})` : ''}
      </Button>
      {props.onExplode && (
        <Button onClick={props.onExplode} disabled={props.exploding}>
          {props.exploding ? 'Pulling in…' : 'Pull in connected'}
        </Button>
      )}
    </div>
  )
}

function EdgePanel({
  edge, doc, classes, linkDefs, inside, onOpen, onChange, onRevert, onRemove,
}: {
  edge: SandboxEdgeData
  doc: { nodes: { id: string; label: string; entity_subclass: string }[] }
  classes: ClassDef[]
  linkDefs: LinkDef[]
  inside: number
  onOpen: () => void
  onChange: (type: string) => void
  onRevert: () => void
  onRemove: () => void
}) {
  const src = doc.nodes.find((n) => n.id === edge.source)
  const tgt = doc.nodes.find((n) => n.id === edge.target)
  const valid = src && tgt ? validLinkTypes(linkDefs, src.entity_subclass, tgt.entity_subclass, classes) : []
  const options = valid.some((v) => v.type === edge.link_type)
    ? valid.map((v) => v.type)
    : [edge.link_type, ...valid.map((v) => v.type)]
  const status = edgeStatus(edge)
  return (
    <div className="flex flex-col gap-2">
      <h3 className={heading}>Link {status !== 'original' && `· ${status === 'new' ? 'new' : 'changed'}`}</h3>
      <p className="text-sm text-[var(--color-text-primary)]">{src?.label} → {tgt?.label}</p>
      <span className="text-xs text-[var(--color-text-muted)]">Relation</span>
      <Select aria-label="Relation" value={edge.link_type} onValueChange={onChange} options={options.map((o) => ({ value: o, label: o }))} />
      <OpenInside inside={inside} onOpen={onOpen} exploding={false} />
      <div className="flex gap-2">
        {status === 'edited' && <Button onClick={onRevert}>Revert</Button>}
        <Button onClick={onRemove}><Trash2 size={14} aria-hidden="true" /> Remove</Button>
      </div>
      {inside > 0 && (
        <p className="text-xs text-[var(--color-text-muted)]">
          Removing this link also removes the {inside} item{inside > 1 ? 's' : ''} inside it.
        </p>
      )}
    </div>
  )
}
