import {
  Background,
  BaseEdge,
  Controls,
  EdgeLabelRenderer,
  Handle,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useInternalNode,
  useNodesState,
  useStore,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useMemo, useRef, useState } from 'react'
import { classMeta } from '../../lib/entityClass'
import { layoutSpheres, neighbourhood, type SphereData } from '../../lib/sphereGraph'

interface SphereNodeData {
  label: string
  entity_subclass: string
  kind: 'new' | 'existing'
  diameter: number
  /** false = dimmed because something else is selected */
  lit: boolean
  [key: string]: unknown
}

const leafName = (subclass: string) => subclass.split('.').slice(1).join(' › ') || subclass

/** One entity as a round sphere: coloured by class, dashed when it will be newly created,
 * solid when it is an existing record. The name sits underneath so it never has to fit. */
function Sphere({ data, selected }: NodeProps & { data: SphereNodeData }) {
  const root = data.entity_subclass.split('.')[0]
  const { icon: Icon, colorVar } = classMeta(root, data.entity_subclass)
  const color = `var(${colorVar})`
  const d = data.diameter
  const iconSize = Math.round(d * 0.34)
  // Zoomed out, names would shrink to nothing: grow them (up to 2x) so they stay readable.
  const zoom = useStore((st) => st.transform[2])
  const boost = Math.min(2, Math.max(1, 0.8 / zoom))
  return (
    <div
      className="flex flex-col items-center"
      style={{ width: d, opacity: data.lit ? 1 : 0.28, transition: 'opacity 120ms' }}
      role="group"
      aria-label={`${data.label}, ${leafName(data.entity_subclass)}, ${data.kind === 'new' ? 'will be created' : 'existing record'}`}
    >
      <Handle type="target" position={Position.Top} className="!pointer-events-none !opacity-0" />
      <div
        className="flex items-center justify-center rounded-full"
        style={{
          width: d,
          height: d,
          background: `radial-gradient(circle at 32% 28%, color-mix(in srgb, ${color} 55%, white) 0%, color-mix(in srgb, ${color} 70%, transparent) 38%, color-mix(in srgb, ${color} 22%, var(--color-surface-1)) 100%)`,
          border: `2px ${data.kind === 'new' ? 'dashed' : 'solid'} ${selected ? 'var(--color-focus)' : color}`,
          boxShadow: selected
            ? `0 0 0 3px color-mix(in srgb, var(--color-focus) 45%, transparent), 0 6px 18px rgba(0,0,0,.45)`
            : `0 6px 16px rgba(0,0,0,.4), inset -6px -8px 14px rgba(0,0,0,.28)`,
        }}
      >
        <Icon size={iconSize} style={{ color: 'rgba(255,255,255,.92)' }} aria-hidden="true" />
      </div>
      <span
        className="mt-1.5 text-center font-medium leading-tight text-[var(--color-text-primary)]"
        style={{ width: Math.max(d, 150), fontSize: `${12 * boost}px`, overflowWrap: 'anywhere' }}
        title={data.label}
      >
        {data.label}
      </span>
      <span className="text-[10px] text-[var(--color-text-muted)]">{data.kind === 'new' ? 'new' : 'existing'}</span>
      <Handle type="source" position={Position.Bottom} className="!pointer-events-none !opacity-0" />
    </div>
  )
}

interface LineData {
  link_type: string
  lit: boolean
  /** diameter of the sphere at each end, so the line stops at its edge */
  sourceDiameter: number
  targetDiameter: number
  [key: string]: unknown
}

/** Where a sphere's centre is: its node box is (diameter wide) with the circle on top. */
function centreOf(internal: ReturnType<typeof useInternalNode>, diameter: number) {
  if (!internal) return null
  const { x, y } = internal.internals.positionAbsolute
  return { x: x + diameter / 2, y: y + diameter / 2 }
}

/** A straight line between two spheres, running from edge to edge (not centre to centre) so
 * the arrowhead is visible, with the relation written on it. */
