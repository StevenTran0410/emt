import React, { useState, useMemo, useCallback, useEffect, useRef } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  Handle,
  Position,
  useNodesState,
  useEdgesState,
  applyNodeChanges,
  useReactFlow,
  BaseEdge,
  EdgeLabelRenderer,
  getSmoothStepPath,
  type Node,
  type Edge,
  type NodeProps,
  type EdgeProps,
  MarkerType
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import {
  Workflow,
  FolderOpen,
  Play,
  Zap,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  FileText,
  Layers,
  X,
  Sparkles,
  Activity,
  ChevronDown,
  ChevronRight,
  GitBranch,
  Info
} from 'lucide-react'
import type {
  BdFlowNode,
  BdFlowEdge,
  BdFlowOverlayClaim,
  BdFlowActivityEvent,
  DocGraphClusterSummary,
  LocalRepo,
  BusinessFlow,
  BusinessFlowStep,
  BusinessFlowBranch
} from '../../types/electron'
import { GroupHullNode } from '../linked-graph/_shared/GroupHullNode'
import { applyDagreLayout, projectBusinessFlowSkeleton } from '../graph/layout'

const NODE_KIND_STYLES: Record<string, { bg: string; border: string; text: string; label: string }> = {
  step: { bg: 'bg-sky-950/80', border: 'border-sky-500', text: 'text-sky-300', label: 'Step' },
  decision: { bg: 'bg-cyan-950/80', border: 'border-cyan-400', text: 'text-cyan-200', label: 'Decision' },
  event: { bg: 'bg-purple-950/80', border: 'border-purple-500', text: 'text-purple-300', label: 'Event' },
  job_step: { bg: 'bg-amber-950/80', border: 'border-amber-500', text: 'text-amber-300', label: 'Job Step' },
  asset: { bg: 'bg-emerald-950/80', border: 'border-emerald-500', text: 'text-emerald-300', label: 'Asset' }
}

function CustomBdFlowNode({ data }: NodeProps): React.ReactElement {
  const rawNode = data.node as BdFlowNode
  const kindStyle = NODE_KIND_STYLES[rawNode.node_kind] ?? {
    bg: 'bg-slate-900',
    border: 'border-slate-600',
    text: 'text-slate-300',
    label: rawNode.node_kind
  }

  const isDecision = rawNode.node_kind === 'decision'

  return (
    <div
      className={`relative px-3 py-2 rounded-lg text-xs shadow-md backdrop-blur-sm min-w-[160px] max-w-[240px] transition-all duration-150 hover:ring-2 hover:ring-indigo-400/50 ${
        kindStyle.bg
      } ${kindStyle.border} ${isDecision ? 'border-2 border-dashed' : 'border'}`}
    >
      <Handle type="target" position={Position.Top} className="!bg-slate-500 !w-2 !h-2 !border-0" />
      <Handle type="source" position={Position.Bottom} className="!bg-slate-500 !w-2 !h-2 !border-0" />
      <div>
        <div className="flex items-center justify-between gap-1 mb-1">
          <span className={`font-semibold px-1.5 py-0.5 rounded text-[10px] bg-slate-900/60 ${kindStyle.text}`}>
            {kindStyle.label}
          </span>
          <div className="flex items-center gap-1">
            {rawNode.guard_text && (
              <span title={`Guard: ${rawNode.guard_text}`}>
                <Zap className="w-3 h-3 text-amber-400" />
              </span>
            )}
            <span className="px-1 py-0.2 text-[9px] font-mono bg-slate-800/80 text-slate-300 rounded border border-slate-700">
              {rawNode.provenance_tier}
            </span>
          </div>
        </div>

        <div className="font-medium text-slate-100 truncate" title={rawNode.label || rawNode.local_id || ''}>
          {rawNode.local_id || rawNode.label}
        </div>

        {rawNode.binding && (
          <div className="text-[10px] text-slate-400 font-mono mt-0.5 truncate" title={`Binding: ${rawNode.binding}`}>
            {rawNode.binding_type ? `(${rawNode.binding_type}) ` : ''}
            {rawNode.binding}
          </div>
        )}
      </div>
    </div>
  )
}

export const BUSINESS_VERDICT_COLORS: Record<string, string> = {
  MATCH: '#10b981',
  PARTIAL: '#f59e0b',
  BROKEN: '#ef4444',
  UNKNOWN: '#6b7280'
}

