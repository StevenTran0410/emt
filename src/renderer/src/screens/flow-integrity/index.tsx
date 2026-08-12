import React, { useState, useEffect, useCallback, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ReactFlow,
  Background,
  Controls,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
  type NodeProps,
  Handle,
  Position,
  MarkerType
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'

import {
  ShieldCheck,
  Zap,
  RefreshCw,
  X,
  ChevronDown,
  ChevronRight,
  ArrowRight,
  ArrowLeft,
  FileCode,
  FileText,
  AlertTriangle,
  CheckCircle,
  HelpCircle,
  Info
} from 'lucide-react'
import { applyComponentBandedLayout } from '../graph/layout'
import type {
  DocGraphClusterSummary,
  FlowIntegrityMapResponse,
  FlowIntegrityFindingsResponse,
  FlowIntegrityMapNode,
  FlowIntegrityMapEdge,
  FlowIntegrityFindingRow
} from '../../types/electron'

interface LocalRepo {
  id: string
  name: string
  active_snapshot_id?: string | null
}

const VERDICT_STYLES: Record<string, { stroke: string; bg: string; text: string; border: string; label: string }> = {
  MATCH: { stroke: '#10b981', bg: 'bg-emerald-950/80', text: 'text-emerald-300', border: 'border-emerald-500', label: 'Match' },
  CODE_ROUTE: { stroke: '#3b82f6', bg: 'bg-blue-950/80', text: 'text-blue-300', border: 'border-blue-500', label: 'Code Route' },
  PARTIAL: { stroke: '#f59e0b', bg: 'bg-amber-950/80', text: 'text-amber-300', border: 'border-amber-500', label: 'Partial' },
  BROKEN: { stroke: '#ef4444', bg: 'bg-red-950/80', text: 'text-red-300', border: 'border-red-500', label: 'Broken' },
  CODE_ONLY: { stroke: '#3b82f6', bg: 'bg-blue-950/80', text: 'text-blue-300', border: 'border-blue-500', label: 'Code Only' },
  UNKNOWN: { stroke: '#64748b', bg: 'bg-slate-900/80', text: 'text-slate-400', border: 'border-slate-600', label: 'Unknown' },
  RECOVERY_GAP: { stroke: '#f97316', bg: 'bg-orange-950/80', text: 'text-orange-300', border: 'border-orange-500', label: 'Recovery Gap' },
  DOC_ONLY: { stroke: '#8b5cf6', bg: 'bg-purple-950/80', text: 'text-purple-300', border: 'border-purple-500', label: 'Doc Only' },
  GRAPH_GAP: { stroke: '#9ca3af', bg: 'bg-slate-800/80', text: 'text-slate-300', border: 'border-slate-500', label: 'Graph Gap' }
}

const TAG_STYLES: Record<string, { bg: string; text: string; label: string }> = {
  DOC_MATCHED: { bg: 'bg-emerald-900/60', text: 'text-emerald-300', label: 'DOC_MATCHED' },
  DOC_CONTRADICTED: { bg: 'bg-red-900/60', text: 'text-red-300', label: 'DOC_CONTRADICTED' },
  CODE_ONLY: { bg: 'bg-blue-900/60', text: 'text-blue-300', label: 'CODE_ONLY' },
  BD_ONLY: { bg: 'bg-purple-900/60', text: 'text-purple-300', label: 'BD_ONLY' },
  UNKNOWN: { bg: 'bg-slate-800', text: 'text-slate-400', label: 'UNKNOWN' }
}

function cleanLabelParts(s: string | undefined | null): { title: string; subtitle?: string } {
  if (!s) return { title: '' }
  const parts = s.split(/<br\s*\/?>/i)
  const title = parts[0].trim()
  const subtitle = parts.slice(1).map((p) => p.trim()).filter(Boolean).join(' · ')
  return { title, subtitle: subtitle || undefined }
}

function CleanLabel({
  text,
  className,
  titleClassName,
  subClassName
}: {
  text: string | undefined | null
  className?: string
  titleClassName?: string
  subClassName?: string
}): React.ReactElement {
  const { title, subtitle } = cleanLabelParts(text)
  return (
    <div className={className}>
      <div className={titleClassName || 'font-semibold text-slate-100 truncate'}>{title}</div>
      {subtitle && (
        <div className={subClassName || 'text-[10px] text-slate-400 font-mono truncate mt-0.5'}>{subtitle}</div>
      )}
    </div>
  )
}

function CustomIntegrityNode({ data }: NodeProps): React.ReactElement {
  const nodeData = data as any
  const tagStyle = TAG_STYLES[nodeData.tag] ?? TAG_STYLES.UNKNOWN

  return (
    <div
      className={`relative px-3 py-2 rounded-lg text-xs shadow-lg backdrop-blur-sm min-w-[170px] max-w-[240px] border transition-all hover:ring-2 hover:ring-indigo-400/50 bg-slate-900/90 border-slate-700`}
    >
      <Handle type="target" position={Position.Left} className="!bg-slate-500 !w-2 !h-2 !border-0" />
      <Handle type="source" position={Position.Right} className="!bg-slate-500 !w-2 !h-2 !border-0" />

      <div className="flex items-center justify-between gap-1 mb-1">
        <span className={`font-mono text-[9px] px-1.5 py-0.5 rounded font-semibold ${tagStyle.bg} ${tagStyle.text}`}>
          {tagStyle.label}
        </span>
        <span className="text-[10px] text-slate-400 uppercase font-mono">{nodeData.node_kind}</span>
      </div>

      <CleanLabel
        text={nodeData.label || nodeData.id}
        titleClassName="font-semibold text-slate-100 truncate"
        subClassName="text-[10px] text-slate-400 font-mono truncate mt-0.5"
      />

      {nodeData.binding && (
        <div className="text-[10px] font-mono text-indigo-300 truncate mt-0.5" title={nodeData.binding}>
          {nodeData.binding}
        </div>
      )}
    </div>
  )
}

export function FlowIntegrityScreen(): React.ReactElement {
  const navigate = useNavigate()
  const [clusters, setClusters] = useState<DocGraphClusterSummary[]>([])
  const [selectedClusterId, setSelectedClusterId] = useState<string>('')
  const [snapshotId, setSnapshotId] = useState<string>('')
  const [mapData, setMapData] = useState<FlowIntegrityMapResponse | null>(null)
  const [findingsData, setFindingsData] = useState<FlowIntegrityFindingsResponse | null>(null)
  const [loading, setLoading] = useState<boolean>(false)
  const [running, setRunning] = useState<boolean>(false)
  const [runError, setRunError] = useState<string | null>(null)
  const [providers, setProviders] = useState<{ id: string; name: string }[]>([])
  const [selectedProviderId, setSelectedProviderId] = useState<string>('')
  const [snapshots, setSnapshots] = useState<{ id: string; label: string }[]>([])
  const [activeTab, setActiveTab] = useState<'broken' | 'code_only' | 'gaps'>('broken')
  const [showUnknown, setShowUnknown] = useState<boolean>(false)
  const [showUnknownMap, setShowUnknownMap] = useState<boolean>(false)
  const [selectedElement, setSelectedElement] = useState<{ type: 'node' | 'edge'; data: any; relatedTransitions?: any[] } | null>(null)
  const [findingsExpanded, setFindingsExpanded] = useState<boolean>(false)

  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([])

  // Load available clusters & providers
  useEffect(() => {
    window.api?.docGraph?.listClusters().then((res: DocGraphClusterSummary[]) => {
      setClusters(res || [])
      if (res && res.length > 0) {
        setSelectedClusterId(res[0].cluster_id)
        if (res[0].snapshot_id) {
          setSnapshotId(res[0].snapshot_id)
        }
      }
    })
    window.api?.provider?.list().then((list: any[]) => {
      setProviders((list || []).map((p) => ({ id: p.id, name: p.display_name || p.model_id || p.id })))
    })
    window.api?.folder?.list().then((repos: LocalRepo[]) => {
      const list = (repos || [])
        .filter((r) => r.active_snapshot_id)
        .map((r) => ({ id: r.active_snapshot_id as string, label: `${r.name || 'Repo'} (${(r.active_snapshot_id as string).slice(0, 8)})` }))
      setSnapshots(list)
      setSnapshotId((prev) => prev || list[0]?.id || '')
    })
  }, [])

  // Fetch map & findings data when cluster/snapshot selection changes (FIX 1: guard against empty snapshot)
  const loadData = useCallback(async () => {
    if (!selectedClusterId) return
    const c = clusters.find((x) => x.cluster_id === selectedClusterId)
    const snap = snapshotId || c?.snapshot_id || ''

    if (!selectedClusterId || !snap) return

    setLoading(true)
    try {
      const [m, f] = await Promise.all([
        window.api?.docGraph?.flowIntegrityGetMap?.(selectedClusterId, snap),
        window.api?.docGraph?.flowIntegrityGetFindings?.(selectedClusterId, snap)
      ])

      setMapData(m)
      setFindingsData(f)
    } catch (e) {
      console.error('Failed to load flow integrity data:', e)
    } finally {
      setLoading(false)
    }
  }, [selectedClusterId, snapshotId, clusters])

  useEffect(() => {
    if (selectedClusterId) {
      loadData()
    }
  }, [selectedClusterId, loadData])

  // Build + lay out graph with component Y-banding in rankdir LR (FIX 3)
  useEffect(() => {
    if (!mapData?.nodes || !mapData?.edges) {
      setNodes([])
      setEdges([])
      return
    }

    const visibleEdgesRaw: FlowIntegrityMapEdge[] = showUnknownMap
      ? mapData.edges
      : mapData.edges.filter((e: FlowIntegrityMapEdge) => e.data?.verdict !== 'UNKNOWN')

    const touched = new Set<string>()
    visibleEdgesRaw.forEach((e: FlowIntegrityMapEdge) => {
      touched.add(e.source)
      touched.add(e.target)
    })
    const visibleNodesRaw: FlowIntegrityMapNode[] = showUnknownMap
      ? mapData.nodes
      : mapData.nodes.filter((n: FlowIntegrityMapNode) => touched.has(n.id))

    const nodeTagMap = new Map<string, string>()
    visibleNodesRaw.forEach((n: FlowIntegrityMapNode) => {
      if (n.data?.tag) nodeTagMap.set(n.id, n.data.tag)
    })

    const rawNodes: Node[] = visibleNodesRaw.map((n: any) => ({
      id: n.id,
      type: 'integrityNode',
      position: { x: 0, y: 0 },
      data: n.data
    }))

    const rawEdges: Edge[] = visibleEdgesRaw.map((e: any) => {
      const vStyle = VERDICT_STYLES[e.data?.verdict] ?? VERDICT_STYLES.UNKNOWN
      const verdict = e.data?.verdict
      const guardText = e.data?.guard_text || e.label
      const guardVerdict = e.data?.guard_verdict
      const isCodeEdge = Boolean(e.data?.is_code_edge)

      let edgeLabel = ''
      if (!isCodeEdge) {
        if (['BROKEN', 'PARTIAL', 'RECOVERY_GAP'].includes(verdict)) {
          edgeLabel = VERDICT_STYLES[verdict]?.label || verdict
        } else if (
          guardVerdict === 'CLASS_MISMATCH' ||
          (guardText && !['calls', 'dependency', 'executes', 'submits', 'transition', 'flow'].includes(guardText.toLowerCase()))
        ) {
          edgeLabel = guardText
        }
      }

      const isSecondary =
        isCodeEdge ||
        nodeTagMap.get(e.source) === 'CODE_ONLY' ||
        nodeTagMap.get(e.target) === 'CODE_ONLY'

      const strokeWidth = isSecondary ? 1.25 : 2
      const opacity = isSecondary ? 0.45 : 1.0

      return {
        id: e.id,
        source: e.source,
        target: e.target,
        type: 'smoothstep',
        label: edgeLabel || undefined,
        labelStyle: edgeLabel ? { fill: '#e2e8f0', fontSize: 10, fontFamily: 'monospace' } : undefined,
        labelBgStyle: edgeLabel ? { fill: '#0f172a', fillOpacity: 0.85 } : undefined,
        labelBgPadding: edgeLabel ? [4, 2] : undefined,
        labelBgBorderRadius: edgeLabel ? 4 : undefined,
        style: {
          stroke: vStyle.stroke,
          strokeWidth,
          opacity,
          strokeDasharray: verdict === 'GRAPH_GAP' ? '5,5' : undefined
        },
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: vStyle.stroke,
          width: isSecondary ? 12 : 16,
          height: isSecondary ? 12 : 16
        },
        data: e.data
      }
    })

    const layoutNodes = applyComponentBandedLayout(rawNodes, rawEdges, {
      rankdir: 'LR',
      nodesep: 70,
      ranksep: 120,
      componentGapY: 80
    })
    setNodes(layoutNodes)
    setEdges(rawEdges)
  }, [mapData, showUnknownMap, setNodes, setEdges])

  const handleRunPipeline = async () => {
    if (!selectedClusterId) return
    const c = clusters.find((x) => x.cluster_id === selectedClusterId)
    const snap = snapshotId || c?.snapshot_id || ''

    if (!snap) {
      setRunError('Select a snapshot first')
      return
    }

    setRunning(true)
    setRunError(null)
    try {
      const pId = selectedProviderId ? selectedProviderId : null
      await window.api?.docGraph?.flowIntegrityRun?.(selectedClusterId, snap, pId)
      await loadData()
    } catch (e: any) {
      console.error('Failed to run flow integrity pipeline:', e)
      setRunError(e?.message || 'Failed to execute flow integrity pipeline')
    } finally {
      setRunning(false)
    }
  }

  const nodeTypes = useMemo(() => ({ integrityNode: CustomIntegrityNode }), [])

  // Rich evidence on click (FIX 2)
  const onNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      const nData = node.data as any
      const related: any[] = []

      if (mapData?.nodes && mapData?.edges) {
        const nodeMap = new Map<string, FlowIntegrityMapNode>(mapData.nodes.map((n: FlowIntegrityMapNode) => [n.id, n]))
        mapData.edges.forEach((e: FlowIntegrityMapEdge) => {
          if (e.source === node.id || e.target === node.id) {
            const isOutgoing = e.source === node.id
            const otherId = isOutgoing ? e.target : e.source
            const otherNode = nodeMap.get(otherId)
            related.push({
              id: e.id,
              direction: isOutgoing ? 'outgoing' : 'incoming',
              otherNodeId: otherId,
              otherNodeLabel: otherNode?.data?.label || otherNode?.data?.binding || otherId,
              otherNodeKind: otherNode?.data?.node_kind,
              verdict: e.data?.verdict,
              guard_verdict: e.data?.guard_verdict,
              ai_bucket: e.data?.ai_bucket,
              reason: e.data?.reason,
              evidence: (e.data as any)?.evidence
            })
          }
        })
      }

      setSelectedElement({ type: 'node', data: nData, relatedTransitions: related })
    },
    [mapData]
  )

  const onEdgeClick = useCallback(
    (_: React.MouseEvent, edge: Edge) => {
      const eData = edge.data as any
      let srcLabel = edge.source
      let dstLabel = edge.target
      if (mapData?.nodes) {
        const nodeMap = new Map<string, FlowIntegrityMapNode>(mapData.nodes.map((n: FlowIntegrityMapNode) => [n.id, n]))
        const sNode = nodeMap.get(edge.source)
        const dNode = nodeMap.get(edge.target)
        if (sNode) srcLabel = sNode.data?.label || sNode.data?.binding || edge.source
        if (dNode) dstLabel = dNode.data?.label || dNode.data?.binding || edge.target
      }
      setSelectedElement({
        type: 'edge',
        data: {
          ...eData,
          srcLabel,
          dstLabel
        }
      })
    },
    [mapData]
  )

  const cal = findingsData?.calibration

  return (
    <div className="flex flex-col h-full bg-slate-950 text-slate-100 overflow-hidden">
      {/* Top Header & Controls */}
      <div className="p-4 border-b border-slate-800 bg-slate-900/60 flex flex-wrap items-center justify-between gap-4 shrink-0">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-lg bg-emerald-500/10 border border-emerald-500/30 text-emerald-400">
            <ShieldCheck className="w-5 h-5" />
          </div>
          <div>
            <h1 className="text-base font-bold text-slate-100 flex items-center gap-2">
              Business Flow Integrity Map (Dimension 03)
            </h1>
            <p className="text-xs text-slate-400">
              End-to-End Flow Map • Node Evidence & Contradiction Proof • Calibration Gate
            </p>
          </div>

          <button
            onClick={() => navigate('/flow-integrity')}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded border border-slate-700 font-medium text-xs shadow-sm transition-colors ml-4"
          >
            <ArrowLeft className="w-3.5 h-3.5" />
            <span>Report</span>
          </button>
        </div>

        {/* Cluster / Snapshot selector & LLM Provider / Run button */}
        <div className="flex items-center gap-2">
          <label className="text-xs text-slate-400">Cluster:</label>
          <select
            className="bg-slate-800 text-xs text-slate-200 px-3 py-1.5 rounded border border-slate-700 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            value={selectedClusterId}
            onChange={(e) => {
              setSelectedClusterId(e.target.value)
              const c = clusters.find((x) => x.cluster_id === e.target.value)
              if (c?.snapshot_id) setSnapshotId(c.snapshot_id)
            }}
          >
            {clusters.map((c) => (
              <option key={c.cluster_id} value={c.cluster_id}>
                {c.cluster_name} ({c.cluster_id})
              </option>
            ))}
          </select>

          <label className="text-xs text-slate-400 ml-1">Snapshot:</label>
          <select
            className="bg-slate-800 text-xs text-slate-200 px-3 py-1.5 rounded border border-slate-700 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            value={snapshotId}
            onChange={(e) => setSnapshotId(e.target.value)}
          >
            {snapshots.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label}
              </option>
            ))}
          </select>

          <label className="text-xs text-slate-400 ml-1">LLM Provider:</label>
          <select
            className="bg-slate-800 text-xs text-slate-200 px-3 py-1.5 rounded border border-slate-700 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            value={selectedProviderId}
            onChange={(e) => setSelectedProviderId(e.target.value)}
          >
            <option value="">Deterministic only (no LLM)</option>
            {providers.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>

          <button
            onClick={handleRunPipeline}
            disabled={loading || running || !selectedClusterId}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-amber-600 hover:bg-amber-500 disabled:opacity-50 text-white rounded font-medium text-xs shadow-md transition-colors"
          >
            <Zap className={`w-3.5 h-3.5 ${running ? 'animate-bounce' : ''}`} />
            <span>{running ? 'Running Pipeline...' : 'Run Pipeline'}</span>
          </button>

          <button
            onClick={loadData}
            disabled={loading || running}
            className="p-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded border border-slate-700 transition-colors"
            title="Reload Data"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {runError && (
        <div className="px-4 py-2 bg-red-950/80 border-b border-red-800 text-red-300 text-xs flex items-center justify-between">
          <span>Error running pipeline: {runError}</span>
          <button onClick={() => setRunError(null)} className="hover:text-white">
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* Calibration Banner */}
      {cal && (
        <div className="px-4 py-2.5 bg-slate-900/90 border-b border-slate-800 flex flex-wrap items-center justify-between gap-4 text-xs shrink-0">
          <div className="flex items-center gap-3">
            <span
              className={`px-2.5 py-1 rounded font-bold font-mono text-xs ${
                cal.calibration_pass
                  ? 'bg-emerald-950 text-emerald-400 border border-emerald-500/50'
                  : 'bg-red-950 text-red-400 border border-red-500/50'
              }`}
            >
              CALIBRATION: {cal.calibration_pass ? 'PASS (80–100%)' : 'FAIL (<80% or 100%)'}
            </span>
            <span className="text-slate-300 font-mono">
              Match: <strong className="text-emerald-400">{cal.match_percentage}%</strong> ({cal.match_count}/{cal.resolved_units} resolved units)
            </span>
          </div>

          <div className="flex items-center gap-4 text-slate-400 font-mono text-[11px]">
            <span>Total Units: {cal.total_units}</span>
            <span>Broken: {cal.broken_count}</span>
            <span>Code Only: {cal.code_only_count}</span>
            <span>Unknown: {cal.unknown_count}</span>
            {cal.ai_bucket_counts?.stale_missing > 0 && (
              <span className="text-purple-400 font-semibold">
                stale_missing: {cal.ai_bucket_counts.stale_missing}
              </span>
            )}
          </div>
        </div>
      )}

      {/* Main Graph View Area */}
      <div className="flex-1 flex min-h-0 relative">
          {/* Map Container */}
          <div className="flex-1 h-full bg-slate-950 relative">
            <ReactFlow
              nodes={nodes}
              edges={edges}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onNodeClick={onNodeClick}
              onEdgeClick={onEdgeClick}
              nodeTypes={nodeTypes}
              fitView
              className="bg-slate-950"
            >
              <Background color="#334155" gap={16} />
              <Controls className="!bg-slate-900 !border-slate-800 !text-slate-300" />
            </ReactFlow>

            {/* Map Legend */}
            <div className="absolute bottom-4 left-4 bg-slate-900/90 border border-slate-800 p-2.5 rounded-lg text-xs space-y-1.5 backdrop-blur-md shadow-xl max-w-xs">
              <div className="font-bold text-slate-300 text-[11px] mb-1">Verdicts & Edges Legend</div>
              <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
                <span className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-full bg-emerald-500 inline-block"></span> MATCH</span>
                <span className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-full bg-blue-500 inline-block"></span> CODE_ROUTE</span>
                <span className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-full bg-amber-500 inline-block"></span> PARTIAL</span>
                <span className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-full bg-red-500 inline-block"></span> BROKEN</span>
                <span className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-full bg-blue-500 inline-block"></span> CODE_ONLY</span>
                <span className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-full bg-slate-500 inline-block"></span> UNKNOWN</span>
              </div>
              <button
                onClick={() => setShowUnknownMap((s) => !s)}
                className="mt-1 w-full flex items-center justify-center gap-1 border-t border-slate-800 pt-1.5 text-[10px] text-slate-400 hover:text-slate-200"
              >
                {showUnknownMap ? '▾ Hide' : '▸ Show'} unknown transitions
                {mapData ? ` (${mapData.edges.filter((e: FlowIntegrityMapEdge) => e.data?.verdict === 'UNKNOWN').length})` : ''}
              </button>
            </div>
          </div>

          {/* Right Side Evidence & Findings Panel (FIX 4 & FIX 2) */}
          <div className="w-[420px] border-l border-slate-800 bg-slate-900/90 flex flex-col shrink-0 overflow-hidden shadow-2xl">
            {/* Primary Section: Node / Edge Evidence Panel (FIX 2) */}
            <div className="flex-1 overflow-y-auto p-4 space-y-4 border-b border-slate-800">
              {selectedElement ? (
                <div className="space-y-4">
                  <div className="flex items-start justify-between gap-2 pb-2 border-b border-slate-800">
                    <div>
                      <span className="font-bold text-indigo-400 text-[10px] uppercase tracking-wider block mb-1">
                        {selectedElement.type === 'edge' ? 'Transition Verdict Evidence' : 'Node Flow Evidence'}
                      </span>
                      {selectedElement.type === 'node' ? (
                        <CleanLabel
                          text={selectedElement.data?.label || selectedElement.data?.id}
                          titleClassName="text-sm font-bold text-slate-100"
                          subClassName="text-xs text-indigo-300 font-mono mt-0.5"
                        />
                      ) : (
                        <div className="text-xs font-semibold text-slate-200">
                          {selectedElement.data?.srcLabel} → {selectedElement.data?.dstLabel}
                        </div>
                      )}
                    </div>
                    <button
                      onClick={() => setSelectedElement(null)}
                      className="p-1 text-slate-400 hover:text-slate-200 hover:bg-slate-800 rounded"
                      title="Close"
                    >
                      <X className="w-4 h-4" />
                    </button>
                  </div>

                  {/* Node Evidence Detail View */}
                  {selectedElement.type === 'node' && (
                    <div className="space-y-3 text-xs">
                      {/* Node Kind & Tag */}
                      <div className="flex items-center gap-2">
                        <span className="px-2 py-0.5 rounded bg-slate-800 text-slate-300 font-mono text-[10px] uppercase">
                          Kind: {selectedElement.data?.node_kind}
                        </span>
                        {selectedElement.data?.tag && (
                          <span
                            className={`px-2 py-0.5 rounded font-mono text-[10px] font-bold ${
                              TAG_STYLES[selectedElement.data.tag]?.bg || 'bg-slate-800'
                            } ${TAG_STYLES[selectedElement.data.tag]?.text || 'text-slate-300'}`}
                          >
                            {selectedElement.data.tag}
                          </span>
                        )}
                      </div>

                      {/* BD Evidence Row */}
                      <div className="p-2.5 rounded-lg bg-slate-950 border border-slate-800 space-y-1">
                        <div className="flex items-center gap-1.5 text-slate-300 font-medium">
                          <FileText className="w-3.5 h-3.5 text-amber-400" />
                          <span>Basic Design (BD) Document Evidence</span>
                        </div>
                        <div className="text-[11px] text-slate-400 font-mono pl-5">
                          Lines {selectedElement.data?.doc_line_start || 1}–{selectedElement.data?.doc_line_end || selectedElement.data?.doc_line_start || 1}
                        </div>
                        {selectedElement.data?.binding && (
                          <div className="text-[11px] text-indigo-300 font-mono pl-5">
                            Binding: {selectedElement.data.binding} ({selectedElement.data.binding_type || 'asset'})
                          </div>
                        )}
                      </div>

                      {/* Code Evidence Row */}
                      {selectedElement.data?.rel_path && (
                        <div className="p-2.5 rounded-lg bg-slate-950 border border-slate-800 space-y-1">
                          <div className="flex items-center gap-1.5 text-slate-300 font-medium">
                            <FileCode className="w-3.5 h-3.5 text-blue-400" />
                            <span>Code Graph Resolved File</span>
                          </div>
                          <div className="text-[11px] text-blue-300 font-mono pl-5 break-all">
                            {selectedElement.data.rel_path}
                          </div>
                        </div>
                      )}

                      {/* Proof Verdict Block */}
                      {selectedElement.data?.tag === 'DOC_MATCHED' && (
                        <div className="p-3 rounded-lg bg-emerald-950/60 border border-emerald-500/40 text-emerald-300 space-y-1">
                          <div className="flex items-center gap-1.5 font-bold">
                            <CheckCircle className="w-4 h-4 text-emerald-400" />
                            <span>Faithful Abstraction Proof</span>
                          </div>
                          <p className="text-[11px] text-emerald-200/90 pl-5">
                            BD line {selectedElement.data?.doc_line_start || 1} step faithfully abstracts code execution flow at <code className="font-mono bg-emerald-900/60 px-1 rounded">{selectedElement.data?.rel_path}</code>.
                          </p>
                        </div>
                      )}

                      {selectedElement.data?.tag === 'DOC_CONTRADICTED' && (
                        <div className="p-3 rounded-lg bg-red-950/70 border border-red-500/50 text-red-300 space-y-1">
                          <div className="flex items-center gap-1.5 font-bold">
                            <AlertTriangle className="w-4 h-4 text-red-400" />
                            <span>Contradiction Proof</span>
                          </div>
                          <p className="text-[11px] text-red-200/90 pl-5 leading-relaxed">
                            BD documents this asset as missing or unresolved at line {selectedElement.data?.doc_line_start || 1}, but the code graph resolves it at <code className="font-mono bg-red-900/60 px-1 rounded">{selectedElement.data?.rel_path}</code> → stale missing contradiction.
                          </p>
                        </div>
                      )}

                      {selectedElement.data?.tag === 'CODE_ONLY' && (
                        <div className="p-3 rounded-lg bg-blue-950/70 border border-blue-500/50 text-blue-300 space-y-1">
                          <div className="flex items-center gap-1.5 font-bold">
                            <Info className="w-4 h-4 text-blue-400" />
                            <span>Code Only Asset (Silent Omission)</span>
                          </div>
                          <p className="text-[11px] text-blue-200/90 pl-5 leading-relaxed">
                            Reachable code route <code className="font-mono bg-blue-900/60 px-1 rounded">{selectedElement.data?.rel_path}</code> is entirely omitted from BD documentation.
                          </p>
                        </div>
                      )}

                      {(selectedElement.data?.tag === 'UNKNOWN' || selectedElement.data?.tag === 'BD_ONLY') && (
                        <div className="p-3 rounded-lg bg-slate-900/90 border border-slate-700/80 text-slate-300 space-y-1">
                          <div className="flex items-center gap-1.5 font-bold text-amber-400">
                            <HelpCircle className="w-4 h-4 text-amber-400" />
                            <span>Not Evaluable (No Code Source File)</span>
                          </div>
                          <p className="text-[11px] text-slate-300 pl-5 leading-relaxed">
                            BD documents this step, but no matching source file exists in the indexed repository (it is below route altitude — an intra-program step — or points to an external program/dataset that is not part of the shipped source). It cannot be verified against code.
                          </p>
                          {selectedElement.data?.binding && (
                            <div className="text-[11px] text-indigo-300 font-mono pl-5 pt-0.5">
                              BD references: <code className="bg-slate-950 px-1 rounded">{selectedElement.data.binding}</code>
                            </div>
                          )}
                        </div>
                      )}

                      {/* Related Connected Transitions */}
                      {selectedElement.relatedTransitions && selectedElement.relatedTransitions.length > 0 && (
                        <div className="space-y-2 pt-2 border-t border-slate-800">
                          <div className="font-bold text-slate-300 text-xs">
                            Connected Flow Transitions ({selectedElement.relatedTransitions.length})
                          </div>
                          <div className="space-y-2 max-h-56 overflow-y-auto pr-1">
                            {selectedElement.relatedTransitions.map((r) => (
                              <div
                                key={r.id}
                                className="p-2.5 rounded-lg bg-slate-950 border border-slate-800 space-y-1 text-[11px]"
                              >
                                <div className="flex items-center justify-between gap-1">
                                  <div className="flex items-center gap-1 text-slate-200 font-medium truncate">
                                    {r.direction === 'outgoing' ? (
                                      <ArrowRight className="w-3.5 h-3.5 text-indigo-400 shrink-0" />
                                    ) : (
                                      <ArrowLeft className="w-3.5 h-3.5 text-amber-400 shrink-0" />
                                    )}
                                    <CleanLabel text={r.otherNodeLabel} titleClassName="truncate" />
                                  </div>
                                  <span
                                    className={`px-1.5 py-0.5 rounded font-mono font-bold text-[10px] ${
                                      VERDICT_STYLES[r.verdict]?.bg || 'bg-slate-800'
                                    } ${VERDICT_STYLES[r.verdict]?.text || 'text-slate-400'}`}
                                  >
                                    {r.verdict}
                                  </span>
                                </div>
                                <div className="text-slate-400 text-[10px] pl-4">{r.reason}</div>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}

                  {/* Edge Evidence Detail View */}
                  {selectedElement.type === 'edge' && (
                    <div className="space-y-3 text-xs">
                      <div className="flex items-center justify-between">
                        <span className="text-slate-400">Verdict:</span>
                        <span
                          className={`px-2.5 py-1 rounded font-bold font-mono text-xs ${
                            VERDICT_STYLES[selectedElement.data?.verdict]?.bg
                          } ${VERDICT_STYLES[selectedElement.data?.verdict]?.text}`}
                        >
                          {selectedElement.data?.verdict}
                        </span>
                      </div>

                      {selectedElement.data?.guard_verdict && (
                        <div className="flex items-center justify-between text-slate-400">
                          <span>Guard Verdict:</span>
                          <span className="font-mono text-indigo-300 font-semibold">
                            {selectedElement.data.guard_verdict}
                          </span>
                        </div>
                      )}

                      {selectedElement.data?.ai_bucket && (
                        <div className="flex items-center justify-between text-slate-400">
                          <span>AI Bucket:</span>
                          <span className="px-2 py-0.5 rounded bg-purple-950 text-purple-300 font-mono text-[10px]">
                            {selectedElement.data.ai_bucket}
                          </span>
                        </div>
                      )}

                      <div className="p-2.5 rounded-lg bg-slate-950 border border-slate-800 space-y-1">
                        <div className="text-slate-300 font-medium">BD Span Evidence</div>
                        <div className="text-[11px] text-slate-400 font-mono">
                          Line {selectedElement.data?.doc_line || 1}
                        </div>
                      </div>

                      {selectedElement.data?.reason && (
                        <div className="text-slate-300 bg-slate-950 p-2.5 rounded-lg border border-slate-800 text-[11px] leading-relaxed">
                          {selectedElement.data.reason}
                        </div>
                      )}

                      {selectedElement.data?.code_subpath && selectedElement.data.code_subpath.length > 0 && (
                        <div className="p-2.5 rounded-lg bg-slate-950 border border-slate-800 space-y-1">
                          <div className="text-slate-300 font-medium text-[11px]">Code Route Hops</div>
                          <div className="text-[10px] font-mono text-blue-300 max-h-24 overflow-y-auto space-y-0.5">
                            {selectedElement.data.code_subpath.map((hop: string, idx: number) => (
                              <div key={idx}>• {hop}</div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ) : (
                /* Empty State Hint */
                <div className="h-full flex flex-col items-center justify-center text-center p-6 text-slate-500 space-y-3">
                  <HelpCircle className="w-8 h-8 text-slate-600 animate-pulse" />
                  <div className="text-xs text-slate-400 font-medium">
                    Select a node or transition on the map to inspect its evidence and proof details.
                  </div>
                </div>
              )}
            </div>

            {/* Collapsible Findings Accordion Section (FIX 4) */}
            <div className="border-t border-slate-800 bg-slate-950/90 shrink-0">
              <button
                onClick={() => setFindingsExpanded((prev) => !prev)}
                className="w-full px-4 py-3 flex items-center justify-between text-xs font-bold text-slate-300 hover:bg-slate-900 transition-colors"
              >
                <span>
                  Findings ({findingsData?.broken_unknown_findings?.length ?? 0} broken/unknown,{' '}
                  {findingsData?.code_only_findings?.length ?? 0} code-only)
                </span>
                {findingsExpanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
              </button>

              {findingsExpanded && (
                <div className="max-h-80 flex flex-col border-t border-slate-800">
                  {/* Findings Navigation Tabs */}
                  <div className="flex border-b border-slate-800 bg-slate-950 text-xs font-medium shrink-0">
                    <button
                      className={`flex-1 py-2 text-center border-b-2 transition-colors ${
                        activeTab === 'broken'
                          ? 'border-indigo-500 text-indigo-400 bg-slate-900/50'
                          : 'border-transparent text-slate-400 hover:text-slate-200'
                      }`}
                      onClick={() => setActiveTab('broken')}
                    >
                      Broken ({findingsData?.broken_unknown_findings?.length ?? 0})
                    </button>
                    <button
                      className={`flex-1 py-2 text-center border-b-2 transition-colors ${
                        activeTab === 'code_only'
                          ? 'border-indigo-500 text-indigo-400 bg-slate-900/50'
                          : 'border-transparent text-slate-400 hover:text-slate-200'
                      }`}
                      onClick={() => setActiveTab('code_only')}
                    >
                      Code Only ({findingsData?.code_only_findings?.length ?? 0})
                    </button>
                    <button
                      className={`flex-1 py-2 text-center border-b-2 transition-colors ${
                        activeTab === 'gaps'
                          ? 'border-indigo-500 text-indigo-400 bg-slate-900/50'
                          : 'border-transparent text-slate-400 hover:text-slate-200'
                      }`}
                      onClick={() => setActiveTab('gaps')}
                    >
                      Gaps ({findingsData?.recovery_gaps?.length ?? 0})
                    </button>
                  </div>

                  {/* Findings Tab Content */}
                  <div className="overflow-y-auto p-3 space-y-2 text-xs max-h-60">
                    {activeTab === 'broken' && (
                      <div className="space-y-2">
                        {findingsData?.broken_unknown_findings?.map((f: FlowIntegrityFindingRow) => (
                          <div
                            key={f.id}
                            className="p-2.5 rounded-lg bg-slate-950 border border-slate-800 hover:border-slate-700 transition-colors space-y-1"
                          >
                            <div className="flex items-center justify-between">
                              <span
                                className={`px-1.5 py-0.5 rounded text-[10px] font-mono font-bold ${
                                  VERDICT_STYLES[f.verdict]?.bg
                                } ${VERDICT_STYLES[f.verdict]?.text}`}
                              >
                                {f.verdict}
                              </span>
                              {f.ai_bucket && (
                                <span className="text-[10px] font-mono text-purple-400 bg-purple-950/80 px-1.5 py-0.5 rounded">
                                  {f.ai_bucket}
                                </span>
                              )}
                            </div>
                            <div className="font-medium text-slate-200">{f.bd_span}</div>
                            <div className="text-[11px] text-slate-400">{f.reason}</div>
                          </div>
                        ))}

                        {/* UNKNOWN transitions — shown collapsed (below route altitude / external boundary) */}
                        {findingsData && findingsData.collapsed_unknown_count > 0 && (
                          <div className="rounded-lg bg-slate-900/60 border border-slate-800/80 overflow-hidden">
                            <button
                              onClick={() => setShowUnknown((s) => !s)}
                              className="w-full flex items-center justify-between px-2.5 py-2 text-slate-400 font-mono text-[11px] hover:bg-slate-900"
                            >
                              <span>
                                ℹ {findingsData.collapsed_unknown_count} transitions below route altitude / external (not evaluated)
                              </span>
                              <span>{showUnknown ? '▾' : '▸'}</span>
                            </button>
                            {showUnknown && (
                              <div className="space-y-1 px-2 pb-2">
                                {findingsData.unknown_findings?.map((f: FlowIntegrityFindingRow) => (
                                  <div
                                    key={f.id}
                                    className="p-2 rounded bg-slate-950/70 border border-slate-800/60 space-y-0.5"
                                  >
                                    <div className="flex items-center justify-between">
                                      <span className="px-1.5 py-0.5 rounded text-[10px] font-mono font-bold bg-slate-800 text-slate-400">
                                        UNKNOWN
                                      </span>
                                    </div>
                                    <div className="text-slate-300 text-[11px]">{f.bd_span}</div>
                                    <div className="text-[10px] text-slate-500">{f.reason}</div>
                                  </div>
                                ))}
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    )}

                    {activeTab === 'code_only' && (
                      <div className="space-y-2">
                        {findingsData?.code_only_findings?.map((f: FlowIntegrityFindingRow) => (
                          <div
                            key={f.id}
                            className="p-2.5 rounded-lg bg-slate-950 border border-blue-900/40 space-y-1"
                          >
                            <div className="flex items-center justify-between">
                              <span className="font-mono text-blue-300 font-semibold">{f.rel_path}</span>
                              <span className="text-[10px] text-slate-400 uppercase font-mono">{f.node_kind}</span>
                            </div>
                            <div className="text-[11px] text-slate-400">{f.reason}</div>
                          </div>
                        ))}
                      </div>
                    )}

                    {activeTab === 'gaps' && (
                      <div>
                        {findingsData?.recovery_gaps && findingsData.recovery_gaps.length > 0 ? (
                          findingsData.recovery_gaps.map((f: FlowIntegrityFindingRow) => (
                            <div key={f.id} className="p-2.5 rounded-lg bg-slate-950 border border-orange-900/40 space-y-1">
                              <div className="font-medium text-orange-300">{f.bd_span}</div>
                              <div className="text-[11px] text-slate-400">{f.reason}</div>
                            </div>
                          ))
                        ) : (
                          <div className="text-slate-500 text-center py-4">No recovery gap findings</div>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
    </div>
  )
}
