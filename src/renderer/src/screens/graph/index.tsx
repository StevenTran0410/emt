import React, { useState, useEffect, useCallback, useMemo } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import {
  ReactFlow,
  Background,
  Controls,
  type Node,
  type NodeChange,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { AlertCircle, Save, Zap, X } from 'lucide-react'
import { Button, PageLoading, useToastStore } from '../../components/ui'
import type {
  ExportJson,
  CommunitiesResponse,
  NeighborResult,
  BlastRadiusResponse,
  ImpactState,
  FileSymbolEdgesResponse,
} from './types'
import { MAX_NODES_DISPLAY, buildFlowGraph } from './utils'
import { applyDagreLayout, applyForceClusterLayout } from './layout'
import { LeftPanel } from './LeftPanel'
import { Legend } from './Legend'

export default function GraphScreen(): React.ReactElement {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const toast = useToastStore()
  const snapshotId = searchParams.get('snapshotId') ?? ''

  const [graphData, setGraphData] = useState<ExportJson | null>(null)
  const [communityData, setCommunityData] = useState<CommunitiesResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selectedNode, setSelectedNode] = useState<string | null>(null)
  const [neighborData, setNeighborData] = useState<NeighborResult | null>(null)
  const [neighborLoading, setNeighborLoading] = useState(false)
  const [symbolEdgesData, setSymbolEdgesData] = useState<FileSymbolEdgesResponse | null>(null)
  const [symbolEdgesLoading, setSymbolEdgesLoading] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [layoutMode, setLayoutMode] = useState<'cluster' | 'hierarchical'>('cluster')
  const [impactState, setImpactState] = useState<ImpactState>({ active: false, seedFiles: [], result: null })
  const [impactLoading, setImpactLoading] = useState(false)

  const loadGraph = useCallback(async () => {
    if (!snapshotId) {
      setError('No snapshot ID provided')
      return
    }

    setLoading(true)
    setError(null)
    try {
      const [exported, communities] = await Promise.all([
        window.api.graph.exportData(snapshotId),
        window.api.graph.communities(snapshotId),
      ])
      setGraphData(exported)
      setCommunityData(communities)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load graph data')
    } finally {
      setLoading(false)
    }
  }, [snapshotId])

  useEffect(() => {
    if (snapshotId) {
      loadGraph()
    }
  }, [snapshotId, loadGraph])

  const onNodeClick = useCallback(
    async (_: React.MouseEvent, node: Node) => {
      const path = node.id
      setSelectedNode(path)
      if (!snapshotId) return

      if (impactState.active) {
        // Impact mode: add this node as a seed file and re-run blast radius
        const newSeedFiles = impactState.seedFiles.includes(path)
          ? impactState.seedFiles
          : [...impactState.seedFiles, path]
        setImpactState((prev) => ({ ...prev, seedFiles: newSeedFiles }))
        setImpactLoading(true)
        try {
          const result = await window.api.impact.blastRadius({
            snapshot_id: snapshotId,
            changed_files: newSeedFiles,
            max_hops: 3,
          }) as BlastRadiusResponse
          setImpactState((prev) => ({ ...prev, result }))
        } catch {
          toast.error('Impact analysis failed')
        } finally {
          setImpactLoading(false)
        }
        return
      }

      setNeighborLoading(true)
      try {
        const res = await window.api.graph.neighbors(snapshotId, path, 2, 100)
        setNeighborData(res)
      } catch {
        setNeighborData(null)
      } finally {
        setNeighborLoading(false)
      }

      setSymbolEdgesLoading(true)
      try {
        const res = await window.api.graph.symbolEdges(snapshotId, path)
        setSymbolEdgesData(res)
      } catch {
        // Not every file has function-level data — treat as "nothing to show", not an error.
        setSymbolEdgesData(null)
      } finally {
        setSymbolEdgesLoading(false)
      }
    },
    [snapshotId, impactState.active, impactState.seedFiles, toast]
  )

  const nodeIndex = graphData?.communities ?? {}
  const communityCount = Object.keys(graphData?.community_groups ?? {}).length

  const { nodes: rawNodes, edges } = useMemo(() => {
    if (!graphData) return { nodes: [], edges: [] }

    const cycleNodes = new Set<string>()
    graphData.cycles.forEach((cycle) => {
      cycle.forEach((path) => cycleNodes.add(path))
    })

    const neighborSet = new Set(neighborData?.nodes ?? [])
    if (selectedNode) neighborSet.add(selectedNode)

    const cappedNodes = graphData.nodes.slice(0, MAX_NODES_DISPLAY)
    const cappedNodeSet = new Set(cappedNodes)
    const cappedData =
      graphData.nodes.length > MAX_NODES_DISPLAY
        ? {
            ...graphData,
            nodes: cappedNodes,
            edges: graphData.edges.filter((e) => cappedNodeSet.has(e.src) && cappedNodeSet.has(e.dst)),
          }
        : graphData

    return buildFlowGraph(cappedData, nodeIndex, selectedNode, neighborSet, cycleNodes, impactState.active ? impactState.result : null)
  }, [graphData, nodeIndex, selectedNode, neighborData, impactState.active, impactState.result])

  const layoutNodes = useMemo(() => {
    if (rawNodes.length === 0) return []
    return layoutMode === 'cluster' ? applyForceClusterLayout(rawNodes, edges) : applyDagreLayout(rawNodes, edges)
  }, [rawNodes, edges, layoutMode])

  // User-dragged positions override the auto-layout, keyed by node id; cleared when
  // layoutMode changes so switching layouts doesn't keep stale drags around.
  const [manualPositions, setManualPositions] = useState<Record<string, { x: number; y: number }>>({})
  useEffect(() => {
    setManualPositions({})
  }, [layoutMode])

  const nodes = useMemo(() => {
    if (Object.keys(manualPositions).length === 0) return layoutNodes
    return layoutNodes.map((node) =>
      manualPositions[node.id] ? { ...node, position: manualPositions[node.id] } : node
    )
  }, [layoutNodes, manualPositions])

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    setManualPositions((prev) => {
      let next = prev
      for (const change of changes) {
        if (change.type === 'position' && change.position) {
          if (next === prev) next = { ...prev }
          next[change.id] = change.position
        }
      }
      return next
    })
  }, [])

  const fitViewOptions = { padding: 0.1 }

  const unresolvedStats = useMemo(() => {
    if (!graphData?.edges) return { total: 0, symbolic: 0, systemCopybook: 0, notInSnapshot: 0, externalRuntime: 0 }
    let symbolic = 0
    let systemCopybook = 0
    let notInSnapshot = 0
    let externalRuntime = 0

    for (const e of graphData.edges) {
      if (e.external) {
        const cls = e.resolution_class || ''
        if (cls === 'unresolved_symbolic') symbolic++
        else if (cls === 'system_copybook') systemCopybook++
        else if (cls === 'external_runtime') externalRuntime++
        else if (cls === 'not_in_snapshot') notInSnapshot++
      }
    }

    return {
      total: symbolic + systemCopybook + notInSnapshot,
      symbolic,
      systemCopybook,
      notInSnapshot,
      externalRuntime
    }
  }, [graphData])

  if (!snapshotId) {
    return (
      <div className="h-full flex items-center justify-center bg-zinc-950">
        <div className="text-center space-y-4">
          <AlertCircle size={32} className="text-zinc-500 mx-auto" />
          <div className="text-zinc-400">No snapshot ID provided</div>
          <Button variant="primary" onClick={() => navigate('/index-overview')}>
            Go back
          </Button>
        </div>
      </div>
    )
  }

  if (loading) {
    return <PageLoading label="Loading graph..." />
  }

  if (error || !graphData || !communityData) {
    return (
      <div className="h-full flex items-center justify-center bg-zinc-950">
        <div className="text-center space-y-4">
          <AlertCircle size={32} className="text-red-500 mx-auto" />
          <div className="text-red-400">{error || 'Failed to load graph'}</div>
          <Button variant="primary" onClick={() => loadGraph()}>
            Retry
          </Button>
        </div>
      </div>
    )
  }

  const totalNodes = graphData?.nodes.length ?? 0
  const nodeCountWarning = totalNodes > MAX_NODES_DISPLAY

  return (
    <div className="flex h-full bg-zinc-950">
      {/* Left panel */}
      <LeftPanel
        selectedNode={selectedNode}
        graphData={graphData}
        communityData={communityData}
        neighborData={neighborData}
        neighborLoading={neighborLoading}
        impactState={impactState}
        impactLoading={impactLoading}
        symbolEdgesData={symbolEdgesData}
        symbolEdgesLoading={symbolEdgesLoading}
      />

      {/* Main canvas area */}
      <div className="flex-1 flex flex-col relative">
        {/* Header */}
        <div className="px-4 py-3 border-b border-zinc-700 bg-zinc-900 flex items-center justify-between shrink-0 flex-wrap gap-2">
          <div className="flex items-center gap-3">
            <div className="text-xs text-zinc-300">
              Graph ({nodes.length} nodes, {edges.length} edges)
              {nodeCountWarning && (
                <span className="ml-2 text-amber-400">
                  (showing {MAX_NODES_DISPLAY} of {totalNodes} nodes)
                </span>
              )}
            </div>
            {unresolvedStats.total > 0 && (
              <div className="px-2.5 py-0.5 bg-amber-500/10 border border-amber-500/30 rounded text-[11px] text-amber-300 font-mono">
                Unresolved references: {unresolvedStats.total} (symbolic: {unresolvedStats.symbolic}, system-copybook: {unresolvedStats.systemCopybook}, not-in-snapshot: {unresolvedStats.notInSnapshot})
              </div>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setLayoutMode((m) => (m === 'cluster' ? 'hierarchical' : 'cluster'))}
            >
              {layoutMode === 'cluster' ? 'Layout: Clustered' : 'Layout: Hierarchical'}
            </Button>
            <Button
              variant={impactState.active ? 'primary' : 'secondary'}
              size="sm"
              onClick={() => {
                if (impactState.active) {
                  setImpactState({ active: false, seedFiles: [], result: null })
                } else {
                  setImpactState((prev) => ({ ...prev, active: true }))
                }
              }}
            >
              {impactState.active ? <X size={11} /> : <Zap size={11} />}
              {impactState.active ? 'Exit Impact Mode' : 'Impact Mode'}
            </Button>
            <Button
              variant="secondary"
              size="sm"
              onClick={async () => {
                setExporting(true)
                try {
                  const result = await window.api.graph.exportJson(snapshotId)
                  if (result.saved) {
                    toast.success('Graph JSON exported')
                  }
                } catch {
                  toast.error('Export failed')
                } finally {
                  setExporting(false)
                }
              }}
              loading={exporting}
            >
              <Save size={11} />
              Export JSON
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => navigate(-1)}
            >
              Back
            </Button>
          </div>
        </div>

        {/* React Flow canvas */}
        <div className="flex-1 relative" style={{ height: 'calc(100% - 3rem)' }}>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodeClick={onNodeClick}
            onNodesChange={onNodesChange}
            nodesDraggable
            fitView
            fitViewOptions={fitViewOptions}
            minZoom={0.05}
            maxZoom={2}
          >
            <Background />
            <Controls />
            <Legend communityCount={communityCount} impactActive={impactState.active} />
          </ReactFlow>
        </div>
      </div>
    </div>
  )
}
