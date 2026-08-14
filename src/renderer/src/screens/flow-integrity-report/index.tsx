import React, { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ShieldCheck,
  Zap,
  RefreshCw,
  X,
  AlertTriangle,
  CheckCircle,
  Share2,
  Sparkles,
  ChevronDown,
  ChevronRight,
  Info,
  Layers,
  ArrowRight,
  FileCode2
} from 'lucide-react'
import type {
  DocGraphClusterSummary,
  FlowIntegrityMapResponse,
  FlowIntegrityFindingsResponse,
  FlowIntegrityMapNode,
  FlowIntegrityFindingRow,
  FlowIntegrityExecutiveSummary,
  BusinessFlowRollup,
  BusinessUnitDetail
} from '../../types/electron'

interface LocalRepo {
  id: string
  name: string
  active_snapshot_id?: string | null
}

type FlowCoverageCategory = 'full' | 'partial' | 'missing'

// TICKET P5-UI: flow-level coverage category, computed from units_matched/total_expected (steps +
// branches). Contradictions (units_broken > 0) are a SEPARATE axis, not a category — a flow can be
// "full" coverage and still carry a red contradiction badge.
function flowCoverage(flow: BusinessFlowRollup): {
  matched: number
  total: number
  category: FlowCoverageCategory
} {
  const matched = flow.units_matched ?? 0
  const total = flow.total_expected ?? flow.steps_total + flow.branches_total
  const category: FlowCoverageCategory =
    total > 0 && matched === total ? 'full' : matched > 0 ? 'partial' : 'missing'
  return { matched, total, category }
}

interface VerdictStatusInfo {
  icon: string
  label: string
  badgeClass: string
  cardBorderClass: string
  iconColorClass: string
}