function Line({ id, source, target, data, selected }: EdgeProps & { data: LineData }) {
  const a = centreOf(useInternalNode(source), data.sourceDiameter)
  const b = centreOf(useInternalNode(target), data.targetDiameter)
  if (!a || !b) return null
  const dx = b.x - a.x
  const dy = b.y - a.y
  const len = Math.hypot(dx, dy)
  if (len < 1) return null
  const ux = dx / len
  const uy = dy / len
  const startPad = data.sourceDiameter / 2 + 2
  const endPad = data.targetDiameter / 2 + 5
  if (len <= startPad + endPad) return null // spheres touching: nothing to draw between them
  const x1 = a.x + ux * startPad
  const y1 = a.y + uy * startPad
  const x2 = b.x - ux * endPad
  const y2 = b.y - uy * endPad
  const color = selected ? 'var(--color-focus)' : 'var(--color-border-strong)'
  return (
    <>
      <BaseEdge
        id={id}
        path={`M ${x1} ${y1} L ${x2} ${y2}`}
        markerEnd="url(#sphere-arrow)"
        style={{ stroke: color, strokeWidth: data.lit ? 1.8 : 1, opacity: data.lit ? 1 : 0.2 }}
      />
      <EdgeLabelRenderer>
        <div
          className="pointer-events-none absolute rounded bg-[var(--color-surface-1)] px-1.5 py-0.5 font-mono text-[10px] text-[var(--color-text-muted)]"
          style={{ transform: `translate(-50%, -50%) translate(${(x1 + x2) / 2}px, ${(y1 + y2) / 2}px)`, opacity: data.lit ? 1 : 0.25 }}
        >
          {data.link_type}
        </div>
      </EdgeLabelRenderer>
    </>
  )
}

const nodeTypes = { sphere: Sphere }
const edgeTypes = { line: Line }

function build(data: SphereData, placed: ReturnType<typeof layoutSpheres>, previous: Node<SphereNodeData>[] = []): Node<SphereNodeData>[] {
  const dragged = new Map(previous.map((n) => [n.id, n.position]))
  return data.nodes.map((n) => {
    const p = placed.find((q) => q.id === n.id)!
    return {
      ...previous.find((c) => c.id === n.id),
      id: n.id,
      type: 'sphere',
      position: dragged.get(n.id) ?? { x: p.x - p.diameter / 2, y: p.y - p.diameter / 2 },
      data: { label: n.label, entity_subclass: n.entity_subclass, kind: n.kind, diameter: p.diameter, lit: true },
    }
  })
}

function Inner({ data }: { data: SphereData }) {
  const [selected, setSelected] = useState<string | null>(null)
  const placed = useMemo(() => layoutSpheres(data), [data])
  const focus = useMemo(() => neighbourhood(data, selected), [data, selected])
  const initial = useRef<Node<SphereNodeData>[] | null>(null)
  if (initial.current === null) initial.current = build(data, placed)
  const [nodes, , onNodesChange] = useNodesState<Node<SphereNodeData>>(initial.current)

  const shown = useMemo(
    () => nodes.map((n) => ({ ...n, data: { ...n.data, lit: focus === null || focus.nodes.has(n.id) } })),
    [nodes, focus],
  )
  const diameters = useMemo(() => new Map(placed.map((p) => [p.id, p.diameter])), [placed])
  const edges: Edge<LineData>[] = useMemo(
    () =>
      data.edges.map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        type: 'line',
        data: {
          link_type: e.link_type,
          lit: focus === null || focus.edges.has(e.id),
          sourceDiameter: diameters.get(e.source) ?? 72,
          targetDiameter: diameters.get(e.target) ?? 72,
        },
      })),
    [data.edges, focus, diameters],
  )

  return (
    <ReactFlow
      nodes={shown}
      edges={edges}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      onNodesChange={onNodesChange}
      onNodeClick={(_, n) => setSelected((cur) => (cur === n.id ? null : n.id))}
      onPaneClick={() => setSelected(null)}
      nodesConnectable={false}
      fitView
      fitViewOptions={{ padding: 0.12, maxZoom: 1.1 }}
      edgesFocusable={false}
      minZoom={0.15}
      maxZoom={2.5}
      proOptions={{ hideAttribution: true }}
      colorMode="dark"
    >
      <svg width="0" height="0" aria-hidden="true">
        <defs>
          <marker id="sphere-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--color-border-strong)" />
          </marker>
        </defs>
      </svg>
      <Background gap={24} size={1} />
      <Controls showInteractive={false} />
    </ReactFlow>
  )
}

const signature = (d: SphereData) => `${d.nodes.map((n) => n.id).join(',')}|${d.edges.map((e) => e.id).join(',')}`

/** The proposal as a picture: round entities joined by labelled lines. Click a sphere to
 * highlight what it is connected to; drag spheres to rearrange; scroll to zoom. */
export function SphereView({ data }: { data: SphereData }) {
  if (data.nodes.length === 0) {
    return <p className="p-4 text-sm text-[var(--color-text-muted)]">Nothing to draw yet.</p>
  }
  return (
    <div className="h-[min(64vh,640px)] min-h-[380px] w-full overflow-hidden rounded-md border border-[var(--color-border)]" aria-label="Proposal as a graph of spheres and links">
      {/* Re-created when the graph itself changes, so it frames the new picture. */}
      <ReactFlowProvider key={signature(data)}>
        <Inner data={data} />
      </ReactFlowProvider>
    </div>
  )
}