export function BusinessStepNode({ data }: NodeProps): React.ReactElement {
  const step = data.step as BusinessFlowStep
  const flow = data.flow as BusinessFlow
  const name = (data.name as string) || step?.name || 'Step'
  const isLlm = flow?.origin === 'llm'
  const verdict = data.verdict as string | undefined
  const verdictColor = verdict ? (BUSINESS_VERDICT_COLORS[verdict] || BUSINESS_VERDICT_COLORS.UNKNOWN) : null

  const accentColor = verdictColor || (isLlm ? '#6366f1' : '#f59e0b')
  const borderStyle = verdictColor
    ? { borderColor: verdictColor, borderWidth: '1.5px' }
    : {}

  return (
    <div
      className={`relative px-3.5 py-2.5 rounded-lg text-xs shadow-lg backdrop-blur-md w-[220px] h-[80px] overflow-hidden bg-slate-900 border transition-all duration-150 hover:border-indigo-400 hover:ring-2 hover:ring-indigo-500/30 ${
        !verdictColor ? 'border-slate-700/80' : ''
      }`}
      style={borderStyle}
    >
      <Handle type="target" position={Position.Top} className="!bg-slate-400 !w-2 !h-2 !border-0" />
      <Handle type="source" position={Position.Bottom} className="!bg-slate-400 !w-2 !h-2 !border-0" />
      <div className="flex items-center gap-2">
        <span
          className="w-1.5 h-7 rounded-full shrink-0"
          style={{ backgroundColor: accentColor }}
        />
        <div className="min-w-0 flex-1">
          <div className="font-semibold text-slate-100 text-xs leading-tight multiline-clamp-2" title={name}>
            {name}
          </div>
          {step?.functionality && (
            <div className="text-[10px] text-slate-400 truncate mt-0.5" title={step.functionality}>
              {step.functionality}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export function BranchEndNode({ data }: NodeProps): React.ReactElement {
  const kind = (data.kind as string) || 'END'
  let bg = 'bg-slate-800 text-slate-300 border-slate-700'
  if (kind === 'SUCCESS') bg = 'bg-emerald-950/80 text-emerald-300 border-emerald-700'
  else if (kind === 'FAILURE') bg = 'bg-rose-950/80 text-rose-300 border-rose-700'
  else if (kind === 'ERROR') bg = 'bg-amber-950/80 text-amber-300 border-amber-700'

  return (
    <div className={`px-2.5 py-1 rounded-full text-[10px] font-semibold border text-center shadow-xs ${bg}`}>
      <Handle type="target" position={Position.Top} className="!bg-slate-500 !w-1.5 !h-1.5 !border-0" />
      {kind}
    </div>
  )
}

export function BusinessBranchEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  markerEnd,
  style,
  data
}: EdgeProps): React.ReactElement {
  const { setEdges, getViewport } = useReactFlow()
  const verdict = data?.verdict as string | undefined
  const verdictColor = verdict ? (BUSINESS_VERDICT_COLORS[verdict] || BUSINESS_VERDICT_COLORS.UNKNOWN) : null

  // Clean orthogonal smoothstep edge (unchanged look). The label is draggable; when moved off the
  // edge, a thin leader line links it back to its arrow so you can tell which arrow it belongs to.
  const [edgePath, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    borderRadius: 8
  })
  const offset = (data?.labelOffset as { dx: number; dy: number }) || { dx: 0, dy: 0 }
  const posX = labelX + offset.dx
  const posY = labelY + offset.dy
  const moved = offset.dx !== 0 || offset.dy !== 0
  const stroke = verdictColor || (data?.strokeColor as string) || '#6b7280'
  const edgeStyle = verdictColor ? { ...style, stroke: verdictColor } : style
  const label = data?.label as string | undefined
  const full = data?.guard as string | undefined
  const dragging = useRef(false)

  const onPointerDown = (e: React.PointerEvent) => {
    e.stopPropagation()
    dragging.current = true
    ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
  }
  const onPointerMove = (e: React.PointerEvent) => {
    if (!dragging.current) return
    const zoom = getViewport().zoom || 1
    const ddx = e.movementX / zoom
    const ddy = e.movementY / zoom
    setEdges((eds) =>
      eds.map((ed) => {
        if (ed.id !== id) return ed
        const cur = (ed.data as any)?.labelOffset || { dx: 0, dy: 0 }
        return { ...ed, data: { ...(ed.data as any), labelOffset: { dx: cur.dx + ddx, dy: cur.dy + ddy } } }
      })
    )
  }
  const onPointerUp = (e: React.PointerEvent) => {
    dragging.current = false
    try {
      ;(e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId)
    } catch {}
  }

  return (
    <>
      <BaseEdge id={id} path={edgePath} markerEnd={markerEnd} style={edgeStyle} />
      {moved && label && (
        <path
          d={`M ${labelX},${labelY} L ${posX},${posY}`}
          stroke={stroke}
          strokeWidth={1}
          strokeDasharray="3 3"
          fill="none"
          opacity={0.5}
        />
      )}
      {label && (
        <EdgeLabelRenderer>
          <div
            className="nodrag nopan"
            title={full}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            style={{
              position: 'absolute',
              transform: `translate(-50%, -50%) translate(${posX}px, ${posY}px)`,
              background: '#0f172a',
              color: '#e2e8f0',
              fontSize: 10,
              fontWeight: 500,
              lineHeight: '14px',
              padding: '2px 6px',
              borderRadius: 4,
              border: `1px solid ${stroke}`,
              maxWidth: 160,
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              pointerEvents: 'all',
              cursor: 'grab',
              userSelect: 'none'
            }}
          >
            {label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  )
}

export const nodeTypes = {
  bdFlowNode: CustomBdFlowNode,
  businessStep: BusinessStepNode,
  branchEnd: BranchEndNode,
  group: GroupHullNode
}

export const edgeTypes = {
  businessBranch: BusinessBranchEdge
}

/** Live per-chunk state accumulated from the bd-flow/build-stream SSE events. */
interface ChunkActivityState {
  chunk: string
  region: string
  attempt: 1 | 2
  thinking: string
  answer: string
  outcome: 'ok' | 'empty' | 'transport' | 'nonjson' | null
  claims: number | null
}

const OUTCOME_STYLES: Record<string, { bg: string; text: string; border: string; label: string }> = {
  ok: { bg: 'bg-emerald-950/60', text: 'text-emerald-300', border: 'border-emerald-800/50', label: 'OK' },
  empty: { bg: 'bg-slate-800/60', text: 'text-slate-400', border: 'border-slate-700', label: 'Empty' },
  transport: { bg: 'bg-rose-950/60', text: 'text-rose-300', border: 'border-rose-800/50', label: 'Transport Error' },
  nonjson: { bg: 'bg-amber-950/60', text: 'text-amber-300', border: 'border-amber-800/50', label: 'Non-JSON' }
}

function ActivityChunkCard({ activity }: { activity: ChunkActivityState }): React.ReactElement {
  const [expanded, setExpanded] = useState(true)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!expanded) return
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [activity.answer, activity.thinking, expanded])

  const outcomeStyle = activity.outcome ? OUTCOME_STYLES[activity.outcome] : null

  return (
    <div className="flex flex-col bg-slate-950 border border-slate-800 rounded-lg overflow-hidden text-[11px]">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="flex items-center justify-between gap-2 px-2.5 py-1.5 bg-slate-900/60 border-b border-slate-800 shrink-0 text-left hover:bg-slate-900"
      >
        <div className="flex items-center gap-1.5 min-w-0">
          {expanded ? (
            <ChevronDown className="w-3 h-3 text-slate-500 shrink-0" />
          ) : (
            <ChevronRight className="w-3 h-3 text-slate-500 shrink-0" />
          )}
          <span className="font-mono text-indigo-300 truncate shrink-0" title={activity.chunk}>
            {activity.chunk}
          </span>
          <span className="px-1 py-0.5 rounded text-[9px] bg-slate-800 text-slate-400 truncate">
            {activity.region}
          </span>
          <span className="px-1 py-0.5 rounded text-[9px] bg-slate-800 text-slate-400 shrink-0">
            attempt {activity.attempt}
          </span>
        </div>
        {outcomeStyle ? (
          <span
            className={`px-1.5 py-0.5 rounded text-[9px] border shrink-0 ${outcomeStyle.bg} ${outcomeStyle.text} ${outcomeStyle.border}`}
          >
            {outcomeStyle.label}
            {activity.claims !== null ? ` · ${activity.claims}` : ''}
          </span>
        ) : (
          <span className="inline-block animate-spin rounded-full h-2.5 w-2.5 border-2 border-indigo-400 border-t-transparent shrink-0" />
        )}
      </button>
      {expanded && (
        <div ref={scrollRef} className="min-h-[70px] max-h-[220px] overflow-y-auto p-2 space-y-1.5">
          {activity.thinking && (
            <p className="italic text-slate-500 whitespace-pre-wrap break-words">{activity.thinking}</p>
          )}
          <pre className="font-mono text-slate-300 whitespace-pre-wrap break-words">{activity.answer}</pre>
        </div>
      )}
    </div>
  )
}

export function BdFlowScreen(): React.ReactElement {
  const [bdPath, setBdPath] = useState<string>('')
  const [llmEnabled, setLlmEnabled] = useState<boolean>(false)
  const [loading, setLoading] = useState<boolean>(false)
  const [error, setError] = useState<string | null>(null)

  // Cluster & Snapshot selection state
  const [clusters, setClusters] = useState<DocGraphClusterSummary[]>([])
  const [selectedClusterId, setSelectedClusterId] = useState<string>('')
  const [snapshots, setSnapshots] = useState<{ id: string; label: string }[]>([])
  const [selectedSnapshotId, setSelectedSnapshotId] = useState<string>('')
  const [boundSnapshotId, setBoundSnapshotId] = useState<string | null>(null)

  const [activeClusterId, setActiveClusterId] = useState<string | null>(null)
  const [clusterName, setClusterName] = useState<string | null>(null)
  const [nodes, setNodes] = useState<BdFlowNode[]>([])
  const [edges, setEdges] = useState<BdFlowEdge[]>([])
  const [businessFlows, setBusinessFlows] = useState<BusinessFlow[]>([])
  const [overlayClaims, setOverlayClaims] = useState<BdFlowOverlayClaim[]>([])
  const [overlayCounts, setOverlayCounts] = useState<{ P1: number; P2: number; REJECTED: number } | null>(null)

  const [viewMode, setViewMode] = useState<'business_flows' | 'detailed'>('business_flows')
  const [selectedNode, setSelectedNode] = useState<BdFlowNode | null>(null)
  const [selectedStep, setSelectedStep] = useState<BusinessFlowStep | null>(null)
  const [selectedBlock, setSelectedBlock] = useState<BusinessFlow | null>(null)
  const [activeTab, setActiveTab] = useState<'graph' | 'overlay'>('graph')

  // React Flow stateful nodes & edges
  const [rfNodes, setRfNodes] = useNodesState<Node>([])
  const [rfEdges, setRfEdges, onEdgesChange] = useEdgesState<Edge>([])

  // LLM Activity panel refs
  const [activityOpen, setActivityOpen] = useState<boolean>(false)
  const [activityMap, setActivityMap] = useState<Record<string, ChunkActivityState>>({})
  const [activityOrder, setActivityOrder] = useState<string[]>([])
  const activityMapRef = useRef<Record<string, ChunkActivityState>>({})
  const activityOrderRef = useRef<string[]>([])
  const activityFrameRef = useRef<number | null>(null)
  const activityHandlerRef = useRef<((evt: BdFlowActivityEvent) => void) | null>(null)

  const fetchClusterList = useCallback(async () => {
    try {
      const cls = await window.api.docGraph.listClusters()
      setClusters(cls || [])
    } catch (e) {
      console.error('Failed to list clusters:', e)
    }
  }, [])

  const fetchSnapshotList = useCallback(async () => {
    try {
      const repos: LocalRepo[] = await window.api.folder.list()
      const list: { id: string; label: string }[] = []
      repos.forEach((r) => {
        if (r.active_snapshot_id) {
          const short = r.active_snapshot_id.slice(0, 8)
          list.push({
            id: r.active_snapshot_id,
            label: `${r.name || 'Repo'} (${short})`
          })
        }
      })
      setSnapshots(list)
      setSelectedSnapshotId((prev) => prev || list[0]?.id || '')
    } catch (e) {
      console.error('Failed to list repos for snapshots:', e)
    }
  }, [])

  useEffect(() => {
    void fetchClusterList()
    void fetchSnapshotList()
  }, [fetchClusterList, fetchSnapshotList])

  const loadCluster = useCallback(
    async (clusterId: string) => {
      if (!clusterId) return
      setLoading(true)
      setError(null)
      setSelectedNode(null)
      setSelectedStep(null)
      setSelectedBlock(null)
      try {
        const [summaryData, flowData, ovData] = await Promise.all([
          window.api.docGraph.summary(clusterId),
          window.api.docGraph.bdFlowGet(clusterId),
          window.api.docGraph.bdFlowOverlayGet(clusterId)
        ])
        setActiveClusterId(clusterId)
        setSelectedClusterId(clusterId)
        setClusterName(summaryData.cluster_name)
        setNodes(flowData.nodes || [])
        setEdges(flowData.edges || [])
        const bFlows = flowData.business_flows || []
        setBusinessFlows(bFlows)
        if (bFlows.length > 0) {
          setViewMode('business_flows')
        } else {
          setViewMode('detailed')
        }
        setOverlayClaims(ovData.claims || [])
        setOverlayCounts(ovData.counts || null)

        const boundSnap = summaryData.snapshot_id || null
        setBoundSnapshotId(boundSnap)

        if (boundSnap) {
          setSelectedSnapshotId(boundSnap)
          setSnapshots((prev) => {
            if (prev.some((s) => s.id === boundSnap)) return prev
            return [{ id: boundSnap, label: `Snapshot (${boundSnap.slice(0, 8)})` }, ...prev]
          })
        }
      } catch (err: any) {
        setError(err?.message || 'Failed to load BD flow cluster')
      } finally {
        setLoading(false)
      }
    },
    []
  )

  const flushActivity = useCallback(() => {
    activityFrameRef.current = null
    setActivityMap({ ...activityMapRef.current })
    setActivityOrder([...activityOrderRef.current])
  }, [])

  const scheduleActivityFlush = useCallback(() => {
    if (activityFrameRef.current !== null) return
    activityFrameRef.current = window.requestAnimationFrame(flushActivity)
  }, [flushActivity])

  const cleanupActivityListener = useCallback(() => {
    if (activityHandlerRef.current) {
      window.api.docGraph.offBdFlowActivity(activityHandlerRef.current)
      activityHandlerRef.current = null
    }
  }, [])

  useEffect(() => {
    return () => {
      cleanupActivityListener()
      if (activityFrameRef.current !== null) window.cancelAnimationFrame(activityFrameRef.current)
    }
  }, [cleanupActivityListener])

  const handlePickFile = async () => {
    try {
      const picked = await window.api.docGraph.pickBdFile()
      if (picked) {
        setBdPath(picked)
      }
    } catch (err: any) {
      setError(err?.message || 'Failed to pick file')
    }
  }

  const refreshAfterStream = useCallback(
    async (clusterId: string) => {
      await loadCluster(clusterId)
      void fetchClusterList()
    },
    [loadCluster, fetchClusterList]
  )

  const runStreamingParse = useCallback(async () => {
    cleanupActivityListener()
    activityMapRef.current = {}
    activityOrderRef.current = []
    setActivityMap({})
    setActivityOrder([])
    setActivityOpen(true)

    const onActivityEvent = (evt: BdFlowActivityEvent): void => {
      if (!evt || typeof evt !== 'object') return
      const chunkKey = evt.chunk

      switch (evt.type) {
        case 'chunk_start':
          if (!chunkKey) break
          activityMapRef.current[chunkKey] = {
            chunk: chunkKey,
            region: evt.region || '',
            attempt: evt.attempt === 2 ? 2 : 1,
            thinking: '',
            answer: '',
            outcome: null,
            claims: null
          }
          if (!activityOrderRef.current.includes(chunkKey)) activityOrderRef.current.push(chunkKey)
          scheduleActivityFlush()
          break
        case 'thinking':
          if (chunkKey && activityMapRef.current[chunkKey]) {
            activityMapRef.current[chunkKey].thinking += evt.text || ''
            scheduleActivityFlush()
          }
          break
        case 'content':
          if (chunkKey && activityMapRef.current[chunkKey]) {
            activityMapRef.current[chunkKey].answer += evt.text || ''
            scheduleActivityFlush()
          }
          break
        case 'chunk_done':
          if (chunkKey && activityMapRef.current[chunkKey]) {
            activityMapRef.current[chunkKey].outcome = evt.outcome ?? null
            activityMapRef.current[chunkKey].claims = typeof evt.claims === 'number' ? evt.claims : null
            scheduleActivityFlush()
          }
          break
        case 'done':
          flushActivity()
          cleanupActivityListener()
          setLoading(false)
          if (evt.overlay_counts) setOverlayCounts(evt.overlay_counts)
          if (evt.cluster_id) {
            setActiveClusterId(evt.cluster_id)
            void refreshAfterStream(evt.cluster_id)
          }
          break
        case 'error':
          flushActivity()
          cleanupActivityListener()
          setLoading(false)
          setError(evt.message || 'BD flow streaming build failed')
          break
        default:
          break
      }
    }

    activityHandlerRef.current = onActivityEvent
    window.api.docGraph.onBdFlowActivity(onActivityEvent)

    try {
      await window.api.docGraph.bdFlowBuildStream({
        bd_path: bdPath,
        snapshot_id: selectedSnapshotId || null,
        llm_enabled: true
      })
    } catch (err: any) {
      setError(err?.message || 'Failed to start BD flow streaming build')
      setLoading(false)
      cleanupActivityListener()
    }
  }, [bdPath, selectedSnapshotId, cleanupActivityListener, flushActivity, refreshAfterStream, scheduleActivityFlush])

  const handleParseBd = async () => {
    if (!bdPath) {
      setError('Please select a BD file first')
      return
    }

    setLoading(true)
    setError(null)
    setSelectedNode(null)
    setSelectedStep(null)
    setSelectedBlock(null)

    if (llmEnabled) {
      await runStreamingParse()
      return
    }

    try {
      const buildRes = await window.api.docGraph.bdFlowBuild({
        bd_path: bdPath,
        snapshot_id: selectedSnapshotId || null,
        llm_enabled: false
      })

      setActiveClusterId(buildRes.cluster_id)
      setSelectedClusterId(buildRes.cluster_id)
      setClusterName(buildRes.cluster_name)
      setOverlayCounts(buildRes.overlay_counts ?? null)
      setBoundSnapshotId(selectedSnapshotId || null)

      const flowData = await window.api.docGraph.bdFlowGet(buildRes.cluster_id)
      setNodes(flowData.nodes || [])
      setEdges(flowData.edges || [])
      const bFlows = flowData.business_flows || []
      setBusinessFlows(bFlows)
      if (bFlows.length > 0) {
        setViewMode('business_flows')
      } else {
        setViewMode('detailed')
      }
      setOverlayClaims([])

      void fetchClusterList()
    } catch (err: any) {
      setError(err?.message || 'Failed to parse BD flow')
    } finally {
      setLoading(false)
    }
  }

  // ReactFlow elements generator for Business Flows vs Detailed Parse
  const { flowNodes, flowEdges } = useMemo(() => {
    if (viewMode === 'business_flows' && businessFlows.length > 0) {
      const res = projectBusinessFlowSkeleton(businessFlows)
      return { flowNodes: res.nodes, flowEdges: res.edges }
    }

    if (nodes.length === 0) return { flowNodes: [], flowEdges: [] }

    // Detailed parse mode: Group nodes by sub-report index
    const nodesBySub: Record<number, BdFlowNode[]> = {}
    nodes.forEach((n) => {
      const parts = n.id.split(':')
      const subIx = parts.length >= 4 ? parseInt(parts[3], 10) || 0 : 0
      if (!nodesBySub[subIx]) nodesBySub[subIx] = []
      nodesBySub[subIx].push(n)
    })

    const rawNodeIdSet = new Set(nodes.map((n) => n.id))

    const rfEdges: Edge[] = edges
      .filter((e) => rawNodeIdSet.has(e.src_node_id) && rawNodeIdSet.has(e.dst_node_id))
      .map((e) => {
        const isPrecedes = e.edge_kind === 'precedes'
        const isDependency = e.edge_kind === 'dependency'

        return {
          id: e.id,
          source: e.src_node_id,
          target: e.dst_node_id,
          label: e.label || e.guard_text || undefined,
          type: 'smoothstep',
          animated: isDependency,
          style: {
            stroke: isPrecedes ? '#f59e0b' : isDependency ? '#10b981' : '#94a3b8',
            strokeWidth: isPrecedes ? 2 : 1.5,
            strokeDasharray: isDependency ? '4 4' : undefined
          },
          markerEnd: isPrecedes
            ? { type: MarkerType.ArrowClosed, color: '#f59e0b' }
            : { type: MarkerType.ArrowClosed, color: '#94a3b8' }
        }
      })

    const laidOutNodes: Node[] = []
    let currentLaneX = 40
    const LANE_GAP = 120
    const NODE_WIDTH = 200
    const NODE_HEIGHT = 70

    const sortedSubIxs = Object.keys(nodesBySub)
      .map((k) => parseInt(k, 10))
      .sort((a, b) => a - b)

    sortedSubIxs.forEach((subIx) => {
      const subNodes = nodesBySub[subIx]
      const subNodeIdSet = new Set(subNodes.map((n) => n.id))

      const subRfNodes: Node[] = subNodes.map((n) => ({
        id: n.id,
        type: 'bdFlowNode',
        position: { x: 0, y: 0 },
        width: NODE_WIDTH,
        height: NODE_HEIGHT,
        style: { width: NODE_WIDTH, height: NODE_HEIGHT },
        data: { node: n }
      }))

      const subRfEdges = rfEdges.filter(
        (e) => subNodeIdSet.has(e.source) && subNodeIdSet.has(e.target)
      )

      const laidOutSub = applyDagreLayout(subRfNodes, subRfEdges, {
        rankdir: 'TB',
        nodeWidth: NODE_WIDTH,
        nodeHeight: NODE_HEIGHT,
        ranksep: 80,
        nodesep: 50
      })

      let minX = Infinity
      let maxX = -Infinity

      laidOutSub.forEach((n) => {
        if (n.position.x < minX) minX = n.position.x
        if (n.position.x + NODE_WIDTH > maxX) maxX = n.position.x + NODE_WIDTH
      })

      if (!isFinite(minX) || !isFinite(maxX)) {
        minX = 0
        maxX = NODE_WIDTH
      }

      const subClusterWidth = Math.max(NODE_WIDTH, maxX - minX)

      laidOutSub.forEach((n) => {
        const shiftedX = n.position.x - minX + currentLaneX
        laidOutNodes.push({
          ...n,
          position: { x: shiftedX, y: n.position.y }
        })
      })

      currentLaneX += subClusterWidth + LANE_GAP
    })

    return { flowNodes: laidOutNodes, flowEdges: rfEdges }
  }, [viewMode, businessFlows, nodes, edges])

  useEffect(() => {
    setRfNodes(flowNodes)
  }, [flowNodes, setRfNodes])

  useEffect(() => {
    setRfEdges(flowEdges)
  }, [flowEdges, setRfEdges])

  const PAD = 24
  const TITLE = 36

  const resizeHulls = useCallback((nds: Node[]): Node[] => {
    const membersByFlow = new Map<string, Node[]>()
    for (const n of nds) {
      if (n.type === 'group') continue
      const fid = (n.data as any)?.flowId as string | undefined
      if (!fid) continue
      if (!membersByFlow.has(fid)) membersByFlow.set(fid, [])
      membersByFlow.get(fid)!.push(n)
    }
    return nds.map((n) => {
      if (n.type !== 'group') return n
      const fid = (n.data as any)?.flowId as string | undefined
      const members = (fid && membersByFlow.get(fid)) || []
      if (members.length === 0) return n
      let minX = Infinity
      let minY = Infinity
      let maxX = -Infinity
      let maxY = -Infinity
      for (const m of members) {
        const w = (m.width as number) || (m.style?.width as number) || 220
        const h = (m.height as number) || (m.style?.height as number) || 80
        minX = Math.min(minX, m.position.x)
        minY = Math.min(minY, m.position.y)
        maxX = Math.max(maxX, m.position.x + w)
        maxY = Math.max(maxY, m.position.y + h)
      }
      const x = minX - PAD
      const y = minY - PAD - TITLE
      const width = maxX - minX + PAD * 2
      const height = maxY - minY + PAD * 2 + TITLE
      return {
        ...n,
        position: { x, y },
        style: { ...n.style, width, height },
        data: { ...(n.data as any), width, height }
      }
    })
  }, [])

  const handleNodesChange = useCallback(
    (changes: any) => {
      setRfNodes((nds) => resizeHulls(applyNodeChanges(changes, nds)))
    },
    [setRfNodes, resizeHulls]
  )

  const onNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      if (node.type === 'businessStep') {
        const step = node.data?.step as BusinessFlowStep | undefined
        if (step) {
          setSelectedStep(step)
          setSelectedBlock(null)
          setSelectedNode(null)
        }
      } else if (node.type === 'group') {
        const flowId = node.data?.flowId as string | undefined
        const flow = businessFlows.find((f) => f.id === flowId) || null
        if (flow) {
          setSelectedBlock(flow)
          setSelectedStep(null)
          setSelectedNode(null)
        }
      } else if (node.type === 'bdFlowNode') {
        const raw = node.data?.node as BdFlowNode | undefined
        if (raw) {
          setSelectedNode(raw)
          setSelectedStep(null)
          setSelectedBlock(null)
        }
      }
    },
    [businessFlows]
  )

  const bindingToFlowIds = useMemo(() => {
    const nodeById = new Map(nodes.map((n) => [n.id, n]))
    const map = new Map<string, Set<string>>()
    for (const f of businessFlows) {
      for (const s of f.steps || []) {
        for (const nid of s.source_node_ids || []) {
          const b = nodeById.get(nid)?.binding
          if (b) {
            if (!map.has(b)) map.set(b, new Set())
            map.get(b)!.add(f.id)
          }
        }
      }
    }
    return map
  }, [businessFlows, nodes])

  const selectedBlockDetails = useMemo(() => {
    if (!selectedBlock) return null
    const nodeById = new Map(nodes.map((n) => [n.id, n]))
    const bindingsMap = new Map<string, string | null>()
    let minLine = Infinity
    let maxLine = -Infinity

    for (const step of selectedBlock.steps || []) {
      if (step.doc_line_start != null && step.doc_line_start < minLine) minLine = step.doc_line_start
      if (step.doc_line_end != null && step.doc_line_end > maxLine) maxLine = step.doc_line_end
      for (const nid of step.source_node_ids || []) {
        const n = nodeById.get(nid)
        if (n) {
          if (n.doc_line_start != null && n.doc_line_start > 0 && n.doc_line_start < minLine) minLine = n.doc_line_start
          if (n.doc_line_end != null && n.doc_line_end > 0 && n.doc_line_end > maxLine) maxLine = n.doc_line_end
          if (n.binding) {
            bindingsMap.set(n.binding, n.binding_type || null)
          }
        }
      }
    }

    const docSpan = isFinite(minLine) && isFinite(maxLine) ? `Lines ${minLine}-${maxLine}` : null

    const relatedFlowMap = new Map<string, Set<string>>()
    for (const binding of bindingsMap.keys()) {
      const flowIds = bindingToFlowIds.get(binding)
      if (flowIds) {
        for (const fid of flowIds) {
          if (fid !== selectedBlock.id) {
            if (!relatedFlowMap.has(fid)) relatedFlowMap.set(fid, new Set())
            relatedFlowMap.get(fid)!.add(binding)
          }
        }
      }
    }

    const flowById = new Map(businessFlows.map((f) => [f.id, f]))
    const relatedFlows: { flow: BusinessFlow; sharedBindings: string[] }[] = []
    for (const [fid, bindingsSet] of relatedFlowMap.entries()) {
      const fl = flowById.get(fid)
      if (fl) {
        relatedFlows.push({ flow: fl, sharedBindings: Array.from(bindingsSet) })
      }
    }

    return {
      touchedPrograms: Array.from(bindingsMap.entries()).map(([binding, bType]) => ({ binding, bindingType: bType })),
      docSpan,
      stepCount: selectedBlock.steps?.length || 0,
      branchCount: selectedBlock.branches?.length || 0,
      relatedFlows
    }
  }, [selectedBlock, nodes, businessFlows, bindingToFlowIds])

  const selectedStepFlow = useMemo(() => {
    if (!selectedStep) return null
    return businessFlows.find((f) => f.id === selectedStep.flow_id) || null
  }, [selectedStep, businessFlows])

  const stepBranches = useMemo(() => {
    if (!selectedStep || !selectedStepFlow) return []
    return (selectedStepFlow.branches || []).filter((b) => b.source_step_id === selectedStep.id)
  }, [selectedStep, selectedStepFlow])

  const underlyingNodes = useMemo(() => {
    if (!selectedStep || !selectedStep.source_node_ids) return []
    const idSet = new Set(selectedStep.source_node_ids)
    return nodes.filter((n) => idSet.has(n.id))
  }, [selectedStep, nodes])

  const alsoReferencedNodes = useMemo(() => {
    if (!selectedStep || underlyingNodes.length === 0) return []
    const stepNodeIds = new Set(underlyingNodes.map((n) => n.id))
    const bindings = new Set(underlyingNodes.map((n) => n.binding).filter(Boolean))
    if (bindings.size === 0) return []
    return nodes.filter((n) => !stepNodeIds.has(n.id) && n.binding && bindings.has(n.binding))
  }, [selectedStep, underlyingNodes, nodes])

  const parsedAttributes = useMemo(() => {
    if (!selectedNode || !selectedNode.attributes) return {}
    if (typeof selectedNode.attributes === 'object') return selectedNode.attributes
    try {
      return JSON.parse(selectedNode.attributes as string)
    } catch {
      return {}
    }
  }, [selectedNode])

  const hasFallbackOrigin = useMemo(() => {
    return businessFlows.some((f) => f.origin === 'fallback')
  }, [businessFlows])

  return (
    <div className="h-full flex flex-col bg-slate-950 text-slate-100 overflow-hidden">
      {/* Top Header Bar */}
      <div className="h-16 border-b border-slate-800 bg-slate-900/80 px-6 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-indigo-500/10 border border-indigo-500/20 rounded-lg text-indigo-400">
            <Workflow className="w-5 h-5" />
          </div>
          <div>
            <h1 className="font-semibold text-slate-100 text-sm">BD Flow Explorer</h1>
            <p className="text-xs text-slate-400">Deterministic Business Flow Graph Skeleton</p>
          </div>
        </div>

        {/* Input Controls */}
        <div className="flex items-center gap-3">
          {clusters.length > 0 && (
            <select
              value={selectedClusterId}
              onChange={(e) => void loadCluster(e.target.value)}
              className="py-1.5 px-2 text-xs bg-slate-950 border border-slate-800 rounded-md text-slate-200 focus:outline-none focus:border-indigo-500 max-w-[180px] truncate"
              title="Load existing cluster from DB"
            >
              <option value="">-- Reload Cluster --</option>
              {clusters.map((c) => (
                <option key={c.cluster_id} value={c.cluster_id}>
                  {c.cluster_name || c.cluster_id}
                </option>
              ))}
            </select>
          )}

          <div className="relative w-72">
            <input
              type="text"
              readOnly
              value={bdPath}
              placeholder="Select BD markdown file..."
              className="w-full pl-3 pr-8 py-1.5 text-xs bg-slate-950 border border-slate-800 rounded-md text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
            />
            <button
              onClick={handlePickFile}
              className="absolute right-1.5 top-1.5 text-slate-400 hover:text-slate-200"
              title="Browse BD file"
            >
              <FolderOpen className="w-4 h-4" />
            </button>
          </div>

          <select
            value={selectedSnapshotId}
            onChange={(e) => setSelectedSnapshotId(e.target.value)}
            className="py-1.5 px-2 text-xs bg-slate-950 border border-slate-800 rounded-md text-slate-200 focus:outline-none focus:border-indigo-500 max-w-[180px] truncate"
            title="Bind Code Snapshot (optional)"
          >
            <option value="">Snapshot: (none)</option>
            {snapshots.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label}
              </option>
            ))}
          </select>

          <label className="flex items-center gap-2 text-xs text-slate-300 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={llmEnabled}
              onChange={(e) => setLlmEnabled(e.target.checked)}
              className="rounded bg-slate-950 border-slate-700 text-indigo-600 focus:ring-0"
            />
            <Sparkles className="w-3.5 h-3.5 text-amber-400" />
            LLM Overlay
          </label>

          <button
            onClick={handleParseBd}
            disabled={loading || !bdPath}
            className="flex items-center gap-1.5 px-4 py-1.5 bg-indigo-600 hover:bg-indigo-500 disabled:bg-slate-800 disabled:text-slate-500 text-white rounded-md text-xs font-medium transition-colors shadow-sm"
          >
            {loading ? (
              <span className="inline-block animate-spin rounded-full h-3.5 w-3.5 border-2 border-white border-t-transparent" />
            ) : (
              <Play className="w-3.5 h-3.5" />
            )}
            Parse BD
          </button>

          <button
            onClick={() => setActivityOpen(true)}
            disabled={!llmEnabled}
            title={llmEnabled ? 'Show LLM activity panel' : 'Enable LLM Overlay to use Activity view'}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 disabled:bg-slate-900 disabled:text-slate-600 text-slate-200 rounded-md text-xs font-medium transition-colors border border-slate-700 disabled:border-slate-800"
          >
            <Activity className="w-3.5 h-3.5" />
            LLM Activity
          </button>
        </div>
      </div>

      {/* Error Alert */}
      {error && (
        <div className="bg-rose-950/50 border-b border-rose-800/50 px-6 py-2 flex items-center justify-between text-xs text-rose-300">
          <div className="flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-rose-400 shrink-0" />
            <span>{error}</span>
          </div>
          <button onClick={() => setError(null)} className="text-rose-400 hover:text-rose-200">
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* Main Content View */}
      <div className="flex-1 flex overflow-hidden relative min-h-0">
        {/* Graph Area */}
        <div className="flex-1 flex flex-col min-w-0">
          {/* Status Bar / View Mode Switcher */}
          {activeClusterId && (
            <div className="h-10 border-b border-slate-800 bg-slate-900/40 px-6 flex items-center justify-between text-xs shrink-0">
              <div className="flex items-center gap-4 text-slate-300">
                <span className="font-medium text-slate-200">Cluster: {clusterName}</span>
                {boundSnapshotId && (
                  <span className="px-2 py-0.5 rounded text-[11px] font-mono bg-indigo-950/60 text-indigo-300 border border-indigo-800/50">
                    Bound Snapshot: {boundSnapshotId.slice(0, 8)}
                  </span>
                )}
                <span>{businessFlows.length} Business Flows</span>
                <span>{nodes.length} Detailed Nodes</span>

                {viewMode === 'business_flows' && hasFallbackOrigin && (
                  <span className="px-2 py-0.5 rounded text-[10px] font-medium bg-amber-950/60 text-amber-300 border border-amber-800/50 flex items-center gap-1">
                    <Info className="w-3 h-3 text-amber-400" />
                    auto-generated names (no LLM)
                  </span>
                )}
              </div>

              <div className="flex items-center gap-3">
                {businessFlows.length > 0 && (
                  <div className="flex items-center bg-slate-950 rounded border border-slate-800 p-0.5">
                    <button
                      onClick={() => {
                        setViewMode('business_flows')
                        setSelectedNode(null)
                        setSelectedBlock(null)
                      }}
                      className={`px-2.5 py-0.5 rounded text-[11px] font-medium transition-colors ${
                        viewMode === 'business_flows' ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-slate-200'
                      }`}
                    >
                      Business Flows ({businessFlows.length})
                    </button>
                    <button
                      onClick={() => {
                        setViewMode('detailed')
                        setSelectedStep(null)
                        setSelectedBlock(null)
                      }}
                      className={`px-2.5 py-0.5 rounded text-[11px] font-medium transition-colors ${
                        viewMode === 'detailed' ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-slate-200'
                      }`}
                    >
                      Detailed Parse
                    </button>
                  </div>
                )}

                {overlayCounts && (
                  <div className="flex items-center bg-slate-950 rounded border border-slate-800 p-0.5 ml-2">
                    <button
                      onClick={() => setActiveTab('graph')}
                      className={`px-2 py-0.5 rounded text-[11px] font-medium transition-colors ${
                        activeTab === 'graph' ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-slate-200'
                      }`}
                    >
                      Graph View
                    </button>
                    <button
                      onClick={() => setActiveTab('overlay')}
                      className={`px-2 py-0.5 rounded text-[11px] font-medium transition-colors ${
                        activeTab === 'overlay' ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-slate-200'
                      }`}
                    >
                      Overlay Claims ({overlayClaims.length})
                    </button>
                  </div>
                )}
              </div>
            </div>
          )}

          {activeTab === 'graph' ? (
            <div className="flex-1 w-full h-full relative">
              {flowNodes.length > 0 ? (
                <ReactFlow
                  nodes={rfNodes}
                  edges={rfEdges}
                  onNodesChange={handleNodesChange}
                  onEdgesChange={onEdgesChange}
                  nodeTypes={nodeTypes}
                  edgeTypes={edgeTypes}
                  onNodeClick={onNodeClick}
                  fitView
                  fitViewOptions={{ padding: 0.2 }}
                  className="bg-slate-950"
                >
                  <Background color="#334155" gap={20} />
                  <Controls className="bg-slate-900 border-slate-800 text-slate-200 fill-slate-200" />
                  <MiniMap
                    nodeColor={(node) => {
                      if (node.type === 'group') return '#334155'
                      if (node.type === 'businessStep') return '#6366f1'
                      if (node.type === 'branchEnd') return '#6b7280'
                      const raw = node.data?.node as BdFlowNode | undefined
                      if (raw?.node_kind === 'step') return '#0ea5e9'
                      if (raw?.node_kind === 'decision') return '#06b6d4'
                      if (raw?.node_kind === 'event') return '#a855f7'
                      if (raw?.node_kind === 'job_step') return '#f59e0b'
                      if (raw?.node_kind === 'asset') return '#10b981'
                      return '#64748b'
                    }}
                    className="bg-slate-900 border-slate-800"
                  />
                </ReactFlow>
              ) : (
                <div className="h-full flex flex-col items-center justify-center text-slate-500 text-xs">
                  <Workflow className="w-12 h-12 mb-3 stroke-1 text-slate-600" />
                  {businessFlows.length === 0 && activeClusterId ? (
                    <p className="text-amber-400 font-medium">
                      Business flows not built — rebuild the BD to generate them.
                    </p>
                  ) : (
                    <p>Select a Business Design (.md) report file above and click "Parse BD".</p>
                  )}
                </div>
              )}
            </div>
          ) : (
            /* Overlay Claims Table */
            <div className="flex-1 overflow-auto p-6 bg-slate-950">
              <h2 className="text-sm font-semibold text-slate-200 mb-4 flex items-center gap-2">
                <Sparkles className="w-4 h-4 text-amber-400" />
                Extracted LLM Prose Overlay Claims
              </h2>
              <table className="w-full text-left text-xs text-slate-300 border-collapse">
                <thead>
                  <tr className="border-b border-slate-800 bg-slate-900/60 text-slate-400 uppercase text-[10px]">
                    <th className="p-2.5">Tier</th>
                    <th className="p-2.5">Kind</th>
                    <th className="p-2.5">Subject</th>
                    <th className="p-2.5">Relation</th>
                    <th className="p-2.5">Object</th>
                    <th className="p-2.5">Guard / Quote</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/60">
                  {overlayClaims.map((claim) => (
                    <tr key={claim.id} className="hover:bg-slate-900/50">
                      <td className="p-2.5">
                        <span
                          className={`px-1.5 py-0.5 rounded text-[10px] font-mono border ${
                            claim.tier === 'P1'
                              ? 'bg-emerald-950 text-emerald-300 border-emerald-800'
                              : claim.tier === 'P2'
                              ? 'bg-sky-950 text-sky-300 border-sky-800'
                              : 'bg-rose-950 text-rose-300 border-rose-800'
                          }`}
                        >
                          {claim.tier}
                        </span>
                      </td>
                      <td className="p-2.5 font-mono text-slate-400">{claim.kind}</td>
                      <td className="p-2.5 font-mono text-indigo-300 font-semibold">
                        {claim.subject_json?.mention || claim.subject_json?.id || '-'}
                      </td>
                      <td className="p-2.5 font-mono text-purple-300">{claim.relation || '-'}</td>
                      <td className="p-2.5 font-mono text-emerald-300 font-semibold">
                        {claim.object_json?.mention || claim.object_json?.id || '-'}
                      </td>
                      <td className="p-2.5 text-slate-300 max-w-xs truncate" title={claim.citation_json?.quote || ''}>
                        {claim.guard_json?.source_text || claim.citation_json?.quote || '-'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Selected Step Right Panel (Business Flows View) */}
        {selectedStep && viewMode === 'business_flows' && (
          <div className="w-80 border-l border-slate-800 bg-slate-900/90 backdrop-blur-md p-4 flex flex-col gap-4 overflow-y-auto shrink-0 text-xs">
            <div className="flex items-start justify-between">
              <div>
                <div className="flex items-center gap-2">
                  <span className="px-2 py-0.5 text-[10px] font-semibold bg-indigo-950 text-indigo-300 border border-indigo-800 rounded">
                    Business Step
                  </span>
                  {selectedStepFlow && (
                    <span className="text-[10px] text-slate-400 truncate max-w-[140px]" title={selectedStepFlow.name}>
                      {selectedStepFlow.name}
                    </span>
                  )}
                </div>
                <h3 className="font-semibold text-slate-100 mt-1 text-sm">{selectedStep.name}</h3>
              </div>
              <button onClick={() => setSelectedStep(null)} className="text-slate-400 hover:text-slate-200 p-1">
                <X className="w-4 h-4" />
              </button>
            </div>

            <div>
              <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-1">Functionality</label>
              <p className="text-slate-200 bg-slate-950 p-2.5 rounded border border-slate-800 leading-relaxed text-xs">
                {selectedStep.functionality}
              </p>
            </div>

            {stepBranches.length > 0 && (
              <div>
                <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-1">Step Branches</label>
                <div className="space-y-1.5">
                  {stepBranches.map((b) => (
                    <div key={b.id} className="p-2 bg-slate-950 rounded border border-slate-800 flex items-start gap-2">
                      <span className={`px-1.5 py-0.5 rounded text-[9px] font-semibold border shrink-0 ${
                        b.branch_kind === 'SUCCESS' ? 'bg-emerald-950 text-emerald-300 border-emerald-700' :
                        b.branch_kind === 'FAILURE' ? 'bg-rose-950 text-rose-300 border-rose-700' :
                        b.branch_kind === 'ERROR' ? 'bg-amber-950 text-amber-300 border-amber-700' :
                        'bg-slate-800 text-slate-300 border-slate-700'
                      }`}>
                        {b.branch_kind}
                      </span>
                      <span className="text-[11px] text-slate-300 flex-1">{b.guard_description}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div>
              <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-1">
                Underlying BD Evidence ({underlyingNodes.length})
              </label>
              <div className="space-y-1.5 max-h-48 overflow-y-auto">
                {underlyingNodes.map((n) => (
                  <div key={n.id} className="p-2 bg-slate-950 rounded border border-slate-800 text-[11px]">
                    <div className="flex items-center justify-between font-mono text-slate-300">
                      <span className="text-sky-300 font-semibold">{n.local_id || n.binding || n.node_kind}</span>
                      <span className="text-[10px] text-slate-500">L{n.doc_line_start}-{n.doc_line_end}</span>
                    </div>
                    {n.label && <div className="text-slate-400 mt-0.5 truncate">{n.label}</div>}
                    {n.binding && (
                      <div className="text-[10px] text-emerald-400 font-mono mt-0.5">
                        {n.binding_type ? `[${n.binding_type}] ` : ''}{n.binding}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>

            {alsoReferencedNodes.length > 0 && (
              <div>
                <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-1 text-indigo-400">
                  Also Referenced In Other Sections ({alsoReferencedNodes.length})
                </label>
                <div className="space-y-1 max-h-36 overflow-y-auto">
                  {alsoReferencedNodes.map((n) => (
                    <div key={n.id} className="p-1.5 bg-indigo-950/30 rounded border border-indigo-900/50 text-[10px] font-mono text-indigo-200">
                      {n.local_id || n.binding} <span className="text-slate-500">({n.node_kind})</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* Selected Block Right Panel (Business Flows View) */}
        {selectedBlock && viewMode === 'business_flows' && selectedBlockDetails && (
          <div className="w-80 border-l border-slate-800 bg-slate-900/90 backdrop-blur-md p-4 flex flex-col gap-4 overflow-y-auto shrink-0 text-xs">
            <div className="flex items-start justify-between">
              <div>
                <div className="flex items-center gap-1.5 mb-1">
                  <span className="px-2 py-0.5 text-[10px] font-semibold bg-slate-800 text-slate-200 border border-slate-700 rounded">
                    Business Flow
                  </span>
                  <span
                    className={`px-2 py-0.5 text-[10px] font-semibold border rounded ${
                      selectedBlock.origin === 'llm'
                        ? 'bg-indigo-950 text-indigo-300 border-indigo-800'
                        : 'bg-amber-950 text-amber-300 border-amber-800'
                    }`}
                  >
                    {selectedBlock.origin}
                  </span>
                </div>
                <h3 className="font-semibold text-slate-100 text-sm">{selectedBlock.name}</h3>
              </div>
              <button onClick={() => setSelectedBlock(null)} className="text-slate-400 hover:text-slate-200 p-1">
                <X className="w-4 h-4" />
              </button>
            </div>

            {selectedBlock.description && (
              <div>
                <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-1">Description</label>
                <p className="text-slate-200 bg-slate-950 p-2.5 rounded border border-slate-800 leading-relaxed text-xs">
                  {selectedBlock.description}
                </p>
              </div>
            )}

            <div className="p-2.5 bg-slate-950 rounded border border-slate-800 space-y-2">
              <div className="flex items-center justify-between">
                <label className="text-[10px] text-slate-500 uppercase tracking-wider">Touched Programs</label>
                <span className="text-[10px] text-slate-400 font-mono">
                  {selectedBlockDetails.stepCount} steps · {selectedBlockDetails.branchCount} branches
                </span>
              </div>

              {selectedBlockDetails.docSpan && (
                <div className="text-[10px] text-slate-400 font-mono">
                  Doc Span: {selectedBlockDetails.docSpan}
                </div>
              )}

              {selectedBlockDetails.touchedPrograms.length > 0 ? (
                <div className="space-y-1 max-h-36 overflow-y-auto">
                  {selectedBlockDetails.touchedPrograms.map(({ binding, bindingType }, idx) => (
                    <div key={idx} className="p-1.5 bg-slate-900 rounded border border-slate-800/80 font-mono text-[11px] text-emerald-400 truncate">
                      {bindingType ? <span className="text-slate-500 mr-1">({bindingType})</span> : null}
                      {binding}
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-slate-500 text-[11px] italic">No program bindings specified.</p>
              )}
            </div>

            <div>
              <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-1">Related Flows</label>
              {selectedBlockDetails.relatedFlows.length > 0 ? (
                <div className="space-y-1.5 max-h-48 overflow-y-auto">
                  {selectedBlockDetails.relatedFlows.map(({ flow, sharedBindings }) => (
                    <button
                      key={flow.id}
                      onClick={() => setSelectedBlock(flow)}
                      className="w-full text-left p-2 bg-slate-950 hover:bg-slate-800 rounded border border-slate-800 transition-colors block"
                    >
                      <div className="font-semibold text-indigo-300 text-[11px] truncate">{flow.name}</div>
                      <div className="text-[10px] text-slate-400 font-mono mt-0.5 truncate">
                        Shared: {sharedBindings.join(', ')}
                      </div>
                    </button>
                  ))}
                </div>
              ) : (
                <p className="text-slate-500 text-[11px] italic p-2 bg-slate-950 rounded border border-slate-800">
                  No shared-program relationships detected.
                </p>
              )}
            </div>
          </div>
        )}

        {/* Selected Node Right Panel (Detailed Parse View) */}
        {selectedNode && viewMode === 'detailed' && (
          <div className="w-80 border-l border-slate-800 bg-slate-900/90 backdrop-blur-md p-4 flex flex-col gap-4 overflow-y-auto shrink-0 text-xs">
            <div className="flex items-start justify-between">
              <div>
                <span className="px-2 py-0.5 text-[10px] font-semibold bg-sky-950 text-sky-300 border border-sky-800 rounded">
                  {selectedNode.node_kind}
                </span>
                <h3 className="font-semibold text-slate-100 mt-1 text-sm">{selectedNode.local_id || selectedNode.label}</h3>
              </div>
              <button onClick={() => setSelectedNode(null)} className="text-slate-400 hover:text-slate-200 p-1">
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="space-y-3">
              {selectedNode.label && (
                <div>
                  <label className="text-[10px] text-slate-500 uppercase tracking-wider">Label</label>
                  <p className="text-slate-200 font-medium">{selectedNode.label}</p>
                </div>
              )}

              {selectedNode.binding && (
                <div>
                  <label className="text-[10px] text-slate-500 uppercase tracking-wider">Binding</label>
                  <p className="font-mono text-emerald-400 break-all">
                    {selectedNode.binding_type ? `[${selectedNode.binding_type}] ` : ''}
                    {selectedNode.binding}
                  </p>
                </div>
              )}

              {selectedNode.source_locator && (
                <div>
                  <label className="text-[10px] text-slate-500 uppercase tracking-wider">Source Locator</label>
                  <p className="font-mono text-amber-300">{selectedNode.source_locator}</p>
                </div>
              )}

              {selectedNode.guard_text && (
                <div>
                  <label className="text-[10px] text-slate-500 uppercase tracking-wider">Guard / Condition</label>
                  <p className="text-amber-200 bg-amber-950/40 p-2 rounded border border-amber-800/40 text-[11px]">
                    {selectedNode.guard_text}
                  </p>
                </div>
              )}

              <div>
                <label className="text-[10px] text-slate-500 uppercase tracking-wider">Doc Line Span</label>
                <p className="font-mono text-slate-300">
                  Line {selectedNode.doc_line_start} to {selectedNode.doc_line_end}
                </p>
              </div>

              {Object.keys(parsedAttributes).length > 0 && (
                <div className="pt-3 border-t border-slate-800">
                  <label className="text-[10px] text-slate-500 uppercase tracking-wider mb-2 block">
                    Attributes
                  </label>
                  <div className="space-y-2 bg-slate-950 p-2.5 rounded border border-slate-800">
                    {Object.entries(parsedAttributes).map(([key, val]) => (
                      <div key={key}>
                        <span className="text-[10px] text-slate-400 font-mono block uppercase">{key}</span>
                        <div className="text-slate-200 font-mono text-[11px] break-words">
                          {Array.isArray(val) ? (
                            <ul className="list-disc pl-4 space-y-0.5 text-slate-300">
                              {val.map((item, idx) => (
                                <li key={idx}>{String(item)}</li>
                              ))}
                            </ul>
                          ) : typeof val === 'object' ? (
                            <pre className="text-[10px] text-slate-400 overflow-x-auto">
                              {JSON.stringify(val, null, 2)}
                            </pre>
                          ) : (
                            String(val)
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* LLM Activity panel */}
      {activityOpen && (
        <div className="h-72 border-t border-slate-800 bg-slate-900/95 backdrop-blur-md flex flex-col shrink-0">
          <div className="h-9 px-4 flex items-center justify-between border-b border-slate-800 shrink-0">
            <div className="flex items-center gap-2 text-xs font-semibold text-slate-200">
              <Sparkles className="w-3.5 h-3.5 text-amber-400" />
              LLM Activity
              {loading && llmEnabled && (
                <span className="inline-block animate-spin rounded-full h-3 w-3 border-2 border-indigo-400 border-t-transparent" />
              )}
              <span className="text-slate-500 font-normal">
                {activityOrder.length} chunk{activityOrder.length === 1 ? '' : 's'}
              </span>
            </div>
            <button
              onClick={() => setActivityOpen(false)}
              className="text-slate-400 hover:text-slate-200 p-1 rounded hover:bg-slate-800"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
          <div className="flex-1 overflow-y-auto p-3 grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3 auto-rows-min">
            {activityOrder.length === 0 ? (
              <div className="col-span-full h-full flex items-center justify-center text-slate-500 text-xs">
                Waiting for the overlay to start streaming…
              </div>
            ) : (
              activityOrder.map((key) => {
                const activity = activityMap[key]
                return activity ? <ActivityChunkCard key={key} activity={activity} /> : null
              })
            )}
          </div>
        </div>
      )}
    </div>
  )
}

export default BdFlowScreen
