import React, { useState, useEffect, useMemo, useCallback } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
  type NodeTypes
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import {
  GitCompare,
  Columns,
  Maximize2,
  Filter,
  RefreshCw,
  Info,
  Code2,
  AlertTriangle,
  Layers,
  FileText,
  CheckCircle2,
  HelpCircle
} from 'lucide-react'
import type {
  LinkedGraphResult,
  LinkedGraphNode,
  DocGraphClusterSummary,
  LocalRepo
} from '../../types/electron'
import { applyDagreLayout, applyLaneAlignedLayout } from '../graph/layout'
import { GroupHullNode } from './_shared/GroupHullNode'
import { ExplainPanel } from './_shared/ExplainPanel'
import { linkedNodeStyle, nodeTypeHex, docEdgeVisual } from './_shared/encoding'

type LayerType = 'bd' | 'dd' | 'code'

const nodeTypes: NodeTypes = {
  group: GroupHullNode
}

export function ComparisonScreen(): React.ReactElement {
  // Cluster / snapshot selection — real ids from the backend, never hardcoded.
  const [clusters, setClusters] = useState<DocGraphClusterSummary[]>([])
  const [snapshots, setSnapshots] = useState<string[]>([])
  const [clusterId, setClusterId] = useState<string>('')
  const [snapshotId, setSnapshotId] = useState<string>('')
  const [graphData, setGraphData] = useState<LinkedGraphResult | null>(null)
  const [loading, setLoading] = useState<boolean>(false)
  const [error, setError] = useState<string | null>(null)

  // Exactly-2 layer selection state (default DD + Code)
  const [activeLayers, setActiveLayers] = useState<Set<LayerType>>(new Set(['dd', 'code']))

  // View modes
  const [isSplit, setIsSplit] = useState<boolean>(false)
  const [showOnlyUnmatched, setShowOnlyUnmatched] = useState<boolean>(false)
  const [statusFilter, setStatusFilter] = useState<string>('all')

  // React Flow state
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([])
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null)

  // Layer toggle: max 2 active, min 1. To switch pairs, deselect one (→1) then pick another (→2).
  const toggleLayerChip = (layer: LayerType) => {
    setActiveLayers((prev) => {
      const next = new Set(prev)
      if (next.has(layer)) {
        if (next.size <= 1) return prev // keep at least one active
        next.delete(layer)
      } else {
        if (next.size >= 2) return prev // at most two active
        next.add(layer)
      }
      return next
    })
  }

  // Load clusters + snapshots. A cluster is bound to the snapshot it was validated against —
  // pair with THAT, else the relation comparator returns STALE_INPUT (empty verdicts).
  useEffect(() => {
    window.api.docGraph
      .listClusters()
      .then((cls) => {
        setClusters(cls)
        if (cls.length > 0) {
          const first = cls[0]
          setClusterId((prev) => prev || first.cluster_id)
          if (first.snapshot_id) {
            setSnapshotId(first.snapshot_id) // bound snapshot wins on initial load
            setSnapshots((prev) =>
              prev.includes(first.snapshot_id!) ? prev : [first.snapshot_id!, ...prev]
            )
          }
        }
      })
      .catch((e) => console.error('Failed to list clusters:', e))
    window.api.folder
      .list()
      .then((repos: LocalRepo[]) => {
        const ids = repos
          .map((r) => r.active_snapshot_id)
          .filter((id): id is string => Boolean(id))
        setSnapshots((prev) => Array.from(new Set([...prev, ...ids])))
        if (ids.length > 0) setSnapshotId((prev) => prev || ids[0])
      })
      .catch((e) => console.error('Failed to list repos:', e))
  }, [])

  // Selecting a cluster re-pins its bound snapshot so the pair always matches.
  const handleClusterChange = useCallback(
    (id: string) => {
      setClusterId(id)
      const cl = clusters.find((c) => c.cluster_id === id)
      if (cl?.snapshot_id) {
        setSnapshotId(cl.snapshot_id)
        setSnapshots((prev) => (prev.includes(cl.snapshot_id!) ? prev : [cl.snapshot_id!, ...prev]))
      }
    },
    [clusters]
  )

  // Fetch graph data when layer pair changes
  const fetchData = useCallback(async () => {
    if (!clusterId || !snapshotId) return
    setLoading(true)
    setError(null)
    try {
      const layersParam = Array.from(activeLayers).join(',')
      const res = await window.api.docCode.linkedGraph({
        cluster_id: clusterId,
        snapshot_id: snapshotId,
        layers: layersParam
      })
      setGraphData(res)
    } catch (err: any) {
      console.error('Failed to load comparison linked graph:', err)
      setError(err?.message || 'Failed to fetch comparison dataset')
    } finally {
      setLoading(false)
    }
  }, [clusterId, snapshotId, activeLayers])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  // Fields grouped under their parent program (via `defines`) and hidden from the graph.
  const fieldsByParent = useMemo(() => {
    const m = new Map<string, LinkedGraphNode[]>()
    if (!graphData) return m
    const byId = new Map(graphData.nodes.map((n) => [n.id, n]))
    for (const e of graphData.edges) {
      if (e.edge_type === 'defines') {
        const f = byId.get(e.dst)
        if (f && f.node_type === 'field') {
          const arr = m.get(e.src) ?? []
          arr.push(f)
          m.set(e.src, arr)
        }
      }
    }
    const claimed = new Set<string>()
    m.forEach((arr) => arr.forEach((f) => claimed.add(f.id)))
    for (const n of graphData.nodes) {
      if (n.node_type === 'field' && !claimed.has(n.id) && n.display_name.includes('.')) {
        const progId = `program/${n.display_name.split('.')[0].toUpperCase()}`
        if (byId.has(progId)) {
          const arr = m.get(progId) ?? []
          arr.push(n)
          m.set(progId, arr)
        }
      }
    }
    return m
  }, [graphData])

  // Build merged / split nodes & edges
  useEffect(() => {
    if (!graphData) return

    const { nodes: rawNodes, edges: rawEdges, cross_links: crossLinks } = graphData

    const hasBd = activeLayers.has('bd')
    const hasDd = activeLayers.has('dd')
    const hasCode = activeLayers.has('code')

    // ── CASE 1: BD + DD Dual-Tagged Single Node Set ──
    if (hasBd && hasDd && !hasCode) {
      const filteredNodes = rawNodes.filter((n) => {
        if (statusFilter !== 'all') {
          // Status filter check if needed
        }
        return (n.in_bd || n.in_dd) && n.node_type !== 'field'
      })

      const nodeMap = new Map(filteredNodes.map((n) => [n.id, n]))

      const initialNodes: Node[] = filteredNodes.map((n) => {
        const isMatched = n.in_bd && n.in_dd
        const isBdOnly = n.in_bd && !n.in_dd
        const isDdOnly = !n.in_bd && n.in_dd
        const isUnmatched = !isMatched
        const isDimmed = showOnlyUnmatched && !isUnmatched
        const isSelected = selectedNodeId === `doc:${n.id}` || selectedNodeId === n.id

        return {
          id: `doc:${n.id}`,
          type: 'default',
          position: { x: 0, y: 0 },
          data: {
            label: (
              <div className="flex flex-col gap-0.5 px-1 py-0.5 text-left">
                <div className="flex items-center justify-between gap-1.5">
                  <div className="flex items-center gap-1.5 min-w-0">
                    <span
                      className="inline-block w-2 h-2 rounded-full shrink-0"
                      style={{ background: nodeTypeHex(n.node_type) }}
                    />
                    <span className="font-semibold text-xs text-zinc-100 truncate">{n.display_name}</span>
                  </div>
                  <span className="text-[9px] uppercase px-1 py-0.2 font-mono rounded bg-zinc-800 text-zinc-400">
                    {n.node_type}
                  </span>
                </div>
                <div className="flex items-center gap-1 mt-0.5">
                  {fieldsByParent.has(n.id) && (
                    <span className="text-[9px] font-mono font-bold px-1.5 py-0.2 rounded bg-sky-500/20 text-sky-300 border border-sky-500/30">
                      {fieldsByParent.get(n.id)!.length}f
                    </span>
                  )}
                  {isMatched && (
                    <span className="text-[9px] font-mono font-bold px-1.5 py-0.2 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/40">
                      BOTH (BD+DD)
                    </span>
                  )}
                  {isBdOnly && (
                    <span className="text-[9px] font-mono font-bold px-1.5 py-0.2 rounded bg-blue-500/20 text-blue-300 border border-blue-500/40">
                      BD ONLY
                    </span>
                  )}
                  {isDdOnly && (
                    <span className="text-[9px] font-mono font-bold px-1.5 py-0.2 rounded bg-purple-500/20 text-purple-300 border border-purple-500/40">
                      DD ONLY
                    </span>
                  )}
                </div>
              </div>
            ),
            rawNode: n
          },
          style: {
            ...linkedNodeStyle(n.node_type, {
              selected: isSelected,
              dimmed: isDimmed,
              ringOverride: isMatched ? null : isBdOnly ? '#3b82f6' : '#a855f7'
            }),
            width: 170
          }
        }
      })

      const initialEdges: Edge[] = rawEdges
        .filter((e) => nodeMap.has(e.src) && nodeMap.has(e.dst))
        .map((e) => {
          const v = docEdgeVisual(e, showOnlyUnmatched)
          return {
            id: `edge:${e.edge_key}`,
            source: `doc:${e.src}`,
            target: `doc:${e.dst}`,
            animated: v.animated,
            style: {
              stroke: v.stroke,
              strokeWidth: v.strokeWidth,
              strokeDasharray: v.strokeDasharray,
              opacity: v.opacity
            },
            data: { rawEdge: e }
          }
        })

      const laidOut = applyDagreLayout(initialNodes, initialEdges)
      setNodes(laidOut)
      setEdges(initialEdges)
      return
    }

    // ── CASE 2: DD + Code OR BD + Code Dual Lane View ──
    const isDocBd = hasBd && hasCode
    const docNodesList = rawNodes.filter(
      (n) => (isDocBd ? n.in_bd : n.in_dd) && n.node_type !== 'field'
    )
    const docNodeIdSet = new Set(docNodesList.map((n) => n.id))

    const docFlowNodes: Node[] = docNodesList.map((n) => {
      const flowId = `doc:${n.id}`
      const isMiss = n.assessed && (n.entity_verdict === 'missing' || n.entity_verdict === 'undocumented')
      const isDimmed = showOnlyUnmatched && !isMiss
      const isSelected = selectedNodeId === flowId || selectedNodeId === n.id

      return {
        id: flowId,
        type: 'default',
        position: { x: 0, y: 0 },
        data: {
          label: (
            <div className="flex flex-col gap-0.5 px-1 py-0.5 text-left">
              <div className="flex items-center justify-between gap-1.5">
                <div className="flex items-center gap-1.5 min-w-0">
                  <span
                    className="inline-block w-2 h-2 rounded-full shrink-0"
                    style={{ background: nodeTypeHex(n.node_type) }}
                  />
                  <span className="font-semibold text-xs text-zinc-100 truncate">{n.display_name}</span>
                </div>
                <span className="text-[9px] uppercase px-1 py-0.2 font-mono rounded bg-zinc-800 text-zinc-400">
                  {n.node_type}
                </span>
              </div>
              <div className="flex items-center gap-1 mt-0.5">
                {fieldsByParent.has(n.id) && (
                  <span className="text-[9px] font-mono font-bold px-1 rounded bg-sky-500/20 text-sky-300 border border-sky-500/30">
                    {fieldsByParent.get(n.id)!.length}f
                  </span>
                )}
                <span
                  className={`text-[9px] font-mono font-bold px-1 rounded ${
                    isDocBd ? 'bg-blue-500/20 text-blue-300 border border-blue-500/30' : 'bg-purple-500/20 text-purple-300 border border-purple-500/30'
                  }`}
                >
                  {isDocBd ? 'BD' : 'DD'}
                </span>
                {n.in_code && (
                  <span className="text-[9px] font-mono font-bold px-1 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">
                    CODE
                  </span>
                )}
              </div>
            </div>
          ),
          rawNode: n
        },
        style: {
          ...linkedNodeStyle(n.node_type, {
            selected: isSelected,
            dimmed: isDimmed,
            ringOverride: isMiss ? (n.entity_verdict === 'undocumented' ? '#06b6d4' : '#f59e0b') : null
          }),
          width: 170
        }
      }
    })

    const docFlowEdges: Edge[] = rawEdges
      .filter((e) => docNodeIdSet.has(e.src) && docNodeIdSet.has(e.dst))
      .map((e) => {
        const v = docEdgeVisual(e, showOnlyUnmatched)
        return {
          id: `edge:${e.edge_key}`,
          source: `doc:${e.src}`,
          target: `doc:${e.dst}`,
          animated: v.animated,
          style: {
            stroke: v.stroke,
            strokeWidth: v.strokeWidth,
            strokeDasharray: v.strokeDasharray,
            opacity: v.opacity
          },
          data: { rawEdge: e }
        }
      })

    // Layout doc anchor layer first with Dagre
    const laidOutDoc = applyDagreLayout(docFlowNodes, docFlowEdges)
    let maxDocX = 0
    laidOutDoc.forEach((n) => {
      if (n.position.x > maxDocX) maxDocX = n.position.x
    })

    // Compute Code Lane X based on Split toggle (Merged = 280, Split = 550)
    const laneSep = isSplit ? 550 : 280
    const codeLaneX = maxDocX + laneSep

    // Build Code nodes and cross-layer edges
    const codeNodes: Node[] = []
    const crossEdges: Edge[] = []

    crossLinks.forEach((link) => {
      const codeNodeId = `code-file:${link.code_rel_path}`
      const isAmbiguous = link.candidates.length > 1

      if (!codeNodes.some((cn) => cn.id === codeNodeId)) {
        codeNodes.push({
          id: codeNodeId,
          type: 'default',
          position: { x: codeLaneX, y: 0 },
          data: {
            label: (
              <div className="flex flex-col gap-0.5 px-1 py-0.5 text-left">
                <div className="flex items-center gap-1.5 text-cyan-300 font-semibold text-xs font-mono">
                  <Code2 className="h-3.5 w-3.5 shrink-0" />
                  <span className="truncate max-w-[180px]">{link.code_rel_path.split('/').pop()}</span>
                </div>
                <div className="text-[10px] text-zinc-400 font-mono truncate max-w-[190px]">
                  {link.code_rel_path}
                </div>
                {isAmbiguous && (
                  <div className="flex items-center gap-1 text-[9px] text-amber-400 font-semibold mt-0.5">
                    <AlertTriangle className="h-3 w-3" /> Ambiguous ({link.candidates.length})
                  </div>
                )}
              </div>
            )
          },
          style: {
            ...linkedNodeStyle('code', { ringOverride: isAmbiguous ? '#f59e0b' : null }),
            width: 210
          }
        })
      }

      if (docNodeIdSet.has(link.doc_id)) {
        crossEdges.push({
          id: `cross:${link.doc_id}->${link.code_rel_path}`,
          source: `doc:${link.doc_id}`,
          target: codeNodeId,
          animated: isAmbiguous,
          style: {
            stroke: isAmbiguous ? '#f59e0b' : '#06b6d4',
            strokeWidth: isAmbiguous ? 2 : 1.5,
            strokeDasharray: isAmbiguous ? '4,4' : 'none'
          },
          label: link.match_method === 'name_fallback' ? 'fallback match' : 'exact match'
        })
      }
    })

    // Align code nodes horizontally across from their matched doc anchor nodes
    const alignedResult = applyLaneAlignedLayout(
      laidOutDoc,
      docFlowEdges,
      codeNodes,
      (secNode) => {
        const link = crossLinks.find((cl) => `code-file:${cl.code_rel_path}` === secNode.id)
        return link ? `doc:${link.doc_id}` : null
      },
      codeLaneX,
      { verticalGap: 24 }
    )

    setNodes([...alignedResult.anchorNodes, ...alignedResult.secondaryNodes])
    setEdges([...docFlowEdges, ...crossEdges])
  }, [graphData, activeLayers, isSplit, showOnlyUnmatched, statusFilter, selectedNodeId, fieldsByParent, setNodes, setEdges])

  // Selected item inspection
  const selectedNodeObj = useMemo(() => {
    if (!selectedNodeId || !graphData) return null
    const rawId = selectedNodeId.replace(/^(doc|code-file):/, '')
    return graphData.nodes.find((n) => n.id === rawId) || null
  }, [selectedNodeId, graphData])

  const selectedEdgeObj = useMemo(() => {
    if (!selectedEdgeId || !graphData) return null
    const rawKey = selectedEdgeId.replace(/^edge:/, '')
    return graphData.edges.find((e) => e.edge_key === rawKey) || null
  }, [selectedEdgeId, graphData])

  const selectedCrossLink = useMemo(() => {
    if (!selectedNodeObj || !graphData) return null
    return graphData.cross_links.find((c) => c.doc_id === selectedNodeObj.id) || null
  }, [selectedNodeObj, graphData])

  const selectedNodeFields = useMemo(
    () => (selectedNodeObj ? fieldsByParent.get(selectedNodeObj.id) ?? [] : []),
    [selectedNodeObj, fieldsByParent]
  )

  // Active Pair Header Title
  const pairTitle = useMemo(() => {
    const list = Array.from(activeLayers).map((l) => l.toUpperCase())
    return `Comparing: ${list.join(' ↔ ')}`
  }, [activeLayers])

  // Asymmetric empty check
  const asymmetricMessage = useMemo(() => {
    if (!graphData) return null
    if (activeLayers.has('code')) {
      const codeNodeCount = graphData.cross_links.length
      if (codeNodeCount === 0) {
        return '0 Code nodes matched — showing doc-side specification entities only.'
      }
    }
    return null
  }, [graphData, activeLayers])

  return (
    <div className="flex h-full w-full flex-col bg-zinc-950 text-zinc-100">
      {/* Top Header & Layer Toggle Bar */}
      <div className="flex items-center justify-between border-b border-zinc-800 bg-zinc-900/60 px-5 py-3 shadow-xs">
        {/* Layer Selector Chips (Exactly 2 active) */}
        <div className="flex items-center gap-3">
          <span className="text-xs font-semibold text-zinc-300">{pairTitle}</span>
          <div className="flex items-center gap-1.5 rounded-lg border border-zinc-800 bg-zinc-950 p-1">
            <button
              onClick={() => toggleLayerChip('bd')}
              className={`flex items-center gap-1 rounded px-2.5 py-1 text-xs font-medium transition-all ${
                activeLayers.has('bd')
                  ? 'bg-blue-500/20 text-blue-300 border border-blue-500/40 shadow-xs'
                  : 'text-zinc-500 hover:text-zinc-300'
              }`}
            >
              <FileText className="h-3.5 w-3.5" /> BD
            </button>
            <button
              onClick={() => toggleLayerChip('dd')}
              className={`flex items-center gap-1 rounded px-2.5 py-1 text-xs font-medium transition-all ${
                activeLayers.has('dd')
                  ? 'bg-purple-500/20 text-purple-300 border border-purple-500/40 shadow-xs'
                  : 'text-zinc-500 hover:text-zinc-300'
              }`}
            >
              <Layers className="h-3.5 w-3.5" /> DD
            </button>
            <button
              onClick={() => toggleLayerChip('code')}
              className={`flex items-center gap-1 rounded px-2.5 py-1 text-xs font-medium transition-all ${
                activeLayers.has('code')
                  ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/40 shadow-xs'
                  : 'text-zinc-500 hover:text-zinc-300'
              }`}
            >
              <Code2 className="h-3.5 w-3.5" /> Code
            </button>
          </div>
          <span className="text-[10px] text-zinc-500 font-mono">(Select exactly 2)</span>
        </div>

        {/* View Options & Filters */}
        <div className="flex items-center gap-3">
          {/* Split View Toggle (Disabled for BD+DD since it's a single dual-tagged node set) */}
          {!activeLayers.has('bd') || !activeLayers.has('dd') || activeLayers.has('code') ? (
            <button
              onClick={() => setIsSplit((v) => !v)}
              className={`flex items-center gap-1.5 rounded-lg border px-3 py-1 text-xs font-medium transition-all ${
                isSplit
                  ? 'border-cyan-500/50 bg-cyan-500/20 text-cyan-300 shadow-xs'
                  : 'border-zinc-800 bg-zinc-900 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200'
              }`}
            >
              {isSplit ? <Columns className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
              {isSplit ? 'Split View (Separated)' : 'Merged View (Compact)'}
            </button>
          ) : null}

          {/* Show Only Unmatched Toggle */}
          <button
            onClick={() => setShowOnlyUnmatched((v) => !v)}
            className={`flex items-center gap-1.5 rounded-lg border px-3 py-1 text-xs font-medium transition-all ${
              showOnlyUnmatched
                ? 'border-amber-500/50 bg-amber-500/20 text-amber-300 shadow-xs'
                : 'border-zinc-800 bg-zinc-900 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200'
            }`}
          >
            <Filter className="h-3.5 w-3.5" />
            Show Only Unmatched
          </button>
        </div>
      </div>

      {/* Cluster / Snapshot selectors */}
      <div className="flex items-center gap-2 border-b border-zinc-800/60 bg-zinc-950 px-5 py-2 text-xs">
        <span className="text-zinc-500">Cluster</span>
        <select
          value={clusterId}
          onChange={(e) => handleClusterChange(e.target.value)}
          className="max-w-[240px] rounded-md border border-zinc-800 bg-zinc-900 px-2 py-1 text-zinc-200"
        >
          {clusters.length === 0 && <option value="">No clusters</option>}
          {clusters.map((c) => (
            <option key={c.cluster_id} value={c.cluster_id}>
              {c.cluster_name} ({c.cluster_id.substring(0, 8)}…)
            </option>
          ))}
        </select>
        <span className="ml-2 text-zinc-500">Snapshot</span>
        <select
          value={snapshotId}
          onChange={(e) => setSnapshotId(e.target.value)}
          className="max-w-[260px] rounded-md border border-zinc-800 bg-zinc-900 px-2 py-1 text-zinc-200"
        >
          {snapshots.length === 0 && <option value="">No snapshots</option>}
          {snapshots.map((s) => (
            <option key={s} value={s}>
              {s.substring(0, 12)}…
            </option>
          ))}
        </select>
      </div>

      {/* Asymmetric Empty State Warning Banner */}
      {asymmetricMessage && (
        <div className="flex items-center gap-2 border-b border-amber-500/30 bg-amber-500/10 px-5 py-2 text-xs text-amber-300">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          <span>{asymmetricMessage}</span>
        </div>
      )}

      {/* Main Flow Canvas */}
      <div className="relative flex-1">
        {loading && (
          <div className="absolute inset-0 z-40 flex items-center justify-center bg-zinc-950/80 backdrop-blur-xs">
            <div className="flex items-center gap-3 rounded-lg border border-zinc-800 bg-zinc-900 px-4 py-3 text-xs text-zinc-300 shadow-xl">
              <RefreshCw className="h-4 w-4 animate-spin text-cyan-400" /> Loading comparison dataset...
            </div>
          </div>
        )}

        {error && (
          <div className="absolute inset-4 z-40 flex items-center justify-center">
            <div className="rounded-xl border border-rose-500/30 bg-rose-950/40 p-6 text-center text-rose-300 max-w-md">
              <AlertTriangle className="mx-auto h-8 w-8 text-rose-400 mb-2" />
              <h3 className="font-semibold text-sm">Failed to Load Comparison View</h3>
              <p className="mt-1 text-xs text-rose-400/80">{error}</p>
            </div>
          </div>
        )}

        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          nodeTypes={nodeTypes}
          onNodeClick={(_e, node) => {
            setSelectedNodeId(node.id)
            setSelectedEdgeId(null)
          }}
          onEdgeClick={(_e, edge) => {
            setSelectedEdgeId(edge.id)
            setSelectedNodeId(null)
          }}
          onPaneClick={() => {
            setSelectedNodeId(null)
            setSelectedEdgeId(null)
          }}
          fitView
          fitViewOptions={{ padding: 0.2 }}
        >
          <Background color="#27272a" gap={20} size={1} />
          <Controls className="fill-zinc-400 bg-zinc-900 border-zinc-800" />
          <MiniMap
            nodeColor={(n) => {
              if (n.id.startsWith('code-file:')) return '#06b6d4'
              return '#a855f7'
            }}
            maskColor="rgba(9, 9, 11, 0.7)"
            className="bg-zinc-900 border-zinc-800"
          />
        </ReactFlow>

        {/* Explain Panel Slide-In */}
        <ExplainPanel
          selectedNode={selectedNodeObj}
          selectedEdge={selectedEdgeObj}
          crossLink={selectedCrossLink}
          fields={selectedNodeFields}
          onClose={() => {
            setSelectedNodeId(null)
            setSelectedEdgeId(null)
          }}
        />
      </div>
    </div>
  )
}
