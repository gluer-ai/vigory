import { useCallback, useEffect, useState } from 'react'
import { BrowsePage } from './components/browse/BrowsePage'
import { Canvas } from './components/graph/Canvas'
import { ScopeListView } from './components/graph/ScopeListView'
import { AppShell } from './components/layout/AppShell'
import { EmptyState, ErrorState, LoadingState } from './components/layout/CanvasStates'
import { AddResourceDialog } from './components/layout/AddResourceDialog'
import { IngestDialog } from './components/layout/IngestDialog'
import { Inspector } from './components/layout/Inspector'
import { LeftRail } from './components/layout/LeftRail'
import { Drawer } from './components/ui/Drawer'
import { api, ApiError } from './lib/api'
import type { Entity, RelevanceAnnotation, ScopeResponse } from './lib/types'

function App() {
  const [page, setPage] = useState<'scope' | 'browse'>('scope')
  const [triggerEntityId, setTriggerEntityId] = useState('')
  const [hops, setHops] = useState(2)
  const [linkTypeFilter, setLinkTypeFilter] = useState<string[]>([])
  const [view, setView] = useState<'canvas' | 'list'>('canvas')

  const [scope, setScope] = useState<ScopeResponse | null>(null)
  const [status, setStatus] = useState<'idle' | 'loading' | 'error' | 'ready'>('idle')
  const [errorMessage, setErrorMessage] = useState('')

  const [selectedEntity, setSelectedEntity] = useState<Entity | null>(null)
  const [ingestOpen, setIngestOpen] = useState(false)
  const [addResourceOpen, setAddResourceOpen] = useState(false)

  const [annotations, setAnnotations] = useState<Record<string, RelevanceAnnotation>>({})
  const [explainStatus, setExplainStatus] = useState<'idle' | 'loading' | 'error' | 'done'>('idle')
  const [explainError, setExplainError] = useState('')

  const fetchScope = useCallback(
    (id: string) => {
      if (!id.trim()) {
        setScope(null)
        setStatus('idle')
        return
      }
      setStatus('loading')
      setAnnotations({})
      setExplainStatus('idle')
      api
        .getScope(id.trim(), hops, linkTypeFilter)
        .then((res) => {
          setScope(res)
          setStatus('ready')
        })
        .catch((err) => {
          setErrorMessage(err instanceof ApiError ? err.message : 'Failed to reach the backend')
          setStatus('error')
        })
    },
    [hops, linkTypeFilter],
  )

  useEffect(() => {
    fetchScope(triggerEntityId)
  }, [fetchScope, triggerEntityId])

  const handleSelectEntity = useCallback(
    (entityId: string) => {
      api.getEntity(entityId).then(setSelectedEntity).catch(() => setSelectedEntity(null))
      if (entityId !== triggerEntityId) setTriggerEntityId(entityId)
    },
    [triggerEntityId],
  )

  function handleBrowseSelectEntity(entityId: string) {
    setPage('scope')
    handleSelectEntity(entityId)
  }

  function handleIngestCommitted(firstEntityId: string) {
    // Leave the dialog open on its "committed" confirmation screen — the
    // user closes it themselves via "View in graph", which is when we
    // actually want the canvas to jump to the new trigger entity.
    setTriggerEntityId(firstEntityId)
  }

  function handleResourceCreated(entityId: string) {
    setAddResourceOpen(false)
    setTriggerEntityId(entityId)
  }

  async function handleExplain() {
    if (!triggerEntityId.trim()) return
    setExplainStatus('loading')
    try {
      const res = await api.explainScope(triggerEntityId.trim(), hops)
      const map: Record<string, RelevanceAnnotation> = {}
      for (const a of res.annotations) map[a.entity_id] = a
      setAnnotations(map)
      setExplainStatus('done')
    } catch (err) {
      setExplainError(err instanceof ApiError ? err.message : 'Failed to reach the backend')
      setExplainStatus('error')
    }
  }

  let canvasContent
  if (status === 'idle') canvasContent = <EmptyState />
  else if (status === 'loading') canvasContent = <LoadingState />
  else if (status === 'error')
    canvasContent = <ErrorState message={errorMessage} onRetry={() => fetchScope(triggerEntityId)} />
  else if (scope && view === 'canvas')
    canvasContent = (
      <Canvas
        triggerEntityId={scope.trigger_entity_id}
        nodes={scope.nodes}
        edges={scope.edges}
        onNodeClick={handleSelectEntity}
        annotations={annotations}
      />
    )
  else if (scope && view === 'list')
    canvasContent = (
      <ScopeListView
        triggerEntityId={scope.trigger_entity_id}
        nodes={scope.nodes}
        edges={scope.edges}
        onSelect={handleSelectEntity}
        annotations={annotations}
      />
    )

  return (
    <>
      <AppShell
        leftRail={
          <LeftRail
            page={page}
            onPageChange={setPage}
            triggerEntityId={triggerEntityId}
            onTriggerChange={setTriggerEntityId}
            hops={hops}
            onHopsChange={setHops}
            view={view}
            onViewChange={setView}
            linkTypeFilter={linkTypeFilter}
            onLinkTypeFilterChange={setLinkTypeFilter}
            onOpenIngest={() => setIngestOpen(true)}
            onOpenAddResource={() => setAddResourceOpen(true)}
            onExplain={handleExplain}
            explainStatus={explainStatus}
            explainError={explainError}
            hasScope={status === 'ready'}
          />
        }
        canvas={page === 'browse' ? <BrowsePage onSelectEntity={handleBrowseSelectEntity} /> : canvasContent}
        drawer={
          <Drawer
            open={selectedEntity !== null}
            onOpenChange={(open) => !open && setSelectedEntity(null)}
            title="Entity inspector"
          >
            {selectedEntity && (
              <Inspector entity={selectedEntity} annotation={annotations[selectedEntity.entity_id]} />
            )}
          </Drawer>
        }
      />
      <IngestDialog
        open={ingestOpen}
        onOpenChange={setIngestOpen}
        onCommitted={handleIngestCommitted}
      />
      <AddResourceDialog
        open={addResourceOpen}
        onOpenChange={setAddResourceOpen}
        onCreated={handleResourceCreated}
      />
    </>
  )
}

export default App
