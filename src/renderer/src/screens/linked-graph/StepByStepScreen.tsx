import React, { useState, useEffect, useMemo, useRef, useCallback } from 'react'
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
  Layers,
  ChevronRight,
  ChevronLeft,
  Filter,
  RefreshCw,
  Info,
  ShieldCheck,
  Code2,
  FileText,
  AlertTriangle
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
import { linkedNodeStyle, nodeTypeHex } from './_shared/encoding'

const nodeTypes: NodeTypes = {
  group: GroupHullNode
}

export function StepByStepScreen(): React.ReactElement {
  // Cluster / snapshot selection — real ids come from the backend, never hardcoded.
  const [clusters, setClusters] = useState<DocGraphClusterSummary[]>([])
  const [snapshots, setSnapshots] = useState<string[]>([])
  const [clusterId, setClusterId] = useState<string>('')
  const [snapshotId, setSnapshotId] = useState<string>('')
  const [graphData, setGraphData] = useState<LinkedGraphResult | null>(null)
  const [loading, setLoading] = useState<boolean>(false)
  const [error, setError] = useState<string | null>(null)

  // Step controller (1: DD graph, 2: +BD Hulls, 3: +Code Lane)
  const [step, setStep] = useState<1 | 2 | 3>(1)
  const [showOnlyUnmatched, setShowOnlyUnmatched] = useState<boolean>(false)

  // React Flow state
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([])
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null)

  // Position Cache for DD layout (Decision #1: DD layout computed ONCE, cached permanently)
  const ddPosCacheRef = useRef<Map<string, { x: number; y: number }>>(new Map())

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

  // Fetch graph data
  const fetchData = useCallback(async () => {
    if (!clusterId || !snapshotId) return
    setLoading(true)
    setError(null)
    try {
      const res = await window.api.docCode.linkedGraph({
        cluster_id: clusterId,
        snapshot_id: snapshotId
      })
      // New dataset → drop the cached DD layout so it is recomputed once for this cluster.
      ddPosCacheRef.current = new Map()
      setGraphData(res)
    } catch (err: any) {
      console.error('Failed to load linked graph:', err)
      setError(err?.message || 'Failed to fetch linked graph dataset')
    } finally {
      setLoading(false)
    }
  }, [clusterId, snapshotId])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  // Fields are grouped under their parent program (via `defines` edges) and hidden from the
  // graph to cut clutter — shown on demand as a table in the panel.
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
    // Fallback: fields with no `defines` edge → group by the program in their display name.
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

  // Build Step 1, Step 2, and Step 3 nodes & edges
  useEffect(() => {
    if (!graphData) return

    const { nodes: rawNodes, edges: rawEdges, cross_links: crossLinks, bd_groups: bdGroups } = graphData

    // ── STEP 1: DD Graph Only ──
    const ddNodeList = rawNodes.filter((n) => n.in_dd && n.node_type !== 'field')
    const ddNodeIdSet = new Set(ddNodeList.map((n) => n.id))

    // Compute or retrieve cached DD positions
    if (ddPosCacheRef.current.size === 0 && ddNodeList.length > 0) {
      const initialNodes: Node[] = ddNodeList.map((n) => ({
        id: `dd:${n.id}`,
        type: 'default',
        position: { x: 0, y: 0 },
        data: {
          label: n.display_name,
          rawNode: n
        }
      }))

      const initialEdges: Edge[] = rawEdges
        .filter((e) => ddNodeIdSet.has(e.src) && ddNodeIdSet.has(e.dst))
        .map((e) => ({
          id: `dd-edge:${e.edge_key}`,
          source: `dd:${e.src}`,
          target: `dd:${e.dst}`,
          label: e.edge_type.replace(/^(program|job|step|doc)_/, '').replace(/_(program|job|step|dd|dataset)$/, '')
        }))

      const laidOut = applyDagreLayout(initialNodes, initialEdges)
      const cache = new Map<string, { x: number; y: number }>()
      laidOut.forEach((node) => {
        cache.set(node.id, node.position)
      })
      ddPosCacheRef.current = cache
    }

    const posCache = ddPosCacheRef.current

    // Build DD ReactFlow nodes
    const baseDdFlowNodes: Node[] = ddNodeList.map((n) => {
      const flowId = `dd:${n.id}`
      const pos = posCache.get(flowId) || { x: 0, y: 0 }

      // Only a genuine miss among ASSESSED entity types counts as unmatched. Documentation
      // constructs (br/tbd/ddlimit/actor/field/doc) are not assessed → never flag them.
      const isMiss = n.assessed && (n.entity_verdict === 'missing' || n.entity_verdict === 'undocumented')
      const isDimmedByFilter = showOnlyUnmatched && !isMiss

      // Check selection highlight
      const isSelected = selectedNodeId === flowId || selectedNodeId === n.id

      return {
        id: flowId,
        type: 'default',
        position: pos,
        data: {
          label: (
            <div className="flex flex-col gap-0.5 px-1 py-0.5">
              <div className="flex items-center justify-between gap-1.5">
                <div className="flex items-center gap-1.5 min-w-0">
                  <span
                    className="inline-block w-2 h-2 rounded-full shrink-0"
                    style={{ background: nodeTypeHex(n.node_type) }}
                  />
                  <span className="font-semibold text-xs text-zinc-100 truncate">{n.display_name}</span>
                </div>
                <span className="text-[9px] uppercase px-1 py-0.2 font-mono rounded bg-zinc-800 text-zinc-400 shrink-0">
                  {n.node_type}
                </span>
              </div>
              <div className="flex items-center gap-1 mt-0.5">
                {fieldsByParent.has(n.id) && (
                  <span className="text-[9px] font-mono font-bold px-1 rounded bg-sky-500/20 text-sky-300 border border-sky-500/30">
                    {fieldsByParent.get(n.id)!.length}f
                  </span>
                )}
                {step >= 2 && n.in_bd && (
                  <span className="text-[9px] font-mono font-bold px-1 rounded bg-blue-500/20 text-blue-300 border border-blue-500/30">
                    BD
                  </span>
                )}
                {n.in_dd && (
                  <span className="text-[9px] font-mono font-bold px-1 rounded bg-purple-500/20 text-purple-300 border border-purple-500/30">
                    DD
                  </span>
                )}
                {step === 3 && n.in_code && (
                  <span className="text-[9px] font-mono font-bold px-1 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">
                    CODE
                  </span>
                )}
              </div>
            </div>
          ),
          rawNode: n
        },
        style: linkedNodeStyle(n.node_type, {
          selected: isSelected,
          dimmed: isDimmedByFilter,
          ringOverride: isMiss ? (n.entity_verdict === 'undocumented' ? '#06b6d4' : '#f59e0b') : null
        })
      }
    })

    // Base DD Edges
    const baseDdFlowEdges: Edge[] = rawEdges
      .filter((e) => ddNodeIdSet.has(e.src) && ddNodeIdSet.has(e.dst))
      .map((e) => {
        const edgeId = `dd-edge:${e.edge_key}`
        // Only assessed relations (calls/copies/runs/binds_dd) carry a real verdict; the rest
        // (defines/rule_about/cites/references/accesses) are documentation-internal → neutral.
        const isMiss = e.assessed && e.endpoint_verdict !== 'MATCH'
        const isDimmed = showOnlyUnmatched && !isMiss
        const stroke = !e.assessed
          ? '#3f3f46'
          : e.endpoint_verdict === 'MATCH'
            ? '#10b981'
            : e.endpoint_verdict === 'DOC_ONLY'
              ? '#f59e0b'
              : '#06b6d4'

        return {
          id: edgeId,
          source: `dd:${e.src}`,
          target: `dd:${e.dst}`,
          animated: isMiss && e.endpoint_verdict === 'DOC_ONLY',
          style: {
            stroke,
            strokeWidth: isMiss ? 2.5 : 1,
            strokeDasharray: e.assessed && e.endpoint_verdict === 'UNKNOWN' ? '5,5' : 'none',
            opacity: isDimmed ? 0.15 : e.assessed ? 1 : 0.45
          },
          data: { rawEdge: e }
        }
      })

    if (step === 1) {
      setNodes(baseDdFlowNodes)
      setEdges(baseDdFlowEdges)
      return
    }

    // ── STEP 2: Overlay BD Painted Hulls ──
    const groupHullNodes: Node[] = []

    bdGroups.forEach((group) => {
      // Find positions of member nodes present in the DD layout
      const memberPositions: { x: number; y: number }[] = []
      group.member_dd_ids.forEach((memberId) => {
        const flowId = `dd:${memberId}`
        const pos = posCache.get(flowId)
        if (pos) memberPositions.push(pos)
      })

      if (memberPositions.length > 0) {
        let minX = Infinity
        let minY = Infinity
        let maxX = -Infinity
        let maxY = -Infinity

        const NODE_W = 160
        const NODE_H = 48
        const PADDING = 28

        memberPositions.forEach((p) => {
          if (p.x < minX) minX = p.x
          if (p.y < minY) minY = p.y
          if (p.x + NODE_W > maxX) maxX = p.x + NODE_W
          if (p.y + NODE_H > maxY) maxY = p.y + NODE_H
        })

        const hullWidth = Math.max(220, maxX - minX + PADDING * 2)
        const hullHeight = Math.max(120, maxY - minY + PADDING * 2)

        groupHullNodes.push({
          id: group.group_id,
          type: 'group',
          position: { x: minX - PADDING, y: minY - PADDING },
          data: {
            label: group.label,
            sublabel: 'BD Inferred Hull',
            width: hullWidth,
            height: hullHeight,
            accentColor: '#3b82f6'
          },
          zIndex: -1
        })
      }
    })

    if (step === 2) {
      setNodes([...groupHullNodes, ...baseDdFlowNodes])
      setEdges(baseDdFlowEdges)
      return
    }

    // ── STEP 3: Add Code Lane on the Right ──
    let maxDdX = 0
    posCache.forEach((pos) => {
      if (pos.x > maxDdX) maxDdX = pos.x
    })

    const codeLaneX = maxDdX + 380

    // Build Code nodes from cross_links
    const codeNodes: Node[] = []
    const crossEdges: Edge[] = []

    crossLinks.forEach((link) => {
      const codeNodeId = `code-file:${link.code_rel_path}`
      const isAmbiguous = link.candidates.length > 1

      // Avoid adding duplicate code file nodes
      if (!codeNodes.some((cn) => cn.id === codeNodeId)) {
        codeNodes.push({
          id: codeNodeId,
          type: 'default',
          position: { x: codeLaneX, y: 0 }, // Positioned via applyLaneAlignedLayout below
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

      // Draw cross-layer edge from DD node to Code file
      crossEdges.push({
        id: `cross:${link.doc_id}->${link.code_rel_path}`,
        source: `dd:${link.doc_id}`,
        target: codeNodeId,
        animated: isAmbiguous,
        style: {
          stroke: isAmbiguous ? '#f59e0b' : '#06b6d4',
          strokeWidth: isAmbiguous ? 2 : 1.5,
          strokeDasharray: isAmbiguous ? '4,4' : 'none'
        },
        label: link.match_method === 'name_fallback' ? 'fallback match' : 'exact match'
      })
    })

    // Align code nodes horizontally across from their matched DD anchor nodes
    const alignedResult = applyLaneAlignedLayout(
      baseDdFlowNodes,
      baseDdFlowEdges,
      codeNodes,
      (secNode) => {
        const link = crossLinks.find((cl) => `code-file:${cl.code_rel_path}` === secNode.id)
        return link ? `dd:${link.doc_id}` : null
      },
      codeLaneX,
      { verticalGap: 24 }
    )

    setNodes([...groupHullNodes, ...baseDdFlowNodes, ...alignedResult.secondaryNodes])
    setEdges([...baseDdFlowEdges, ...crossEdges])
  }, [graphData, step, showOnlyUnmatched, selectedNodeId, fieldsByParent, setNodes, setEdges])

  // Selected item inspection
  const selectedNodeObj = useMemo(() => {
    if (!selectedNodeId || !graphData) return null
    const rawId = selectedNodeId.replace(/^(dd|code-file|bd-group):/, '')
    return graphData.nodes.find((n) => n.id === rawId) || null
  }, [selectedNodeId, graphData])

  const selectedEdgeObj = useMemo(() => {
    if (!selectedEdgeId || !graphData) return null
    const rawKey = selectedEdgeId.replace(/^dd-edge:/, '')
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

  // A clicked BD hull → resolve the section and the DD flows it wraps.
  const selectedBdGroup = useMemo(() => {
    if (!selectedNodeId || !selectedNodeId.startsWith('bd-group:') || !graphData) return null
    const g = graphData.bd_groups.find((bg) => bg.group_id === selectedNodeId)
    if (!g) return null
    const byId = new Map(graphData.nodes.map((n) => [n.id, n]))
    return {
      label: g.label,
      section_id: g.section_id,
      members: g.member_dd_ids.map((id) => {
        const n = byId.get(id)
        return { id, display_name: n?.display_name ?? id, node_type: n?.node_type ?? 'unknown' }
      }),
      evidence: g.evidence
    }
  }, [selectedNodeId, graphData])

  return (
    <div className="flex h-full w-full flex-col bg-zinc-950 text-zinc-100">
      {/* Step Navigation Header */}
      <div className="flex items-center justify-between border-b border-zinc-800 bg-zinc-900/60 px-5 py-3 shadow-xs">
        {/* Step Controller Breadcrumbs */}
        <div className="flex items-center gap-2">
          <button
            onClick={() => setStep(1)}
            className={`flex items-center gap-2 rounded-lg px-3 py-1.5 text-xs font-semibold transition-all ${
              step === 1
                ? 'bg-purple-500/20 text-purple-300 border border-purple-500/40 shadow-xs'
                : 'text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200'
            }`}
          >
            <span className="flex h-5 w-5 items-center justify-center rounded-full bg-purple-500/20 text-[10px]">
              1
            </span>
            Step 1: DD Graph Only
          </button>

          <ChevronRight className="h-4 w-4 text-zinc-600" />

          <button
            onClick={() => setStep(2)}
            className={`flex items-center gap-2 rounded-lg px-3 py-1.5 text-xs font-semibold transition-all ${
              step === 2
                ? 'bg-blue-500/20 text-blue-300 border border-blue-500/40 shadow-xs'
                : 'text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200'
            }`}
          >
            <span className="flex h-5 w-5 items-center justify-center rounded-full bg-blue-500/20 text-[10px]">
              2
            </span>
            Step 2: + BD Hulls
          </button>

          <ChevronRight className="h-4 w-4 text-zinc-600" />

          <button
            onClick={() => setStep(3)}
            className={`flex items-center gap-2 rounded-lg px-3 py-1.5 text-xs font-semibold transition-all ${
              step === 3
                ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 shadow-xs'
                : 'text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200'
            }`}
          >
            <span className="flex h-5 w-5 items-center justify-center rounded-full bg-emerald-500/20 text-[10px]">
              3
            </span>
            Step 3: + Code Lane
          </button>
        </div>

        {/* Step Stepper Actions & Toolbar */}
        <div className="flex items-center gap-3">
          <button
            onClick={() => setStep((s) => (s > 1 ? ((s - 1) as 1 | 2 | 3) : s))}
            disabled={step === 1}
            className="flex items-center gap-1 rounded-md border border-zinc-700 bg-zinc-800 px-2.5 py-1 text-xs font-medium text-zinc-300 hover:bg-zinc-700 disabled:opacity-40"
          >
            <ChevronLeft className="h-4 w-4" /> Prev
          </button>
          <button
            onClick={() => setStep((s) => (s < 3 ? ((s + 1) as 1 | 2 | 3) : s))}
            disabled={step === 3}
            className="flex items-center gap-1 rounded-md border border-zinc-700 bg-zinc-800 px-2.5 py-1 text-xs font-medium text-zinc-300 hover:bg-zinc-700 disabled:opacity-40"
          >
            Next <ChevronRight className="h-4 w-4" />
          </button>

          <div className="h-4 w-[1px] bg-zinc-800" />

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
        <button
          onClick={() => fetchData()}
          className="ml-auto rounded-md border border-zinc-700 bg-zinc-800 px-2.5 py-1 font-medium text-zinc-300 hover:bg-zinc-700"
        >
          Reload
        </button>
      </div>

      {/* Derived Legend Banner */}
      <div className="flex items-center justify-between border-b border-zinc-800/60 bg-zinc-900/30 px-5 py-2 text-xs text-zinc-400">
        <div className="flex items-center gap-2">
          <Info className="h-3.5 w-3.5 text-blue-400" />
          <span>
            {step === 1 && 'Showing DD technical specification graph nodes & relations.'}
            {step === 2 && 'BD section group hulls are visually inferred from BD document section structure.'}
            {step === 3 && 'Code lane shows source files matched via exact key or display name fallback.'}
          </span>
        </div>

        {graphData && (
          <div className="flex items-center gap-3 font-mono text-[11px]">
            <span className="text-zinc-400">Nodes: <strong className="text-zinc-200">{nodes.length}</strong></span>
            <span className="text-zinc-400">Edges: <strong className="text-zinc-200">{edges.length}</strong></span>
            <span className="text-zinc-400">BD Groups: <strong className="text-zinc-200">{graphData.bd_groups.length}</strong></span>
          </div>
        )}
      </div>

      {/* Main Flow Canvas */}
      <div className="relative flex-1">
        {loading && (
          <div className="absolute inset-0 z-40 flex items-center justify-center bg-zinc-950/80 backdrop-blur-xs">
            <div className="flex items-center gap-3 rounded-lg border border-zinc-800 bg-zinc-900 px-4 py-3 text-xs text-zinc-300 shadow-xl">
              <RefreshCw className="h-4 w-4 animate-spin text-cyan-400" /> Loading multi-graph dataset...
            </div>
          </div>
        )}

        {error && (
          <div className="absolute inset-4 z-40 flex items-center justify-center">
            <div className="rounded-xl border border-rose-500/30 bg-rose-950/40 p-6 text-center text-rose-300 max-w-md">
              <AlertTriangle className="mx-auto h-8 w-8 text-rose-400 mb-2" />
              <h3 className="font-semibold text-sm">Failed to Load Linked Graph</h3>
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
              if (n.type === 'group') return '#3b82f6'
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
          bdGroup={selectedBdGroup}
          onClose={() => {
            setSelectedNodeId(null)
            setSelectedEdgeId(null)
          }}
        />
      </div>
    </div>
  )
}