// TICKET P5-UI-FIX2: per-unit row status is 4-way by verdict, not binary MATCH/Missing — a PARTIAL
// unit has some source backing but is incomplete, and must render as its own amber state rather
// than being lumped in with BROKEN/UNKNOWN as "Missing". Does NOT affect flowCoverage() above,
// which stays MATCH-only for the "fully backed" definition.
function verdictStatus(verdict: string | null | undefined): VerdictStatusInfo {
  switch (verdict) {
    case 'MATCH':
      return {
        icon: '🟢',
        label: 'Backed',
        badgeClass: 'bg-emerald-950 text-emerald-400 border border-emerald-500/30',
        cardBorderClass: 'bg-slate-950/70 border-emerald-900/40 hover:border-emerald-700/60',
        iconColorClass: 'text-emerald-400'
      }
    case 'PARTIAL':
      return {
        icon: '🟡',
        label: 'Partial',
        badgeClass: 'bg-amber-950 text-amber-400 border border-amber-500/30',
        cardBorderClass: 'bg-amber-950/10 border-amber-800/50 hover:border-amber-700/60',
        iconColorClass: 'text-amber-400'
      }
    case 'BROKEN':
      return {
        icon: '⚠',
        label: 'Contradiction',
        badgeClass: 'bg-red-950 text-red-400 border border-red-500/30',
        cardBorderClass: 'bg-red-950/20 border-red-900/50 hover:border-red-700/60',
        iconColorClass: 'text-red-400'
      }
    default:
      return {
        icon: '🔴',
        label: 'Missing',
        badgeClass: 'bg-red-950 text-red-400 border border-red-500/30',
        cardBorderClass: 'bg-red-950/20 border-red-900/50 hover:border-red-700/60',
        iconColorClass: 'text-red-400'
      }
  }
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

function UnitEvidenceBlock({
  citations,
  aspects
}: {
  citations?: BusinessUnitDetail['citations']
  aspects?: BusinessUnitDetail['aspects']
}): React.ReactElement | null {
  const [open, setOpen] = useState(false)

  if (!citations || citations.length === 0) return null

  return (
    <div className="mt-2 pt-2 border-t border-slate-900/80">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 text-[11px] font-mono text-indigo-400 hover:text-indigo-300 transition-colors"
      >
        <FileCode2 className="w-3.5 h-3.5" />
        <span>
          Source Evidence ({citations.length} citation{citations.length > 1 ? 's' : ''})
        </span>
        {open ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
      </button>

      {open && (
        <div className="mt-2 space-y-2 pl-2 border-l-2 border-indigo-900/60">
          {aspects && (
            <div className="flex flex-wrap items-center gap-2 text-[10px] font-mono text-slate-400 pb-1">
              {aspects.target_reachable && (
                <span className="bg-slate-900 px-1.5 py-0.5 rounded border border-slate-800">
                  Target: <strong className="text-slate-200">{aspects.target_reachable}</strong>
                </span>
              )}
              {aspects.guard_equivalence && (
                <span className="bg-slate-900 px-1.5 py-0.5 rounded border border-slate-800">
                  Guard: <strong className="text-slate-200">{aspects.guard_equivalence}</strong>
                </span>
              )}
              {aspects.route_order && (
                <span className="bg-slate-900 px-1.5 py-0.5 rounded border border-slate-800">
                  Order: <strong className="text-slate-200">{aspects.route_order}</strong>
                </span>
              )}
              {aspects.negative_modality && (
                <span className="bg-slate-900 px-1.5 py-0.5 rounded border border-slate-800">
                  Negative: <strong className="text-slate-200">{aspects.negative_modality}</strong>
                </span>
              )}
            </div>
          )}

          {citations.map((c, idx) => (
            <div key={idx} className="space-y-1">
              <div className="flex items-center gap-1.5 text-[10px] font-mono text-indigo-300 font-semibold">
                <span>
                  {c.rel_path}:{c.line_start}-{c.line_end}
                </span>
              </div>
              {c.fetched_text ? (
                <pre className="bg-slate-950 p-2.5 rounded border border-slate-800/80 font-mono text-[10px] text-slate-200 overflow-x-auto max-h-56 leading-snug whitespace-pre-wrap">
                  {c.fetched_text}
                </pre>
              ) : (
                <div className="text-[10px] text-slate-500 italic">No source text fetched</div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export function FlowIntegrityReportScreen(): React.ReactElement {
  const navigate = useNavigate()
  const [clusters, setClusters] = useState<DocGraphClusterSummary[]>([])
  const [selectedClusterId, setSelectedClusterId] = useState<string>('')
  const [snapshotId, setSnapshotId] = useState<string>('')
  const [mapData, setMapData] = useState<FlowIntegrityMapResponse | null>(null)
  const [findingsData, setFindingsData] = useState<FlowIntegrityFindingsResponse | null>(null)
  const [summaryData, setSummaryData] = useState<FlowIntegrityExecutiveSummary | null>(null)
  const [loading, setLoading] = useState<boolean>(false)
  const [running, setRunning] = useState<boolean>(false)
  const [summaryLoading, setSummaryLoading] = useState<boolean>(false)
  const [runError, setRunError] = useState<string | null>(null)
  const [providers, setProviders] = useState<{ id: string; name: string }[]>([])
  const [selectedProviderId, setSelectedProviderId] = useState<string>('')
  const [snapshots, setSnapshots] = useState<{ id: string; label: string }[]>([])
  const [reportUnknownExpanded, setReportUnknownExpanded] = useState<boolean>(false)
  const [expandedFlowIds, setExpandedFlowIds] = useState<Record<string, boolean>>({})
  const [expandedStepIds, setExpandedStepIds] = useState<Record<string, boolean>>({})
  const [selectedFlowId, setSelectedFlowId] = useState<string>('')

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
        .map((r) => ({
          id: r.active_snapshot_id as string,
          label: `${r.name || 'Repo'} (${(r.active_snapshot_id as string).slice(0, 8)})`
        }))
      setSnapshots(list)
      setSnapshotId((prev) => prev || list[0]?.id || '')
    })
  }, [])

  // Load map & findings data
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
      // Prefer the executive summary persisted during a Run — no live LLM call, works on LLM-blocked machines.
      const persistedSummary = (f as any)?.business?.executive_summary
      if (persistedSummary) setSummaryData(persistedSummary)
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

  // TICKET P5-UI: default-select the first flow with contradictions (demo-relevant), else the
  // first Fully/Partial-backed flow, else the first flow — reruns whenever new findings load.
  useEffect(() => {
    const flows = findingsData?.business?.per_flow
    if (!flows || flows.length === 0) {
      setSelectedFlowId('')
      return
    }
    setSelectedFlowId((prev) => {
      if (prev && flows.some((f) => f.flow_id === prev)) return prev
      const withContradictions = flows.find((f) => (f.units_broken ?? 0) > 0)
      if (withContradictions) return withContradictions.flow_id
      const backed = flows.find((f) => flowCoverage(f).category !== 'missing')
      if (backed) return backed.flow_id
      return flows[0].flow_id
    })
  }, [findingsData])

  // Generate executive summary
  const loadSummary = useCallback(async () => {
    if (!selectedClusterId) return
    const c = clusters.find((x) => x.cluster_id === selectedClusterId)
    const snap = snapshotId || c?.snapshot_id || ''
    if (!snap) return

    setSummaryLoading(true)
    try {
      const res = await window.api?.docGraph?.flowIntegrityGenerateSummary?.(
        selectedClusterId,
        snap,
        selectedProviderId || null
      )
      setSummaryData(res || null)
    } catch (e) {
      console.error('Failed to generate flow integrity executive summary:', e)
    } finally {
      setSummaryLoading(false)
    }
  }, [selectedClusterId, snapshotId, selectedProviderId, clusters])

  // No auto-generate on load: the persisted summary (from a prior Run) is shown via loadData above.
  // The "Generate Analysis" button and post-Run flow call loadSummary() explicitly when a live LLM is available.

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
      await loadSummary()
    } catch (e: any) {
      console.error('Failed to run flow integrity pipeline:', e)
      setRunError(e?.message || 'Failed to execute flow integrity pipeline')
    } finally {
      setRunning(false)
    }
  }

  const toggleFlowExpansion = (flowId: string) => {
    setExpandedFlowIds((prev) => ({ ...prev, [flowId]: !prev[flowId] }))
  }

  const biz = findingsData?.business
  const cal = findingsData?.calibration

  // SPEC: "Coverage % = Backed / Total units. Show it plainly; do not dress it up." This is
  // deliberately NOT biz.calibration.match_percentage, which excludes untraced (UNKNOWN) units
  // from its denominator (a calibration-gate metric) and would overstate coverage to a
  // non-technical reader — low coverage is expected and fine for this MVP.
  const coveragePct =
    biz && biz.calibration.total_units > 0
      ? (biz.calibration.match_count / biz.calibration.total_units) * 100
      : 0

  // Helper to group units by flow_id
  const matchedByFlow: Record<string, BusinessUnitDetail[]> = {}
  const contradictedByFlow: Record<string, BusinessUnitDetail[]> = {}
  const unknownByFlow: Record<string, BusinessUnitDetail[]> = {}

  if (biz) {
    for (const u of biz.matched_units || []) {
      if (!matchedByFlow[u.flow_id]) matchedByFlow[u.flow_id] = []
      matchedByFlow[u.flow_id].push(u)
    }
    for (const u of biz.contradicted_units || []) {
      if (!contradictedByFlow[u.flow_id]) contradictedByFlow[u.flow_id] = []
      contradictedByFlow[u.flow_id].push(u)
    }
    for (const u of biz.unknown_units || []) {
      if (!unknownByFlow[u.flow_id]) unknownByFlow[u.flow_id] = []
      unknownByFlow[u.flow_id].push(u)
    }
  }

  // TICKET P5-UI: flow-level coverage category counts (Fully/Partial/Missing) + a separate
  // contradictions tally — contradictions are an axis on top of a category, not a category.
  const flowCoverageSummary = (biz?.per_flow || []).reduce(
    (acc, f) => {
      acc[flowCoverage(f).category]++
      if ((f.units_broken ?? 0) > 0) {
        acc.contradictedFlows++
        acc.contradictedUnits += f.units_broken ?? 0
      }
      return acc
    },
    { full: 0, partial: 0, missing: 0, contradictedFlows: 0, contradictedUnits: 0 }
  )

  // Selector list: Fully -> Partial -> Missing (stable order preserved within each group).
  const categoryRank: Record<FlowCoverageCategory, number> = { full: 0, partial: 1, missing: 2 }
  const flowsSorted = [...(biz?.per_flow || [])].sort(
    (a, b) => categoryRank[flowCoverage(a).category] - categoryRank[flowCoverage(b).category]
  )

  const selectedFlow = biz?.per_flow.find((f) => f.flow_id === selectedFlowId) || null

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
              Business Flow Coverage Dashboard (Dimension 03)
            </h1>
            <p className="text-xs text-slate-400">
              BD-Centric Executive Summary • Traceability Proof-of-Concept • Verification Evidence
            </p>
          </div>

          <button
            onClick={() => navigate('/flow-integrity/graph')}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-indigo-300 rounded border border-slate-700 font-medium text-xs shadow-sm transition-colors ml-4"
          >
            <Share2 className="w-3.5 h-3.5" />
            <span>Open Skeleton Graph</span>
          </button>
        </div>

        {/* Controls: Cluster, Snapshot, Provider, Run button */}
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
            onClick={() => {
              loadData()
              loadSummary()
            }}
            disabled={loading || running}
            className="p-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded border border-slate-700 transition-colors"
            title="Reload Report"
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

      {/* Fallback Banner if business flow pipeline has not been run */}
      {!biz && (
        <div className="px-4 py-2.5 bg-amber-950/60 border-b border-amber-800/60 text-amber-300 text-xs flex items-center gap-2">
          <Info className="w-4 h-4 shrink-0 text-amber-400" />
          <span>
            Business-flow results not built for this cluster — run the pipeline above to generate the BD-centric coverage dashboard.
          </span>
        </div>
      )}

      {/* Main Content Area */}
      <div className="flex-1 overflow-y-auto p-6 space-y-6 bg-slate-950 text-slate-100">
        {biz ? (
          /* ========================================================================= */
          /* BD-CENTRIC COVERAGE DASHBOARD (TICKET P4-4)                              */
          /* ========================================================================= */
          <>
            {/* 1. Header KPI Row */}
            <div className="grid grid-cols-1 md:grid-cols-7 gap-4">
              {/* Coverage % Main Card */}
              <div className="md:col-span-2 p-5 rounded-xl bg-slate-900/90 border border-slate-800 flex flex-col justify-between shadow-md relative overflow-hidden">
                <div className="flex items-center justify-between">
                  <span className="text-xs text-slate-400 font-semibold tracking-wide uppercase">
                    BD Coverage
                  </span>
                  <span className="px-2 py-0.5 rounded text-[10px] font-bold font-mono bg-emerald-950 text-emerald-400 border border-emerald-500/40">
                    {coveragePct.toFixed(1)}% BACKED
                  </span>
                </div>

                <div className="my-3">
                  <div className="text-4xl font-black font-mono text-emerald-400">
                    {coveragePct.toFixed(1)}%
                  </div>
                  <div className="w-full bg-slate-800 h-2 rounded-full overflow-hidden mt-2">
                    <div
                      className="bg-emerald-500 h-full transition-all duration-500"
                      style={{ width: `${Math.min(100, Math.max(0, coveragePct))}%` }}
                    />
                  </div>
                </div>

                <p className="text-[11px] text-slate-400 flex items-center gap-1.5">
                  <Info className="w-3.5 h-3.5 text-slate-500 shrink-0" />
                  <span>MVP scope — coverage reflects how much of the BD the engine can trace to source.</span>
                </p>
              </div>

              {/* KPI Tiles */}
              <div className="p-4 rounded-xl bg-slate-900/90 border border-slate-800 flex flex-col justify-between shadow-md">
                <div className="text-xs text-slate-400 font-medium">Business Flows</div>
                <div className="text-3xl font-extrabold font-mono text-indigo-300">
                  {biz.per_flow.length}
                </div>
                <div className="text-[11px] text-slate-500">Documented flows</div>
              </div>

              <div className="p-4 rounded-xl bg-slate-900/90 border border-slate-800 flex flex-col justify-between shadow-md">
                <div className="text-xs text-slate-400 font-medium">Total Units</div>
                <div className="text-3xl font-extrabold font-mono text-slate-100">
                  {biz.calibration.total_units}
                </div>
                <div className="text-[11px] text-slate-500">Steps & Branches</div>
              </div>

              {/* TICKET P5-UI: flow-level coverage categories replace the unit-bucket tiles below —
                  the headline framing on this screen is flow coverage, not unit buckets. */}
              <div className="p-4 rounded-xl bg-slate-900/90 border border-emerald-900/40 flex flex-col justify-between shadow-md">
                <div className="text-xs text-slate-400 font-medium flex items-center gap-1">
                  <span className="w-2 h-2 rounded-full bg-emerald-400"></span>
                  <span>Fully Backed</span>
                </div>
                <div className="text-3xl font-extrabold font-mono text-emerald-400">
                  {flowCoverageSummary.full}
                </div>
                <div className="text-[11px] text-slate-500">Every step & branch backed</div>
              </div>

              <div className="p-4 rounded-xl bg-slate-900/90 border border-amber-900/40 flex flex-col justify-between shadow-md">
                <div className="text-xs text-slate-400 font-medium flex items-center gap-1">
                  <span className="w-2 h-2 rounded-full bg-amber-400"></span>
                  <span>Partial</span>
                </div>
                <div className="text-3xl font-extrabold font-mono text-amber-400">
                  {flowCoverageSummary.partial}
                </div>
                <div className="text-[11px] text-slate-500">Some units backed</div>
              </div>

              <div className="p-4 rounded-xl bg-slate-900/90 border border-slate-800 flex flex-col justify-between shadow-md">
                <div className="text-xs text-slate-400 font-medium flex items-center gap-1">
                  <span className="w-2 h-2 rounded-full bg-slate-500"></span>
                  <span>Missing</span>
                </div>
                <div className="text-3xl font-extrabold font-mono text-slate-300">
                  {flowCoverageSummary.missing}
                </div>
                <div className="text-[11px] text-slate-500">Nothing backed</div>
              </div>
            </div>

            {/* Contradictions indicator — a SEPARATE axis from coverage category; a flow can be
                mostly/fully backed and still carry a contradiction. */}
            {flowCoverageSummary.contradictedFlows > 0 && (
              <div className="flex items-center gap-2">
                <span className="px-2.5 py-1 rounded text-xs font-bold font-mono bg-red-950 text-red-400 border border-red-500/40">
                  ⚠ Contradictions: {flowCoverageSummary.contradictedFlows} flow
                  {flowCoverageSummary.contradictedFlows > 1 ? 's' : ''} ({flowCoverageSummary.contradictedUnits} unit
                  {flowCoverageSummary.contradictedUnits > 1 ? 's' : ''})
                </span>
              </div>
            )}

            {/* 2. Per-Flow Coverage Cards (Main Section for Non-Technical Stakeholders) */}
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <h2 className="text-base font-bold text-slate-100 flex items-center gap-2">
                  <Layers className="w-5 h-5 text-indigo-400" />
                  <span>Business Flow Verification Cards ({biz.per_flow.length})</span>
                </h2>
                <span className="text-xs text-slate-400">
                  High-level plain-language evidence by business flow
                </span>
              </div>

              <div className="flex flex-col lg:flex-row gap-4 items-start">
                {/* TICKET P5-UI: Flow selector — grouped Fully -> Partial -> Missing. Picking a row
                    drives which single flow's detail card renders on the right (basic stats per row:
                    category chip, units_matched/total_expected, red contradiction count). */}
                <div className="w-full lg:w-80 shrink-0 rounded-xl bg-slate-900/90 border border-slate-800 overflow-hidden shadow-md">
                  <div className="max-h-[70vh] overflow-y-auto divide-y divide-slate-800/60">
                    {flowsSorted.map((flow) => {
                      const { matched, total, category } = flowCoverage(flow)
                      const broken = flow.units_broken ?? 0
                      const isSelected = flow.flow_id === selectedFlowId
                      return (
                        <button
                          key={flow.flow_id}
                          type="button"
                          onClick={() => setSelectedFlowId(flow.flow_id)}
                          className={`w-full text-left p-3 transition-colors border-l-2 ${
                            isSelected
                              ? 'bg-indigo-950/50 border-indigo-500'
                              : 'border-transparent hover:bg-slate-800/40'
                          }`}
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span className="text-xs font-semibold text-slate-100 truncate">
                              {flow.flow_name}
                            </span>
                            <span
                              className={`shrink-0 px-1.5 py-0.5 rounded text-[9px] font-bold font-mono uppercase ${
                                category === 'full'
                                  ? 'bg-emerald-950 text-emerald-400 border border-emerald-500/40'
                                  : category === 'partial'
                                  ? 'bg-amber-950 text-amber-400 border border-amber-500/40'
                                  : 'bg-slate-800 text-slate-400 border border-slate-700'
                              }`}
                            >
                              {category === 'full' ? 'Full' : category === 'partial' ? 'Partial' : 'Missing'}
                            </span>
                          </div>
                          <div className="flex items-center justify-between gap-2 mt-1">
                            <span
                              className={`text-[10px] font-mono font-semibold ${
                                category === 'full'
                                  ? 'text-emerald-400'
                                  : category === 'partial'
                                  ? 'text-amber-400'
                                  : 'text-slate-400'
                              }`}
                            >
                              {flow.steps_backed ?? flow.steps_matched}/{flow.steps_total}
                            </span>
                            {broken > 0 && (
                              <span className="text-[10px] font-bold font-mono text-red-400">
                                ⚠ {broken} contradiction{broken > 1 ? 's' : ''}
                              </span>
                            )}
                          </div>
                        </button>
                      )
                    })}
                  </div>
                </div>

                {/* Selected flow's detail card — reuses the existing per-flow card JSX unchanged,
                    just driven from [selectedFlow] instead of mapping over all of biz.per_flow. */}
                <div className="flex-1 min-w-0 space-y-4">
                {(selectedFlow ? [selectedFlow] : []).map((flow: BusinessFlowRollup) => {
                  const fid = flow.flow_id
                  const isExpanded = expandedFlowIds[fid] ?? false
                  const flowMatched = matchedByFlow[fid] || []
                  const flowContradicted = contradictedByFlow[fid] || []
                  const flowUnknown = unknownByFlow[fid] || []
                  const allUnits = [...flowMatched, ...flowContradicted, ...flowUnknown]
                  // Steps are the focus; each step's outgoing branches (transitions to the next step)
                  // are nested under the step and revealed on expand — not flat rows.
                  const allFlowUnits = allUnits.filter((u) => u.unit_kind === 'step')
                  const branchesBySourceStep: Record<string, BusinessUnitDetail[]> = {}
                  allUnits
                    .filter((u) => u.unit_kind === 'branch')
                    .forEach((b) => {
                      const sid = (b as BusinessUnitDetail).source_step_id
                      if (sid) {
                        if (!branchesBySourceStep[sid]) branchesBySourceStep[sid] = []
                        branchesBySourceStep[sid].push(b)
                      }
                    })

                  return (
                    <div
                      key={fid}
                      className="rounded-xl bg-slate-900/90 border border-slate-800 overflow-hidden shadow-lg"
                    >
                      {/* Card Header */}
                      <div className="p-5 border-b border-slate-800 bg-slate-900/40 flex flex-wrap items-center justify-between gap-4">
                        <div className="space-y-1">
                          <div className="flex items-center gap-3">
                            <h3 className="text-sm font-bold text-slate-100">{flow.flow_name}</h3>
                            {/* TICKET P5-UI-FIX: coverage CATEGORY badge (same as selector chips), not flow.status */}
                            <span
                              className={`px-2 py-0.5 rounded text-[10px] font-bold font-mono ${
                                flowCoverage(flow).category === 'full'
                                  ? 'bg-emerald-950 text-emerald-400 border border-emerald-500/40'
                                  : flowCoverage(flow).category === 'partial'
                                  ? 'bg-amber-950 text-amber-400 border border-amber-500/40'
                                  : 'bg-slate-800 text-slate-400 border border-slate-700'
                              }`}
                            >
                              {flowCoverage(flow).category === 'full'
                                ? 'FULLY BACKED'
                                : flowCoverage(flow).category === 'partial'
                                ? 'PARTIAL'
                                : 'MISSING'}
                            </span>
                            {/* Contradictions are a SEPARATE axis from the category badge above */}
                            {(flow.units_broken ?? 0) > 0 && (
                              <span className="px-2 py-0.5 rounded text-[10px] font-bold font-mono bg-red-950 text-red-400 border border-red-500/40">
                                ⚠ {flow.units_broken} contradiction{flow.units_broken > 1 ? 's' : ''}
                              </span>
                            )}
                          </div>

                          {/* BD Provenance Line */}
                          <div className="text-xs text-indigo-300/90 flex items-center gap-2">
                            <span>
                              Parsed from BD section &apos;
                              <strong className="text-indigo-200">{flow.section_name || 'Business Flow'}</strong>
                              &apos;
                              {flow.doc_line_start && flow.doc_line_end
                                ? `, lines ${flow.doc_line_start}–${flow.doc_line_end}`
                                : ''}
                            </span>
                          </div>
                        </div>

                        {/* Coverage Progress Bar for this Flow — TWO levels: steps (any backing) and
                            the finer units (steps + branches) metric, matching the selector rows. */}
                        <div className="flex items-center gap-4">
                          <div className="text-right">
                            <div
                              className={`text-xs font-bold font-mono ${
                                flowCoverage(flow).category === 'full'
                                  ? 'text-emerald-400'
                                  : flowCoverage(flow).category === 'partial'
                                  ? 'text-amber-400'
                                  : 'text-slate-400'
                              }`}
                            >
                              {flow.steps_backed ?? flow.steps_matched}/{flow.steps_total} steps
                            </div>
                            <div className="w-32 bg-slate-800 h-1.5 rounded-full overflow-hidden mt-1">
                              <div
                                className={`h-full ${
                                  flowCoverage(flow).category === 'full'
                                    ? 'bg-emerald-500'
                                    : flowCoverage(flow).category === 'partial'
                                    ? 'bg-amber-500'
                                    : 'bg-slate-500'
                                }`}
                                style={{
                                  width: `${
                                    flow.steps_total > 0
                                      ? Math.round(
                                          ((flow.steps_backed ?? flow.steps_matched) / flow.steps_total) * 100
                                        )
                                      : 0
                                  }%`
                                }}
                              />
                            </div>
                          </div>

                          <button
                            onClick={() => toggleFlowExpansion(fid)}
                            className="p-1.5 rounded bg-slate-800 hover:bg-slate-700 text-slate-300 transition-colors"
                            title="Toggle flow description & details"
                          >
                            {isExpanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                          </button>
                        </div>
                      </div>

                      {/* Card Narrative Body */}
                      <div className="p-5 space-y-4">
                        {/* Grounded LLM Conclusion Paragraph */}
                        {flow.narrative && (
                          <div className="p-3.5 rounded-lg bg-indigo-950/30 border border-indigo-900/40 text-xs text-slate-200 leading-relaxed">
                            <div className="font-semibold text-indigo-300 mb-1 flex items-center gap-1.5">
                              <Sparkles className="w-3.5 h-3.5 text-indigo-400" />
                              <span>Executive Conclusion:</span>
                            </div>
                            <p>{flow.narrative}</p>
                          </div>
                        )}

                        {/* Expanded Flow Description */}
                        {isExpanded && flow.description && (
                          <div className="p-3 rounded bg-slate-950 border border-slate-800 text-xs text-slate-300">
                            <div className="font-semibold text-slate-400 mb-1">Design Specification Description:</div>
                            <p className="leading-relaxed whitespace-pre-wrap">{flow.description}</p>
                          </div>
                        )}

                        {/* Steps List (Business Language) */}
                        <div className="space-y-2">
                          <div className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
                            Business Steps & Source Code Verification:
                          </div>

                          <div className="space-y-2">
                            {allFlowUnits.length > 0 ? (
                              allFlowUnits.map((u: BusinessUnitDetail) => {
                                // TICKET P5-UI-FIX2: 4-way status by verdict (MATCH/PARTIAL/BROKEN/
                                // else), not binary MATCH/Missing -- a PARTIAL unit was rendering as
                                // "Missing" (and, before the backend fix, wasn't rendered at all).
                                const isMatched = u.verdict === 'MATCH'
                                const status = verdictStatus(u.verdict)
                                // TICKET P5-UI-FIX3: a step's own verdict can be MATCH/PARTIAL while one
                                // of its nested outgoing branches is BROKEN — flag the step so "N
                                // contradiction(s)" in the header points to a visible unit.
                                const hasContradiction =
                                  u.verdict === 'BROKEN' ||
                                  (branchesBySourceStep[u.unit_id] || []).some((b) => b.verdict === 'BROKEN')

                                return (
                                  <div
                                    key={u.unit_id}
                                    className={`p-3 rounded-lg border text-xs transition-colors ${status.cardBorderClass}`}
                                  >
                                    <div className="flex flex-wrap items-start justify-between gap-2">
                                      <div className="flex items-start gap-2 max-w-2xl">
                                        {isMatched ? (
                                          <CheckCircle className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
                                        ) : (
                                          <AlertTriangle className={`w-4 h-4 ${status.iconColorClass} shrink-0 mt-0.5`} />
                                        )}

                                        <div>
                                          <div className="font-bold text-slate-200 flex items-center gap-2 flex-wrap">
                                            <span>{u.unit_name}</span>
                                            <span
                                              className={`px-1.5 py-0.2 rounded text-[9px] font-mono uppercase ${status.badgeClass}`}
                                            >
                                              {status.icon} {status.label}
                                            </span>
                                            {hasContradiction && (
                                              <span className="px-1.5 py-0.2 rounded text-[9px] font-mono uppercase bg-red-950 text-red-400 border border-red-500/40">
                                                ⚠ contradiction
                                              </span>
                                            )}
                                            {!isMatched && u.reason_codes && u.reason_codes.length > 0 && (
                                              <div className="flex flex-wrap items-center gap-1">
                                                {u.reason_codes.map((code, idx) => (
                                                  <span
                                                    key={idx}
                                                    className="px-1.5 py-0.5 rounded text-[9px] font-mono uppercase bg-amber-950/80 text-amber-300 border border-amber-500/30"
                                                  >
                                                    {code}
                                                  </span>
                                                ))}
                                              </div>
                                            )}
                                          </div>

                                          {u.prose && (
                                            <p className="text-slate-400 mt-0.5 text-[11px] leading-relaxed">
                                              {u.prose}
                                            </p>
                                          )}
                                        </div>
                                      </div>

                                      {/* Evidence & Mapped Program Chain */}
                                      <div className="text-right space-y-1">
                                        {isMatched && u.segment?.bindings && u.segment.bindings.length > 0 ? (
                                          <div>
                                            <div className="text-[10px] text-slate-400 font-mono">Handled by:</div>
                                            <div className="font-mono text-emerald-300 font-medium flex items-center justify-end gap-1 flex-wrap">
                                              {u.segment.bindings.map((b, idx) => (
                                                <React.Fragment key={idx}>
                                                  <span className="bg-emerald-950/80 px-1.5 py-0.5 rounded border border-emerald-700/40">
                                                    {b}
                                                  </span>
                                                  {idx < (u.segment?.bindings?.length ?? 0) - 1 && (
                                                    <ArrowRight className="w-3 h-3 text-slate-600" />
                                                  )}
                                                </React.Fragment>
                                              ))}
                                            </div>
                                            {u.segment.rel_paths && u.segment.rel_paths.length > 0 && (
                                              <div className="text-[10px] font-mono text-slate-400 mt-0.5">
                                                Source File: {u.segment.rel_paths.join(', ')}
                                              </div>
                                            )}
                                          </div>
                                        ) : (
                                          <div className="text-[11px] text-slate-400 italic max-w-xs text-right">
                                            {u.reason || 'No source code route found'}
                                          </div>
                                        )}
                                      </div>
                                    </div>

                                    {/* Per-step LLM Reason (Evidence Explanation) */}
                                    {u.reason && (
                                      <div className="mt-2 pt-2 border-t border-slate-900 text-[11px] text-slate-300 flex items-center gap-1.5">
                                        <Info className="w-3 h-3 text-emerald-400 shrink-0" />
                                        <span>{u.reason}</span>
                                      </div>
                                    )}

                                    {/* Expandable Source Evidence Block */}
                                    <UnitEvidenceBlock citations={u.citations} aspects={u.aspects} />

                                    {/* Nested outgoing branches (transitions to the next step), revealed on expand */}
                                    {(branchesBySourceStep[u.unit_id]?.length ?? 0) > 0 &&
                                      (() => {
                                        const stepBranches = branchesBySourceStep[u.unit_id]
                                        const open = expandedStepIds[u.unit_id] ?? hasContradiction
                                        return (
                                          <div className="mt-2 pt-2 border-t border-slate-900">
                                            <button
                                              onClick={() =>
                                                setExpandedStepIds((p) => ({ ...p, [u.unit_id]: !open }))
                                              }
                                              className="flex items-center gap-1 text-[11px] text-slate-400 hover:text-slate-200"
                                            >
                                              {open ? (
                                                <ChevronDown className="w-3 h-3" />
                                              ) : (
                                                <ChevronRight className="w-3 h-3" />
                                              )}
                                              <span>
                                                {stepBranches.length} transition
                                                {stepBranches.length > 1 ? 's' : ''} from this step
                                              </span>
                                            </button>
                                            {open && (
                                              <div className="mt-1.5 space-y-1.5 pl-4">
                                                {stepBranches.map((b) => {
                                                  // TICKET P5-UI-FIX2: 4-way status by verdict, same
                                                  // mapping as the step rows above.
                                                  const bStatus = verdictStatus(b.verdict)
                                                  return (
                                                    <div
                                                      key={b.unit_id}
                                                      className="p-2 rounded bg-slate-900/60 border border-slate-800 text-[11px] space-y-1"
                                                    >
                                                      <div className="flex items-center gap-2 flex-wrap">
                                                        <span className={bStatus.iconColorClass}>{bStatus.icon}</span>
                                                        <span className="px-1.5 py-0.5 rounded bg-slate-800 text-slate-300 font-mono text-[10px]">
                                                          {b.branch_kind || 'OTHER'}
                                                        </span>
                                                        <ArrowRight className="w-3 h-3 text-slate-600 shrink-0" />
                                                        <span className="text-slate-300">
                                                          {b.target_step_name || 'end'}
                                                        </span>
                                                        {b.prose && (
                                                          <span className="text-slate-500 italic truncate">
                                                            — {b.prose}
                                                          </span>
                                                        )}
                                                      </div>
                                                      {b.reason_codes && b.reason_codes.length > 0 && (
                                                        <div className="flex flex-wrap items-center gap-1">
                                                          {b.reason_codes.map((code, idx) => (
                                                            <span
                                                              key={idx}
                                                              className="px-1.5 py-0.5 rounded text-[9px] font-mono uppercase bg-amber-950/80 text-amber-300 border border-amber-500/30"
                                                            >
                                                              {code}
                                                            </span>
                                                          ))}
                                                        </div>
                                                      )}
                                                      <UnitEvidenceBlock citations={b.citations} aspects={b.aspects} />
                                                    </div>
                                                  )
                                                })}
                                              </div>
                                            )}
                                          </div>
                                        )
                                      })()}
                                  </div>
                                )
                              })
                            ) : (
                              <div className="text-xs text-slate-500 py-2">No steps recorded for this flow</div>
                            )}
                          </div>
                        </div>
                      </div>
                    </div>
                  )
                })}
                  {!selectedFlow && (
                    <div className="text-xs text-slate-500 py-8 text-center rounded-xl bg-slate-900/60 border border-slate-800">
                      Select a flow from the list to see its verification detail.
                    </div>
                  )}
                </div>
              </div>
            </div>

            {/* 3. Findings Section — Contradictions (Source does not back BD claim) */}
            {biz.contradicted_units && biz.contradicted_units.length > 0 && (
              <div className="rounded-xl bg-slate-900/90 border border-red-900/50 p-5 space-y-4 shadow-lg">
                <div className="flex items-center justify-between pb-3 border-b border-red-900/40">
                  <div className="flex items-center gap-2">
                    <AlertTriangle className="w-5 h-5 text-red-400" />
                    <h2 className="text-sm font-bold text-slate-100">
                      ⚠️ Contradictions Found ({biz.contradicted_units.length})
                    </h2>
                  </div>
                  <span className="text-xs text-red-300">Source code contradicts BD specification</span>
                </div>

                <div className="space-y-3">
                  {biz.contradicted_units.map((c: BusinessUnitDetail) => (
                    <div
                      key={c.unit_id}
                      className="p-3.5 rounded-lg bg-red-950/30 border border-red-900/60 text-xs space-y-2"
                    >
                      <div className="flex items-center justify-between font-bold text-slate-200">
                        <span>
                          Flow &apos;<strong className="text-red-300">{c.flow_name}</strong>&apos; — Step &apos;
                          {c.unit_name}&apos;
                        </span>
                        <span className="px-2 py-0.5 rounded bg-red-950 text-red-400 border border-red-500/40 font-mono text-[10px]">
                          CONTRADICTION
                        </span>
                      </div>
                      <p className="text-slate-200 font-medium leading-relaxed bg-slate-950/60 p-2 rounded border border-red-900/40">
                        {c.reason}
                      </p>
                      {c.reason_codes && c.reason_codes.length > 0 && (
                        <div className="flex flex-wrap items-center gap-1">
                          {c.reason_codes.map((code, idx) => (
                            <span
                              key={idx}
                              className="px-1.5 py-0.5 rounded text-[9px] font-mono uppercase bg-red-950 text-red-300 border border-red-500/30"
                            >
                              {code}
                            </span>
                          ))}
                        </div>
                      )}
                      {c.segment?.rel_paths && c.segment.rel_paths.length > 0 && (
                        <div className="text-[10px] font-mono text-slate-400">
                          Contradicted File: {c.segment.rel_paths.join(', ')}
                        </div>
                      )}
                      <UnitEvidenceBlock citations={c.citations} aspects={c.aspects} />
                    </div>
                  ))}
                </div>
              </div>
            )}


            {/* 4. Executive Analysis Panel */}
            <div className="rounded-xl bg-slate-900/90 border border-indigo-900/50 p-5 space-y-4 shadow-lg">
              <div className="flex items-center justify-between pb-3 border-b border-slate-800">
                <div className="flex items-center gap-2">
                  <Sparkles className="w-5 h-5 text-indigo-400" />
                  <h2 className="text-sm font-bold text-slate-100">Executive Analysis & Risk Assessment</h2>
                </div>
                <button
                  onClick={loadSummary}
                  disabled={summaryLoading}
                  className="flex items-center gap-1.5 px-3 py-1 bg-indigo-900/60 hover:bg-indigo-800/80 text-indigo-300 rounded text-xs border border-indigo-700/50 transition-colors"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${summaryLoading ? 'animate-spin' : ''}`} />
                  <span>{summaryLoading ? 'Analyzing...' : 'Generate Analysis'}</span>
                </button>
              </div>

              {summaryData ? (
                <div className="space-y-4 text-xs">
                  <div className="flex items-center gap-3">
                    <span
                      className={`px-3 py-1 rounded font-bold font-mono text-xs ${
                        summaryData.overall_verdict === 'PASS'
                          ? 'bg-emerald-950 text-emerald-400 border border-emerald-500/50'
                          : 'bg-red-950 text-red-400 border border-red-500/50'
                      }`}
                    >
                      VERDICT: {summaryData.overall_verdict}
                    </span>
                    <p className="text-sm font-semibold text-slate-200">{summaryData.headline}</p>
                  </div>

                  <div className="space-y-2 bg-slate-950 p-3.5 rounded-lg border border-slate-800">
                    <div className="font-bold text-slate-300">Key Flow Risks & Contradictions:</div>
                    <ul className="list-disc list-inside space-y-1 text-slate-300">
                      {summaryData.key_risks?.map((risk, idx) => (
                        <li key={idx} className="leading-relaxed">
                          {risk}
                        </li>
                      ))}
                    </ul>
                  </div>

                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="bg-slate-950 p-3 rounded-lg border border-slate-800 space-y-1">
                      <div className="font-bold text-slate-400">Coverage & Altitude Caveat:</div>
                      <p className="text-slate-300 leading-relaxed">{summaryData.coverage_note}</p>
                    </div>
                    <div className="bg-slate-950 p-3 rounded-lg border border-slate-800 space-y-1">
                      <div className="font-bold text-indigo-400">Architectural Recommendation:</div>
                      <p className="text-slate-300 leading-relaxed">{summaryData.recommendation}</p>
                    </div>
                  </div>
                </div>
              ) : (
                <div className="text-xs text-slate-400 py-4 text-center">
                  Click &quot;Generate Analysis&quot; or select an LLM provider to synthesize executive audit findings.
                </div>
              )}
            </div>

            {/* 5. Collapsed Technical Details Section (Legacy per-edge tables) */}
            <details className="rounded-xl bg-slate-900/60 border border-slate-800 overflow-hidden group">
              <summary className="p-4 bg-slate-900/80 hover:bg-slate-900 text-xs font-bold text-slate-300 flex items-center justify-between cursor-pointer select-none">
                <div className="flex items-center gap-2">
                  <FileCode2 className="w-4 h-4 text-slate-400" />
                  <span>Technical Details (per-edge, legacy view)</span>
                </div>
                <span className="text-slate-500 font-mono text-[11px] group-open:rotate-180 transition-transform">
                  ▼
                </span>
              </summary>

              <div className="p-5 space-y-6 border-t border-slate-800">
                {/* Legacy Calibration Metrics */}
                {cal && (
                  <div className="p-3 rounded bg-slate-950 border border-slate-800 text-xs font-mono space-y-1">
                    <div className="font-semibold text-slate-300">Legacy Calibration Breakdown:</div>
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-slate-400 text-[11px]">
                      <div>Match %: {cal.match_percentage}%</div>
                      <div>Total Units: {cal.total_units}</div>
                      <div>Matched: {cal.match_count}</div>
                      <div>Broken: {cal.broken_count}</div>
                      <div>Code Only: {cal.code_only_count}</div>
                      <div>Unknown: {cal.unknown_count}</div>
                    </div>
                  </div>
                )}

                {/* Legacy Matched Nodes Table */}
                {(() => {
                  const matchedNodes = (mapData?.nodes || []).filter(
                    (n: FlowIntegrityMapNode) => n.data?.tag === 'DOC_MATCHED'
                  )
                  return (
                    <div className="space-y-2">
                      <h4 className="text-xs font-bold text-slate-300">Matched Flow Edges ({matchedNodes.length})</h4>
                      <div className="overflow-x-auto">
                        <table className="w-full text-left text-xs border-collapse">
                          <thead>
                            <tr className="border-b border-slate-800 text-slate-400 font-medium">
                              <th className="py-2 px-3">BD Step</th>
                              <th className="py-2 px-3">Line</th>
                              <th className="py-2 px-3">Binding</th>
                              <th className="py-2 px-3">Resolved File</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-slate-800/60">
                            {matchedNodes.map((n: FlowIntegrityMapNode) => (
                              <tr key={n.id} className="hover:bg-slate-800/40">
                                <td className="py-1.5 px-3">
                                  <CleanLabel text={n.data?.label || n.id} titleClassName="font-medium text-slate-200" />
                                </td>
                                <td className="py-1.5 px-3 font-mono text-slate-400">Line {n.data?.doc_line_start || 1}</td>
                                <td className="py-1.5 px-3 font-mono text-indigo-300">{n.data?.binding || '-'}</td>
                                <td className="py-1.5 px-3 font-mono text-blue-300">{n.data?.rel_path || '-'}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )
                })()}

                {/* Legacy Unknown References Table */}
                <div className="space-y-2">
                  <button
                    onClick={() => setReportUnknownExpanded((prev) => !prev)}
                    className="w-full flex items-center justify-between text-left focus:outline-none"
                  >
                    <h4 className="text-xs font-bold text-slate-300">
                      BD References Without Source File / Below Route Altitude ({findingsData?.collapsed_unknown_count ?? 0})
                    </h4>
                    <span className="text-slate-400 text-xs font-mono">
                      {reportUnknownExpanded ? 'Hide ▾' : 'Show ▸'}
                    </span>
                  </button>

                  {reportUnknownExpanded && (
                    <div className="overflow-x-auto pt-2 border-t border-slate-800">
                      <table className="w-full text-left text-xs border-collapse">
                        <thead>
                          <tr className="border-b border-slate-800 text-slate-400 font-medium">
                            <th className="py-2 px-3">BD Span</th>
                            <th className="py-2 px-3">Evaluation Note</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-800/60">
                          {(findingsData?.unknown_findings || []).map((f: FlowIntegrityFindingRow) => (
                            <tr key={f.id} className="hover:bg-slate-800/40">
                              <td className="py-1.5 px-3 font-medium text-slate-200">{f.bd_span}</td>
                              <td className="py-1.5 px-3 text-slate-400">{f.reason}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              </div>
            </details>
          </>
        ) : (
          /* ========================================================================= */
          /* LEGACY VIEW FALLBACK (WHEN NO BUSINESS FLOW PIPELINE DATA IS PRESENT)     */
          /* ========================================================================= */
          <>
            {/* Calibration Summary */}
            {cal && (
              <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                <div className="p-4 rounded-xl bg-slate-900/90 border border-slate-800 flex flex-col justify-between shadow-md">
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-slate-400 font-medium">Match Percentage</span>
                    <span
                      className={`px-2 py-0.5 rounded text-[10px] font-bold font-mono ${
                        cal.calibration_pass
                          ? 'bg-emerald-950 text-emerald-400 border border-emerald-500/40'
                          : 'bg-red-950 text-red-400 border border-red-500/40'
                      }`}
                    >
                      {cal.calibration_pass ? 'PASS (80–99%)' : 'FAIL'}
                    </span>
                  </div>
                  <div className="mt-2 flex items-baseline gap-2">
                    <span
                      className={`text-3xl font-extrabold font-mono ${
                        cal.calibration_pass ? 'text-emerald-400' : 'text-red-400'
                      }`}
                    >
                      {cal.match_percentage ?? 0}%
                    </span>
                  </div>
                  <div className="text-[11px] text-slate-500 mt-1">Healthy 80–99% calibration band</div>
                </div>

                <div className="p-4 rounded-xl bg-slate-900/90 border border-slate-800 space-y-1 shadow-md">
                  <div className="text-xs text-slate-400 font-medium">Resolved vs Total Units</div>
                  <div className="text-2xl font-bold font-mono text-slate-100">
                    {cal.resolved_units ?? 0} <span className="text-sm font-normal text-slate-500">/ {cal.total_units ?? 0}</span>
                  </div>
                  <div className="text-[11px] text-slate-500">Evaluated at route altitude</div>
                </div>

                <div className="p-4 rounded-xl bg-slate-900/90 border border-slate-800 space-y-1 shadow-md">
                  <div className="text-xs text-slate-400 font-medium">Matched / Broken / Code Only</div>
                  <div className="text-2xl font-bold font-mono flex items-center gap-2">
                    <span className="text-emerald-400">{cal.match_count ?? 0}</span>
                    <span className="text-slate-600">/</span>
                    <span className="text-red-400">{cal.broken_count ?? 0}</span>
                    <span className="text-slate-600">/</span>
                    <span className="text-blue-400">{cal.code_only_count ?? 0}</span>
                  </div>
                  <div className="text-[11px] text-slate-500">Matched / Broken / Omissions</div>
                </div>

                <div className="p-4 rounded-xl bg-slate-900/90 border border-slate-800 space-y-1 shadow-md">
                  <div className="text-xs text-slate-400 font-medium">Stale Missing & Unknown</div>
                  <div className="text-2xl font-bold font-mono flex items-center gap-2">
                    <span className="text-purple-400">{cal.ai_bucket_counts?.stale_missing ?? 0}</span>
                    <span className="text-slate-600">/</span>
                    <span className="text-slate-400">{cal.unknown_count ?? 0}</span>
                  </div>
                  <div className="text-[11px] text-slate-500">Contradictions / Micro-steps</div>
                </div>
              </div>
            )}

            {/* Executive Summary Panel */}
            <div className="rounded-xl bg-slate-900/90 border border-indigo-900/50 p-5 space-y-4 shadow-lg">
              <div className="flex items-center justify-between pb-3 border-b border-slate-800">
                <div className="flex items-center gap-2">
                  <Sparkles className="w-5 h-5 text-indigo-400" />
                  <h2 className="text-sm font-bold text-slate-100">Executive Analysis & Risk Assessment</h2>
                </div>
                <button
                  onClick={loadSummary}
                  disabled={summaryLoading}
                  className="flex items-center gap-1.5 px-3 py-1 bg-indigo-900/60 hover:bg-indigo-800/80 text-indigo-300 rounded text-xs border border-indigo-700/50 transition-colors"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${summaryLoading ? 'animate-spin' : ''}`} />
                  <span>{summaryLoading ? 'Analyzing...' : 'Generate Analysis'}</span>
                </button>
              </div>

              {summaryData ? (
                <div className="space-y-4 text-xs">
                  <div className="flex items-center gap-3">
                    <span
                      className={`px-3 py-1 rounded font-bold font-mono text-xs ${
                        summaryData.overall_verdict === 'PASS'
                          ? 'bg-emerald-950 text-emerald-400 border border-emerald-500/50'
                          : 'bg-red-950 text-red-400 border border-red-500/50'
                      }`}
                    >
                      VERDICT: {summaryData.overall_verdict}
                    </span>
                    <p className="text-sm font-semibold text-slate-200">{summaryData.headline}</p>
                  </div>

                  <div className="space-y-2 bg-slate-950 p-3.5 rounded-lg border border-slate-800">
                    <div className="font-bold text-slate-300">Key Flow Risks & Contradictions:</div>
                    <ul className="list-disc list-inside space-y-1 text-slate-300">
                      {summaryData.key_risks?.map((risk, idx) => (
                        <li key={idx} className="leading-relaxed">
                          {risk}
                        </li>
                      ))}
                    </ul>
                  </div>

                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="bg-slate-950 p-3 rounded-lg border border-slate-800 space-y-1">
                      <div className="font-bold text-slate-400">Coverage & Altitude Caveat:</div>
                      <p className="text-slate-300 leading-relaxed">{summaryData.coverage_note}</p>
                    </div>
                    <div className="bg-slate-950 p-3 rounded-lg border border-slate-800 space-y-1">
                      <div className="font-bold text-indigo-400">Architectural Recommendation:</div>
                      <p className="text-slate-300 leading-relaxed">{summaryData.recommendation}</p>
                    </div>
                  </div>
                </div>
              ) : (
                <div className="text-xs text-slate-400 py-4 text-center">
                  Click &quot;Generate Analysis&quot; or select an LLM provider to synthesize executive audit findings.
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
