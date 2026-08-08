import React, { useState, useMemo, useCallback, useEffect, useRef } from 'react'
import {
  FileText,
  Upload,
  X,
  RefreshCw,
  CheckCircle,
  Layers,
  ChevronDown,
  ChevronRight,
  ShieldAlert,
  Sparkles,
  Plus,
  ArrowLeft,
  History,
  Eye,
  Filter,
  Trash2
} from 'lucide-react'
import {
  ReactFlow,
  Background,
  Controls,
  type Node,
  type Edge,
  type NodeProps,
  Handle,
  Position
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'

import { applyDagreLayout } from '../graph/layout'
import { Button, Spinner, ErrorBanner } from '../../components/ui'
import type {
  DocGraphSummary,
  DocGraphExport,
  DocNode,
  DocEdge,
  DocMismatch,
  DocGraphClusterSummary
} from '../../types/electron'

function basename(pathStr: string): string {
  const parts = pathStr.replace(/\\/g, '/').split('/')
  return parts[parts.length - 1] || pathStr
}

function getNodeStyle(nodeType: string): { bg: string; border: string; text: string; dot: string } {
  switch (nodeType) {
    case 'program':
      return {
        bg: 'bg-indigo-950/90',
        border: 'border-indigo-500/80',
        text: 'text-indigo-200',
        dot: 'bg-indigo-400'
      }
    case 'dataset':
      return {
        bg: 'bg-emerald-950/90',
        border: 'border-emerald-500/80',
        text: 'text-emerald-200',
        dot: 'bg-emerald-400'
      }
    case 'step':
      return {
        bg: 'bg-sky-950/90',
        border: 'border-sky-500/80',
        text: 'text-sky-200',
        dot: 'bg-sky-400'
      }
    case 'copybook':
      return {
        bg: 'bg-violet-950/90',
        border: 'border-violet-500/80',
        text: 'text-violet-200',
        dot: 'bg-violet-400'
      }
    case 'job':
      return {
        bg: 'bg-amber-950/90',
        border: 'border-amber-500/80',
        text: 'text-amber-200',
        dot: 'bg-amber-400'
      }
    case 'extroutine':
      return {
        bg: 'bg-rose-950/90',
        border: 'border-rose-500/80',
        text: 'text-rose-200',
        dot: 'bg-rose-400'
      }
    case 'br':
      return {
        bg: 'bg-teal-950/90',
        border: 'border-teal-500/80',
        text: 'text-teal-200',
        dot: 'bg-teal-400'
      }
    case 'tbd':
    case 'ddlimit':
      return {
        bg: 'bg-orange-950/90',
        border: 'border-orange-500/80',
        text: 'text-orange-200',
        dot: 'bg-orange-400'
      }
    case 'capability':
      return {
        bg: 'bg-fuchsia-950/90',
        border: 'border-fuchsia-500/80',
        text: 'text-fuchsia-200',
        dot: 'bg-fuchsia-400'
      }
    case 'actor':
      return {
        bg: 'bg-slate-800/90',
        border: 'border-slate-500/80',
        text: 'text-slate-200',
        dot: 'bg-slate-400'
      }
    case 'dd':
    case 'doc':
    default:
      return {
        bg: 'bg-zinc-800/90',
        border: 'border-zinc-600/80',
        text: 'text-zinc-200',
        dot: 'bg-zinc-400'
      }
  }
}

function CustomDocNodeComponent({ data }: NodeProps): React.ReactElement {
  const label = String(data.label || '')
  const type = String(data.nodeType || 'node')
  const style = getNodeStyle(type)

  return (
    <div
      className={`px-3 py-1.5 rounded-lg border shadow-md flex items-center gap-2 cursor-pointer transition-all hover:scale-105 ${style.bg} ${style.border} ${style.text}`}
      style={{ minWidth: '140px' }}
    >
      <Handle type="target" position={Position.Left} className="w-2 h-2 !bg-zinc-400" />
      <span className={`w-2 h-2 rounded-full shrink-0 ${style.dot}`} />
      <div className="flex flex-col min-w-0 flex-1">
        <span className="text-xs font-semibold truncate leading-tight">{label}</span>
        <span className="text-[10px] opacity-75 font-mono uppercase truncate">{type}</span>
      </div>
      <Handle type="source" position={Position.Right} className="w-2 h-2 !bg-zinc-400" />
    </div>
  )
}

const nodeTypesConfig = {
  customDocNode: CustomDocNodeComponent
}

export default function DocParsingScreen(): React.ReactElement {
  const [ddPaths, setDdPaths] = useState<string[]>([])
  const [bdPaths, setBdPaths] = useState<string[]>([])
  const [llmEnabled, setLlmEnabled] = useState<boolean>(true)
  const [parsing, setParsing] = useState<boolean>(false)
  const [llmProgress, setLlmProgress] = useState<{ done: number; total: number } | null>(null)
  const [error, setError] = useState<string | null>(null)

  const [summary, setSummary] = useState<DocGraphSummary | null>(null)
  const [exportData, setExportData] = useState<DocGraphExport | null>(null)

  const [showAllNodes, setShowAllNodes] = useState<boolean>(true)
  const [selectedNode, setSelectedNode] = useState<DocNode | null>(null)
  const [activeMismatchTab, setActiveMismatchTab] = useState<'deterministic' | 'llm'>(
    'deterministic'
  )
  const [expandedMismatchFps, setExpandedMismatchFps] = useState<Set<string>>(new Set())

  const [recentClusters, setRecentClusters] = useState<DocGraphClusterSummary[]>([])
  const [loadingClusters, setLoadingClusters] = useState<boolean>(false)

  const streamHandlerRef = useRef<((evt: any) => void) | null>(null)

  const loadRecentClusters = useCallback(async () => {
    try {
      setLoadingClusters(true)
      const list = await window.api.docGraph.listClusters()
      setRecentClusters(list || [])
    } catch {
      // quiet fallback
    } finally {
      setLoadingClusters(false)
    }
  }, [])

  useEffect(() => {
    loadRecentClusters()
  }, [loadRecentClusters])

  const cleanupStreamListener = useCallback(() => {
    if (streamHandlerRef.current) {
      window.api.docGraph.offStreamEvent(streamHandlerRef.current)
      streamHandlerRef.current = null
    }
  }, [])

  useEffect(() => {
    return () => {
      cleanupStreamListener()
    }
  }, [cleanupStreamListener])

  const handlePickFiles = async (target: 'dd' | 'bd') => {
    try {
      const picked = await window.api.docGraph.pickFiles()
      if (picked.length === 0) return

      if (target === 'dd') {
        setDdPaths((prev) => Array.from(new Set([...prev, ...picked])))
      } else {
        setBdPaths((prev) => Array.from(new Set([...prev, ...picked])))
      }
    } catch (e: any) {
      setError(e?.message || 'Failed to pick files')
    }
  }

  const handleRemoveFile = (target: 'dd' | 'bd', pathToRemove: string) => {
    if (target === 'dd') {
      setDdPaths((prev) => prev.filter((p) => p !== pathToRemove))
    } else {
      setBdPaths((prev) => prev.filter((p) => p !== pathToRemove))
    }
  }

  const canStartParsing = ddPaths.length >= 1 && bdPaths.length === 1 && !parsing

  const handleStartParsing = async () => {
    if (!canStartParsing) return
    setParsing(true)
    setLlmProgress(null)
    setError(null)

    cleanupStreamListener()

    const onEvent = async (evt: any) => {
      if (!evt || typeof evt !== 'object') return

      if (evt.type === 'deterministic_done') {
        setSummary(evt.summary)
        try {
          const exp = await window.api.docGraph.exportJson(evt.summary.cluster_id)
          setExportData(exp)
        } catch {
          setExportData({
            summary: evt.summary,
            nodes: [],
            edges: [],
            mismatches: evt.mismatches || [],
            assertions: []
          })
        }
      } else if (evt.type === 'llm_progress') {
        setLlmProgress({ done: evt.done, total: evt.total })
      } else if (evt.type === 'llm_finding') {
        if (evt.mismatch) {
          setExportData((prev) => {
            if (!prev) return prev
            const existing = new Set(prev.mismatches.map((m) => m.fingerprint))
            if (existing.has(evt.mismatch.fingerprint)) return prev
            return {
              ...prev,
              mismatches: [...prev.mismatches, evt.mismatch]
            }
          })
        }
      } else if (evt.type === 'done') {
        setSummary(evt.summary)
        setParsing(false)
        setLlmProgress(null)
        cleanupStreamListener()
        loadRecentClusters()
      } else if (evt.type === 'error') {
        setError(evt.message || 'DocGraph build failed')
        setParsing(false)
        setLlmProgress(null)
        cleanupStreamListener()
      }
    }

    streamHandlerRef.current = onEvent
    window.api.docGraph.onStreamEvent(onEvent)

    try {
      const allFiles = [...ddPaths, ...bdPaths]
      await window.api.docGraph.buildStream({
        files: allFiles,
        force_rebuild: true,
        llm_enabled: llmEnabled
      })
    } catch (e: any) {
      const msg = e?.response?.data?.detail || e?.message || 'Failed to start streaming build'
      setError(typeof msg === 'string' ? msg : JSON.stringify(msg))
      setParsing(false)
      setLlmProgress(null)
      cleanupStreamListener()
    }
  }

  const handleOpenCluster = async (clusterId: string) => {
    try {
      setError(null)
      const exp = await window.api.docGraph.exportJson(clusterId)
      const sum = await window.api.docGraph.summary(clusterId)
      setSummary(sum)
      setExportData(exp)
    } catch (e: any) {
      setError(e?.message || 'Failed to load cluster')
    }
  }

  const handleDeleteCluster = async (e: React.MouseEvent, clusterId: string) => {
    e.stopPropagation()
    if (!window.confirm('Delete this parse permanently?')) return

    try {
      await window.api.docGraph.deleteCluster(clusterId)
      if (summary?.cluster_id === clusterId) {
        handleReset()
      } else {
        loadRecentClusters()
      }
    } catch (err: any) {
      setError(err?.message || 'Failed to delete cluster')
    }
  }

  const handleReset = () => {
    cleanupStreamListener()
    setSummary(null)
    setExportData(null)
    setSelectedNode(null)
    setError(null)
    setParsing(false)
    setLlmProgress(null)
    loadRecentClusters()
  }

  // Compute connected vs isolated nodes
  const { connectedNodeIds, isolatedNodesGrouped, totalIsolatedCount } = useMemo(() => {
    if (!exportData || !exportData.nodes) {
      return { connectedNodeIds: new Set<string>(), isolatedNodesGrouped: {}, totalIsolatedCount: 0 }
    }

    const connectedIds = new Set<string>()
    for (const e of exportData.edges || []) {
      connectedIds.add(e.src_node_id)
      connectedIds.add(e.dst_node_id)
    }

    const isolatedGrouped: Record<string, DocNode[]> = {}
    let isolatedCount = 0

    for (const n of exportData.nodes) {
      if (!connectedIds.has(n.id)) {
        isolatedCount++
        const t = n.node_type || 'other'
        if (!isolatedGrouped[t]) isolatedGrouped[t] = []
        isolatedGrouped[t].push(n)
      }
    }

    return {
      connectedNodeIds: connectedIds,
      isolatedNodesGrouped: isolatedGrouped,
      totalIsolatedCount: isolatedCount
    }
  }, [exportData])

  // Build flow graph nodes & edges
  const flowNodesAndEdges = useMemo(() => {
    if (!exportData || !exportData.nodes) return { nodes: [], edges: [], presentNodeTypes: [] }

    const filteredNodes = showAllNodes
      ? exportData.nodes
      : exportData.nodes.filter((n) => connectedNodeIds.has(n.id) && n.node_type !== 'field')

    const rawNodes = filteredNodes.slice(0, 400)
    const validNodeIds = new Set(rawNodes.map((n) => n.id))

    const initialNodes: Node[] = rawNodes.map((n) => ({
      id: n.id,
      type: 'customDocNode',
      data: { label: n.display_name, nodeType: n.node_type, rawNode: n },
      position: { x: 0, y: 0 }
    }))

    const initialEdges: Edge[] = exportData.edges
      .filter((e) => validNodeIds.has(e.src_node_id) && validNodeIds.has(e.dst_node_id))
      .map((e) => ({
        id: `${e.src_node_id}->${e.dst_node_id}:${e.edge_type}`,
        source: e.src_node_id,
        target: e.dst_node_id,
        label: e.edge_type,
        style: { stroke: '#64748b', strokeWidth: 1.5 },
        animated: false
      }))

    const layoutedNodes = applyDagreLayout(initialNodes, initialEdges)
    const presentNodeTypes = Array.from(new Set(rawNodes.map((n) => n.node_type))).sort()

    return { nodes: layoutedNodes, edges: initialEdges, presentNodeTypes }
  }, [exportData, showAllNodes, connectedNodeIds])

  const handleNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      const rawNode = (node.data?.rawNode as DocNode) || null
      setSelectedNode(rawNode)
    },
    []
  )

  const deterministicMismatches = useMemo(() => {
    if (!exportData?.mismatches) return []
    return exportData.mismatches
      .filter((m) => m.derivation === 'deterministic')
      .sort((a, b) => {
        const order = { error: 0, warning: 1, info: 2 }
        return (order[a.severity] ?? 3) - (order[b.severity] ?? 3)
      })
  }, [exportData])

  const llmMismatches = useMemo(() => {
    if (!exportData?.mismatches) return []
    return exportData.mismatches
      .filter((m) => m.derivation === 'llm')
      .sort((a, b) => {
        const order = { error: 0, warning: 1, info: 2 }
        return (order[a.severity] ?? 3) - (order[b.severity] ?? 3)
      })
  }, [exportData])

  const toggleMismatchExpand = (fp: string) => {
    setExpandedMismatchFps((prev) => {
      const next = new Set(prev)
      if (next.has(fp)) next.delete(fp)
      else next.add(fp)
      return next
    })
  }

  // -------------------------------------------------------------------------
  // Render Results View
  // -------------------------------------------------------------------------
  if (summary && exportData) {
    const displayedMismatches =
      activeMismatchTab === 'deterministic' ? deterministicMismatches : llmMismatches

    return (
      <div className="p-6 space-y-6 max-w-7xl mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold text-zinc-100 flex items-center gap-2">
              <FileText className="w-5 h-5 text-indigo-400" />
              Document Graph Results: {summary.cluster_name}
            </h1>
            <p className="text-xs text-zinc-400 mt-1">
              Parsed {summary.document_count} files into {summary.node_count} nodes &{' '}
              {summary.edge_count} edges with {exportData.mismatches.length} mismatches.
            </p>
          </div>
          <Button variant="secondary" size="sm" onClick={handleReset} className="gap-2">
            <ArrowLeft className="w-4 h-4" />
            Re-parse / Edit Files
          </Button>
        </div>

        {/* Live LLM Progress Indicator */}
        {parsing && (
          <div className="bg-indigo-950/60 border border-indigo-500/40 rounded-xl p-3 flex items-center justify-between text-xs text-indigo-200">
            <div className="flex items-center gap-2.5">
              <Spinner size="sm" />
              <Sparkles className="w-4 h-4 text-indigo-400" />
              <span className="font-semibold">Checking LLM citation consistency...</span>
              {llmProgress && (
                <span className="bg-indigo-900/80 px-2 py-0.5 rounded text-indigo-300 font-mono">
                  {llmProgress.done} / {llmProgress.total} citations
                </span>
              )}
            </div>
            <span className="text-indigo-400/80 italic">Graph is ready to view below</span>
          </div>
        )}

        {/* (a) Summary Tiles */}
        <div className="grid grid-cols-4 gap-4">
          <div className="bg-zinc-800/40 border border-zinc-700/80 rounded-xl p-4 flex flex-col">
            <span className="text-xs font-medium text-zinc-400 uppercase tracking-wider">
              Documents
            </span>
            <span className="text-2xl font-bold text-zinc-100 mt-1">
              {summary.document_count}
            </span>
          </div>
          <div className="bg-zinc-800/40 border border-zinc-700/80 rounded-xl p-4 flex flex-col">
            <span className="text-xs font-medium text-zinc-400 uppercase tracking-wider">
              Nodes
            </span>
            <span className="text-2xl font-bold text-indigo-400 mt-1">
              {summary.node_count}
            </span>
          </div>
          <div className="bg-zinc-800/40 border border-zinc-700/80 rounded-xl p-4 flex flex-col">
            <span className="text-xs font-medium text-zinc-400 uppercase tracking-wider">
              Edges
            </span>
            <span className="text-2xl font-bold text-sky-400 mt-1">
              {summary.edge_count}
            </span>
          </div>
          <div className="bg-zinc-800/40 border border-zinc-700/80 rounded-xl p-4 flex flex-col">
            <span className="text-xs font-medium text-zinc-400 uppercase tracking-wider">
              Assertions
            </span>
            <span className="text-2xl font-bold text-emerald-400 mt-1">
              {summary.assertion_count}
            </span>
          </div>
        </div>

        {/* Severity Summary Bar */}
        <div className="bg-zinc-800/40 border border-zinc-700/80 rounded-xl p-4 flex items-center justify-between text-sm">
          <div className="flex items-center gap-4">
            <span className="font-semibold text-zinc-200">Mismatch Breakdown:</span>
            <div className="flex items-center gap-2">
              <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-rose-500/20 text-rose-300 border border-rose-500/30">
                {exportData.mismatches.filter((m) => m.severity === 'error').length} Error
              </span>
              <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-amber-500/20 text-amber-300 border border-amber-500/30">
                {exportData.mismatches.filter((m) => m.severity === 'warning').length} Warning
              </span>
              <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-zinc-500/20 text-zinc-300 border border-zinc-500/30">
                {exportData.mismatches.filter((m) => m.severity === 'info').length} Info
              </span>
            </div>
          </div>
          <div className="flex items-center gap-3 text-xs text-zinc-400">
            <span>
              Structural:{' '}
              <strong className="text-zinc-200">{deterministicMismatches.length}</strong>
            </span>
            <span>•</span>
            <span>
              Semantic (LLM):{' '}
              <strong className="text-indigo-300">{llmMismatches.length}</strong>
            </span>
          </div>
        </div>

        {/* (b) Doc-Graph Visualization */}
        <div className="bg-zinc-800/40 border border-zinc-700/80 rounded-xl overflow-hidden relative flex flex-col space-y-0">
          <div className="px-4 py-3 border-b border-zinc-700/80 flex items-center justify-between bg-zinc-900/60">
            <div className="flex items-center gap-3">
              <div className="flex items-center gap-2">
                <Layers className="w-4 h-4 text-indigo-400" />
                <span className="font-semibold text-sm text-zinc-200">
                  Document Graph Visualization
                </span>
              </div>

              {/* Show All Nodes Toggle */}
              <button
                onClick={() => setShowAllNodes((v) => !v)}
                className={`px-2.5 py-1 rounded text-xs font-medium border flex items-center gap-1.5 transition-colors ${
                  showAllNodes
                    ? 'bg-indigo-900/60 text-indigo-200 border-indigo-500/50'
                    : 'bg-zinc-800 text-zinc-400 border-zinc-700 hover:text-zinc-200'
                }`}
                title="Toggle between connected structural nodes vs all nodes including floating annotations"
              >
                <Filter className="w-3 h-3" />
                <span>Show all nodes</span>
                {totalIsolatedCount > 0 && (
                  <span className="px-1.5 py-0.2 rounded-full text-[10px] bg-zinc-900 text-zinc-300 font-mono">
                    +{totalIsolatedCount} isolated
                  </span>
                )}
              </button>
            </div>

            {/* Legend */}
            <div className="flex items-center gap-3 text-xs flex-wrap">
              {flowNodesAndEdges.presentNodeTypes.map((type) => {
                const st = getNodeStyle(type)
                return (
                  <div key={type} className="flex items-center gap-1.5">
                    <span className={`w-2.5 h-2.5 rounded-full ${st.dot}`} />
                    <span className="text-zinc-300 font-mono text-[11px] uppercase">{type}</span>
                  </div>
                )
              })}
            </div>
          </div>

          <div className="h-[480px] w-full relative">
            <ReactFlow
              nodes={flowNodesAndEdges.nodes}
              edges={flowNodesAndEdges.edges}
              nodeTypes={nodeTypesConfig}
              onNodeClick={handleNodeClick}
              fitView
              colorMode="dark"
            >
              <Background color="#334155" gap={16} />
              <Controls />
            </ReactFlow>

            {/* Selected Node Panel */}
            {selectedNode && (
              <div className="absolute bottom-4 right-4 w-80 bg-zinc-900/95 border border-zinc-700 rounded-xl p-4 shadow-xl backdrop-blur text-xs space-y-3 z-10">
                <div className="flex items-center justify-between border-b border-zinc-800 pb-2">
                  <div className="flex items-center gap-2 truncate">
                    <span
                      className={`w-2.5 h-2.5 rounded-full shrink-0 ${getNodeStyle(selectedNode.node_type).dot}`}
                    />
                    <span className="font-bold text-zinc-100 truncate">
                      {selectedNode.display_name}
                    </span>
                  </div>
                  <button
                    onClick={() => setSelectedNode(null)}
                    className="text-zinc-400 hover:text-zinc-200"
                  >
                    <X className="w-4 h-4" />
                  </button>
                </div>

                <div>
                  <span className="text-zinc-500 font-medium">Type:</span>
                  <span className="ml-2 font-mono text-zinc-300 uppercase">
                    {selectedNode.node_type}
                  </span>
                </div>

                {Object.keys(selectedNode.attributes || {}).length > 0 && (
                  <div>
                    <span className="text-zinc-500 font-medium block mb-1">Attributes:</span>
                    <pre className="bg-zinc-950 p-2 rounded text-[10px] text-zinc-300 overflow-x-auto max-h-32 font-mono">
                      {JSON.stringify(selectedNode.attributes, null, 2)}
                    </pre>
                  </div>
                )}

                {selectedNode.provenance && selectedNode.provenance.length > 0 && (
                  <div>
                    <span className="text-zinc-500 font-medium block mb-1">Provenance:</span>
                    <div className="space-y-1 max-h-28 overflow-y-auto">
                      {selectedNode.provenance.map((p, idx) => (
                        <div key={idx} className="bg-zinc-950/60 p-1.5 rounded text-[11px] text-zinc-300">
                          <div><strong className="text-zinc-400">Doc:</strong> {p.doc_id}</div>
                          {p.doc_span && <div><strong className="text-zinc-400">Section:</strong> {p.doc_span.section_id} (L{p.doc_span.line_start})</div>}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Compact Isolated Annotation Nodes Panel */}
          {totalIsolatedCount > 0 && !showAllNodes && (
            <div className="p-3 bg-zinc-900/80 border-t border-zinc-700/80 space-y-2 text-xs">
              <div className="flex items-center justify-between text-zinc-400 font-medium">
                <span className="flex items-center gap-1.5">
                  <Filter className="w-3.5 h-3.5 text-zinc-400" />
                  Isolated Annotation Nodes ({totalIsolatedCount} nodes hidden from graph):
                </span>
                <span className="text-[11px] text-zinc-500">Click a node to inspect attributes</span>
              </div>

              <div className="flex flex-wrap gap-3 max-h-32 overflow-y-auto pr-1">
                {Object.entries(isolatedNodesGrouped).map(([type, nodes]) => (
                  <div key={type} className="bg-zinc-950/70 p-2 rounded-lg border border-zinc-800 space-y-1">
                    <div className="flex items-center gap-1.5 text-[11px] font-mono text-zinc-400 uppercase font-semibold">
                      <span className={`w-2 h-2 rounded-full ${getNodeStyle(type).dot}`} />
                      <span>{type} ({nodes.length})</span>
                    </div>
                    <div className="flex flex-wrap gap-1 max-w-md">
                      {nodes.slice(0, 15).map((n) => (
                        <button
                          key={n.id}
                          onClick={() => setSelectedNode(n)}
                          className="px-1.5 py-0.5 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-[10px] font-mono truncate max-w-[140px] border border-zinc-700/60"
                          title={n.display_name}
                        >
                          {n.display_name}
                        </button>
                      ))}
                      {nodes.length > 15 && (
                        <span className="text-[10px] text-zinc-500 self-center">
                          +{nodes.length - 15} more
                        </span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* (c) Mismatch Findings */}
        <div className="bg-zinc-800/40 border border-zinc-700/80 rounded-xl overflow-hidden">
          <div className="px-4 py-3 border-b border-zinc-700/80 flex items-center justify-between bg-zinc-900/60">
            <div className="flex items-center gap-2">
              <ShieldAlert className="w-4 h-4 text-amber-400" />
              <span className="font-semibold text-sm text-zinc-200">Consistency Findings</span>
            </div>

            {/* Tabs */}
            <div className="flex items-center gap-1 bg-zinc-950 p-1 rounded-lg border border-zinc-800">
              <button
                onClick={() => setActiveMismatchTab('deterministic')}
                className={`px-3 py-1 rounded-md text-xs font-medium transition-colors flex items-center gap-1.5 ${
                  activeMismatchTab === 'deterministic'
                    ? 'bg-zinc-800 text-zinc-100 shadow'
                    : 'text-zinc-400 hover:text-zinc-200'
                }`}
              >
                <span>Structural</span>
                <span className="px-1.5 py-0.2 rounded-full text-[10px] bg-zinc-700 text-zinc-200 font-bold">
                  {deterministicMismatches.length}
                </span>
              </button>

              <button
                onClick={() => setActiveMismatchTab('llm')}
                className={`px-3 py-1 rounded-md text-xs font-medium transition-colors flex items-center gap-1.5 ${
                  activeMismatchTab === 'llm'
                    ? 'bg-indigo-900/60 text-indigo-200 border border-indigo-500/40 shadow'
                    : 'text-zinc-400 hover:text-zinc-200'
                }`}
              >
                <Sparkles className="w-3 h-3 text-indigo-400" />
                <span>Semantic (LLM)</span>
                <span className="px-1.5 py-0.2 rounded-full text-[10px] bg-indigo-950 text-indigo-300 font-bold border border-indigo-800">
                  {llmMismatches.length}
                </span>
                {parsing && <Spinner size="sm" className="ml-1" />}
              </button>
            </div>
          </div>

          <div className="p-4 space-y-3">
            {displayedMismatches.length === 0 ? (
              <div className="py-8 text-center text-zinc-500 text-sm flex flex-col items-center gap-2">
                {parsing && activeMismatchTab === 'llm' ? (
                  <>
                    <Spinner size="md" />
                    <span>Evaluating LLM citations live...</span>
                  </>
                ) : (
                  <>
                    <CheckCircle className="w-6 h-6 text-emerald-500/60" />
                    <span>No findings in this tier.</span>
                  </>
                )}
              </div>
            ) : (
              displayedMismatches.map((m) => {
                const isExpanded = expandedMismatchFps.has(m.fingerprint)
                const sevBadge =
                  m.severity === 'error'
                    ? 'bg-rose-500/20 text-rose-300 border-rose-500/30'
                    : m.severity === 'warning'
                    ? 'bg-amber-500/20 text-amber-300 border-amber-500/30'
                    : 'bg-zinc-500/20 text-zinc-300 border-zinc-500/30'

                const evidenceList = Array.isArray(m.evidence) ? m.evidence : []
                const llmEvidence = evidenceList[0] || {}

                return (
                  <div
                    key={m.fingerprint}
                    className="bg-zinc-900/60 border border-zinc-800 rounded-lg p-3 text-xs space-y-2 transition-colors hover:border-zinc-700"
                  >
                    <div
                      className="flex items-start justify-between cursor-pointer gap-3"
                      onClick={() => toggleMismatchExpand(m.fingerprint)}
                    >
                      <div className="flex items-start gap-2.5 flex-1">
                        <button className="mt-0.5 text-zinc-400 hover:text-zinc-200">
                          {isExpanded ? (
                            <ChevronDown className="w-4 h-4" />
                          ) : (
                            <ChevronRight className="w-4 h-4" />
                          )}
                        </button>
                        <div className="space-y-1">
                          <div className="flex items-center gap-2 flex-wrap">
                            <span
                              className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase border ${sevBadge}`}
                            >
                              {m.severity}
                            </span>
                            <span className="font-mono font-semibold text-zinc-200">
                              {m.mismatch_type}
                            </span>
                          </div>
                          <p className="text-zinc-300 font-medium leading-relaxed">
                            {m.description}
                          </p>
                        </div>
                      </div>

                      {m.confidence && (
                        <span className="text-[10px] text-zinc-500 bg-zinc-950 px-2 py-0.5 rounded font-mono border border-zinc-800 shrink-0">
                          conf: {m.confidence}
                        </span>
                      )}
                    </div>

                    {isExpanded && (
                      <div className="mt-3 pt-3 border-t border-zinc-800/80 space-y-2.5 text-zinc-400 pl-6">
                        {/* Locations */}
                        <div className="grid grid-cols-2 gap-3 bg-zinc-950 p-2.5 rounded border border-zinc-800/80">
                          <div>
                            <span className="text-zinc-500 font-semibold block mb-0.5">
                              BD Location:
                            </span>
                            {m.bd_location ? (
                              <span className="font-mono text-zinc-300">
                                {m.bd_location.doc} ({m.bd_location.section || 'section'} L
                                {m.bd_location.line})
                              </span>
                            ) : (
                              <span className="text-zinc-600">N/A</span>
                            )}
                          </div>
                          <div>
                            <span className="text-zinc-500 font-semibold block mb-0.5">
                              DD Location:
                            </span>
                            {m.dd_location ? (
                              <span className="font-mono text-zinc-300">
                                {m.dd_location.doc} ({m.dd_location.section || 'section'} L
                                {m.dd_location.line})
                              </span>
                            ) : (
                              <span className="text-zinc-600">N/A</span>
                            )}
                          </div>
                        </div>

                        {/* Evidence Quotes for LLM tier */}
                        {m.derivation === 'llm' && (
                          <div className="space-y-2">
                            {llmEvidence.bd_quote && (
                              <div className="bg-zinc-950 p-2.5 rounded border border-zinc-800/80">
                                <span className="text-indigo-400 font-semibold block mb-1">
                                  BD Verbatim Quote:
                                </span>
                                <p className="font-mono text-zinc-200 italic">
                                  &quot;{llmEvidence.bd_quote}&quot;
                                </p>
                              </div>
                            )}
                            {llmEvidence.dd_quote && (
                              <div className="bg-zinc-950 p-2.5 rounded border border-zinc-800/80">
                                <span className="text-emerald-400 font-semibold block mb-1">
                                  DD Verbatim Quote:
                                </span>
                                <p className="font-mono text-zinc-200 italic">
                                  &quot;{llmEvidence.dd_quote}&quot;
                                </p>
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )
              })
            )}
          </div>
        </div>
      </div>
    )
  }

  // -------------------------------------------------------------------------
  // Render Upload View
  // -------------------------------------------------------------------------
  return (
    <div className="p-6 space-y-6 max-w-5xl mx-auto">
      {/* Screen Header */}
      <div className="screen-header">
        <h1 className="screen-title flex items-center gap-2">
          <FileText className="w-6 h-6 text-indigo-400" />
          Document Parsing
        </h1>
        <p className="screen-subtitle">
          Upload DD + BD reports, parse into a graph, and check BD↔DD consistency.
        </p>
      </div>

      {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}

      {/* Upload Boxes Side-by-Side */}
      <div className="grid grid-cols-2 gap-6">
        {/* Detail Design (DD) Box */}
        <div className="bg-zinc-800/40 border-2 border-dashed border-zinc-700 rounded-xl p-5 min-h-[240px] flex flex-col justify-between transition-colors hover:border-zinc-600">
          <div className="space-y-4">
            <div className="flex items-center justify-between border-b border-zinc-700/60 pb-3">
              <div className="flex items-center gap-2">
                <FileText className="w-5 h-5 text-sky-400" />
                <span className="font-bold text-sm text-zinc-100">Detail Design (DD)</span>
              </div>
              <span className="text-xs bg-sky-500/20 text-sky-300 font-semibold px-2 py-0.5 rounded border border-sky-500/30">
                {ddPaths.length} files
              </span>
            </div>

            {ddPaths.length === 0 ? (
              <div
                className="py-10 text-center flex flex-col items-center justify-center gap-2 cursor-pointer"
                onClick={() => handlePickFiles('dd')}
              >
                <Upload className="w-8 h-8 text-zinc-500" />
                <p className="text-xs text-zinc-400">
                  Click to add DD report files (<code className="text-zinc-300">*.report.md</code>)
                </p>
              </div>
            ) : (
              <div className="grid grid-cols-1 gap-2 max-h-48 overflow-y-auto pr-1">
                {ddPaths.map((pathStr) => (
                  <div
                    key={pathStr}
                    className="flex items-center justify-between bg-zinc-900/80 border border-zinc-700/60 p-2 rounded-lg text-xs"
                  >
                    <div className="flex items-center gap-2 truncate">
                      <FileText className="w-4 h-4 text-sky-400 shrink-0" />
                      <span className="text-zinc-200 truncate font-mono" title={pathStr}>
                        {basename(pathStr)}
                      </span>
                    </div>
                    <button
                      onClick={() => handleRemoveFile('dd', pathStr)}
                      className="text-zinc-500 hover:text-rose-400 ml-2"
                    >
                      <X className="w-4 h-4" />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          <Button
            variant="secondary"
            size="sm"
            onClick={() => handlePickFiles('dd')}
            className="w-full mt-4 gap-2 border-zinc-700 hover:bg-zinc-800"
          >
            <Plus className="w-4 h-4" />
            Add DD Files
          </Button>
        </div>

        {/* Basic Design (BD) Box */}
        <div className="bg-zinc-800/40 border-2 border-dashed border-zinc-700 rounded-xl p-5 min-h-[240px] flex flex-col justify-between transition-colors hover:border-zinc-600">
          <div className="space-y-4">
            <div className="flex items-center justify-between border-b border-zinc-700/60 pb-3">
              <div className="flex items-center gap-2">
                <FileText className="w-5 h-5 text-indigo-400" />
                <span className="font-bold text-sm text-zinc-100">Basic Design (BD)</span>
              </div>
              <span className="text-xs bg-indigo-500/20 text-indigo-300 font-semibold px-2 py-0.5 rounded border border-indigo-500/30">
                {bdPaths.length} / 1 required
              </span>
            </div>

            {bdPaths.length === 0 ? (
              <div
                className="py-10 text-center flex flex-col items-center justify-center gap-2 cursor-pointer"
                onClick={() => handlePickFiles('bd')}
              >
                <Upload className="w-8 h-8 text-zinc-500" />
                <p className="text-xs text-zinc-400">
                  Click to add exact 1 BD report file (<code className="text-zinc-300">GEN.BD-*.report.md</code>)
                </p>
              </div>
            ) : (
              <div className="grid grid-cols-1 gap-2 max-h-48 overflow-y-auto pr-1">
                {bdPaths.map((pathStr) => (
                  <div
                    key={pathStr}
                    className="flex items-center justify-between bg-zinc-900/80 border border-zinc-700/60 p-2 rounded-lg text-xs"
                  >
                    <div className="flex items-center gap-2 truncate">
                      <FileText className="w-4 h-4 text-indigo-400 shrink-0" />
                      <span className="text-zinc-200 truncate font-mono" title={pathStr}>
                        {basename(pathStr)}
                      </span>
                    </div>
                    <button
                      onClick={() => handleRemoveFile('bd', pathStr)}
                      className="text-zinc-500 hover:text-rose-400 ml-2"
                    >
                      <X className="w-4 h-4" />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          <Button
            variant="secondary"
            size="sm"
            onClick={() => handlePickFiles('bd')}
            className="w-full mt-4 gap-2 border-zinc-700 hover:bg-zinc-800"
          >
            <Plus className="w-4 h-4" />
            Add BD File
          </Button>
        </div>
      </div>

      {/* Bottom Bar / Action */}
      <div className="bg-zinc-800/40 border border-zinc-700/80 rounded-xl p-5 flex items-center justify-between">
        <label className="flex items-center gap-2.5 text-xs text-zinc-300 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={llmEnabled}
            onChange={(e) => setLlmEnabled(e.target.checked)}
            className="w-4 h-4 rounded border-zinc-700 bg-zinc-900 text-indigo-600 focus:ring-indigo-500"
          />
          <div className="flex items-center gap-1.5">
            <Sparkles className="w-3.5 h-3.5 text-indigo-400" />
            <span>Run semantic (LLM) citation consistency checks</span>
          </div>
        </label>

        <div className="flex items-center gap-4">
          {!canStartParsing && !parsing && (
            <span className="text-xs text-amber-400/90 font-medium">
              Add ≥1 DD and exactly 1 BD
            </span>
          )}

          <Button
            onClick={handleStartParsing}
            disabled={!canStartParsing}
            className="bg-indigo-600 hover:bg-indigo-500 text-white gap-2 font-semibold px-6"
          >
            {parsing ? (
              <>
                <Spinner size="sm" />
                <span>Parsing Cluster…</span>
              </>
            ) : (
              <>
                <RefreshCw className="w-4 h-4" />
                <span>Start Parsing</span>
              </>
            )}
          </Button>
        </div>
      </div>

      {/* Recent Parses Section */}
      {recentClusters.length > 0 && (
        <div className="bg-zinc-800/40 border border-zinc-700/80 rounded-xl p-5 space-y-4">
          <div className="flex items-center justify-between border-b border-zinc-700/60 pb-3">
            <div className="flex items-center gap-2">
              <History className="w-4 h-4 text-indigo-400" />
              <h2 className="font-bold text-sm text-zinc-100">Recent Parses</h2>
            </div>
            <span className="text-xs text-zinc-400 font-mono">{recentClusters.length} clusters available</span>
          </div>

          <div className="grid grid-cols-1 gap-2.5 max-h-64 overflow-y-auto pr-1">
            {recentClusters.map((cluster) => (
              <div
                key={cluster.cluster_id}
                onClick={() => handleOpenCluster(cluster.cluster_id)}
                className="flex items-center justify-between bg-zinc-900/80 border border-zinc-700/60 p-3 rounded-lg text-xs cursor-pointer transition-colors hover:border-indigo-500/60 hover:bg-zinc-900"
              >
                <div className="flex items-center gap-3">
                  <FileText className="w-4 h-4 text-indigo-400 shrink-0" />
                  <div>
                    <span className="font-semibold text-zinc-100 block">{cluster.cluster_name}</span>
                    <span className="text-[10px] text-zinc-500 font-mono">
                      Generated: {cluster.generated_at ? new Date(cluster.generated_at).toLocaleString() : 'N/A'}
                    </span>
                  </div>
                </div>

                <div className="flex items-center gap-3">
                  <div className="flex items-center gap-1.5 text-[11px]">
                    <span className="px-2 py-0.5 rounded bg-indigo-950 text-indigo-300 font-mono border border-indigo-800">
                      {cluster.node_count} nodes
                    </span>
                    <span className="px-2 py-0.5 rounded bg-sky-950 text-sky-300 font-mono border border-sky-800">
                      {cluster.edge_count} edges
                    </span>
                    <span className="px-2 py-0.5 rounded bg-amber-950 text-amber-300 font-mono border border-amber-800">
                      {cluster.mismatch_count} mismatches
                    </span>
                  </div>

                  <div className="flex items-center gap-1.5">
                    <Button variant="secondary" size="sm" className="gap-1 text-[11px] border-zinc-700">
                      <Eye className="w-3.5 h-3.5 text-indigo-400" />
                      <span>View</span>
                    </Button>
                    <button
                      onClick={(e) => handleDeleteCluster(e, cluster.cluster_id)}
                      className="p-1.5 rounded text-zinc-500 hover:text-rose-400 hover:bg-rose-500/10 transition-colors"
                      title="Delete this parse permanently"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
