import {
  Background,
  Controls,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
  useReactFlow,
  type Edge,
  type Node,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useEffect, useRef } from 'react'
import type { Entity, Link, RelevanceAnnotation } from '../../lib/types'
import { EntityNode, type EntityNodeData } from './EntityNode'
import { RelationEdge, type RelationEdgeData } from './RelationEdge'

const nodeTypes = { entity: EntityNode }
const edgeTypes = { relation: RelationEdge }

interface CanvasProps {
  triggerEntityId: string
  nodes: Entity[]
  edges: Link[]
  onNodeClick: (entityId: string) => void
  annotations?: Record<string, RelevanceAnnotation>
}

/** Initial radial position for a node the first time it appears — after
 * that, wherever the user dragged it to wins (see the effect below). */
function initialPosition(index: number, total: number, isTrigger: boolean) {
  if (isTrigger) return { x: 0, y: 0 }
  const radius = 260
  const angle = (index / Math.max(total, 1)) * 2 * Math.PI
  return { x: radius * Math.cos(angle), y: radius * Math.sin(angle) }
}

export function Canvas(props: CanvasProps) {
  return (
    <ReactFlowProvider>
      <CanvasInner {...props} />
    </ReactFlowProvider>
  )
}

function CanvasInner({
  triggerEntityId,
  nodes: entities,
  edges: links,
  onNodeClick,
  annotations,
}: CanvasProps) {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node<EntityNodeData>>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge<RelationEdgeData>>([])
  const { fitView } = useReactFlow()
  const lastFittedTrigger = useRef<string | null>(null)

  // Rebuild from the latest scope/annotations, but reuse each existing
  // node's current (possibly user-dragged) position instead of resetting it
  // to the radial layout — dragging should stick across re-scoping/explain.
  useEffect(() => {
    const others = entities.filter((e) => e.entity_id !== triggerEntityId)
    setNodes((current) => {
      const positionById = new Map(current.map((n) => [n.id, n.position]))
      return entities.map((e) => {
        const isTrigger = e.entity_id === triggerEntityId
        const idx = others.findIndex((o) => o.entity_id === e.entity_id)
        return {
          id: e.entity_id,
          type: 'entity',
          position:
            positionById.get(e.entity_id) ?? initialPosition(idx, others.length, isTrigger),
          data: { ...e, isTrigger, relevance: annotations?.[e.entity_id]?.relevance },
        }
      })
    })
  }, [entities, triggerEntityId, annotations, setNodes])

  useEffect(() => {
    setEdges(
      links.map((l) => ({
        id: l.link_id,
        source: l.source_entity,
        target: l.target_entity,
        type: 'relation',
        data: { link_type: l.link_type },
      })),
    )
  }, [links, setEdges])

  // Only recenter the camera when the trigger entity actually changes (a
  // genuinely new subgraph) — not on every hop-depth/filter/explain update,
  // which would otherwise silently undo a user's manual drag layout.
  useEffect(() => {
    if (entities.length === 0 || lastFittedTrigger.current === triggerEntityId) return
    lastFittedTrigger.current = triggerEntityId
    const id = requestAnimationFrame(() => fitView({ duration: 200 }))
    return () => cancelAnimationFrame(id)
  }, [triggerEntityId, entities.length, fitView])

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      onNodeClick={(_, node) => onNodeClick(node.id)}
      proOptions={{ hideAttribution: true }}
      colorMode="dark"
    >
      <Background color="var(--color-border)" gap={24} />
      <Controls />
    </ReactFlow>
  )
}
