import React, { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ShieldCheck,
  Zap,
  RefreshCw,
  X,
  FileCode,
  FileText,
  AlertTriangle,
  CheckCircle,
  HelpCircle,
  Share2,
  Sparkles,
  ChevronDown,
  ChevronRight
} from 'lucide-react'
import type {
  DocGraphClusterSummary,
  FlowIntegrityMapResponse,
  FlowIntegrityFindingsResponse,
  FlowIntegrityMapNode,
  FlowIntegrityFindingRow,
  FlowIntegrityExecutiveSummary
} from '../../types/electron'

interface LocalRepo {
  id: string
  name: string
  active_snapshot_id?: string | null
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

  // Load map & findings data (with transient 404 guard)
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

  useEffect(() => {
    if (selectedClusterId && snapshotId) {
      loadSummary()
    }
  }, [selectedClusterId, snapshotId, loadSummary])

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
              Business Flow Integrity Report (Dimension 03)
            </h1>
            <p className="text-xs text-slate-400">
              Executive Findings • Calibration Gate • Faithful Abstraction Proof
            </p>
          </div>

          <button
            onClick={() => navigate('/flow-integrity/graph')}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-indigo-300 rounded border border-slate-700 font-medium text-xs shadow-sm transition-colors ml-4"
          >
            <Share2 className="w-3.5 h-3.5" />
            <span>Open Graph</span>
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

      {/* Main Report Body */}
      <div className="flex-1 overflow-y-auto p-6 space-y-6 bg-slate-950 text-slate-100">
        {/* 1. Summary KPI Tiles */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <div className="p-4 rounded-xl bg-slate-900/90 border border-slate-800 flex flex-col justify-between shadow-md">
            <div className="flex items-center justify-between">
              <span className="text-xs text-slate-400 font-medium">Match Percentage</span>
              <span
                className={`px-2 py-0.5 rounded text-[10px] font-bold font-mono ${
                  cal?.calibration_pass
                    ? 'bg-emerald-950 text-emerald-400 border border-emerald-500/40'
                    : 'bg-red-950 text-red-400 border border-red-500/40'
                }`}
              >
                {cal?.calibration_pass ? 'PASS (80–99%)' : 'FAIL'}
              </span>
            </div>
            <div className="mt-2 flex items-baseline gap-2">
              <span
                className={`text-3xl font-extrabold font-mono ${
                  cal?.calibration_pass ? 'text-emerald-400' : 'text-red-400'
                }`}
              >
                {cal?.match_percentage ?? 0}%
              </span>
            </div>
            <div className="text-[11px] text-slate-500 mt-1">Healthy 80–99% calibration band</div>
          </div>

          <div className="p-4 rounded-xl bg-slate-900/90 border border-slate-800 space-y-1 shadow-md">
            <div className="text-xs text-slate-400 font-medium">Resolved vs Total Units</div>
            <div className="text-2xl font-bold font-mono text-slate-100">
              {cal?.resolved_units ?? 0} <span className="text-sm font-normal text-slate-500">/ {cal?.total_units ?? 0}</span>
            </div>
            <div className="text-[11px] text-slate-500">Evaluated at route altitude</div>
          </div>

          <div className="p-4 rounded-xl bg-slate-900/90 border border-slate-800 space-y-1 shadow-md">
            <div className="text-xs text-slate-400 font-medium">Matched / Broken / Code Only</div>
            <div className="text-2xl font-bold font-mono flex items-center gap-2">
              <span className="text-emerald-400">{cal?.match_count ?? 0}</span>
              <span className="text-slate-600">/</span>
              <span className="text-red-400">{cal?.broken_count ?? 0}</span>
              <span className="text-slate-600">/</span>
              <span className="text-blue-400">{cal?.code_only_count ?? 0}</span>
            </div>
            <div className="text-[11px] text-slate-500">Matched / Broken / Omissions</div>
          </div>

          <div className="p-4 rounded-xl bg-slate-900/90 border border-slate-800 space-y-1 shadow-md">
            <div className="text-xs text-slate-400 font-medium">Stale Missing & Unknown</div>
            <div className="text-2xl font-bold font-mono flex items-center gap-2">
              <span className="text-purple-400">{cal?.ai_bucket_counts?.stale_missing ?? 0}</span>
              <span className="text-slate-600">/</span>
              <span className="text-slate-400">{cal?.unknown_count ?? 0}</span>
            </div>
            <div className="text-[11px] text-slate-500">Contradictions / Micro-steps</div>
          </div>
        </div>

        {/* 2. LLM Executive Analysis Panel (FIX 3) */}
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

        {/* 3. Matched Steps Table */}
        {(() => {
          const matchedNodes = (mapData?.nodes || []).filter(
            (n: FlowIntegrityMapNode) => n.data?.tag === 'DOC_MATCHED'
          )
          return (
            <div className="rounded-xl bg-slate-900/80 border border-slate-800 p-5 space-y-3 shadow-md">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-bold text-slate-100 flex items-center gap-2">
                  <CheckCircle className="w-4 h-4 text-emerald-400" />
                  <span>Matched Flow Steps ({matchedNodes.length})</span>
                </h2>
                <span className="text-xs text-slate-400">Faithful code route abstractions</span>
              </div>

              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs border-collapse">
                  <thead>
                    <tr className="border-b border-slate-800 text-slate-400 font-medium">
                      <th className="py-2.5 px-3">BD Step</th>
                      <th className="py-2.5 px-3">BD Line</th>
                      <th className="py-2.5 px-3">Binding</th>
                      <th className="py-2.5 px-3">Resolved Source File</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/60">
                    {matchedNodes.length > 0 ? (
                      matchedNodes.map((n: FlowIntegrityMapNode) => (
                        <tr key={n.id} className="hover:bg-slate-800/40">
                          <td className="py-2 px-3">
                            <CleanLabel text={n.data?.label || n.id} titleClassName="font-medium text-slate-200" />
                          </td>
                          <td className="py-2 px-3 font-mono text-slate-400">Line {n.data?.doc_line_start || 1}</td>
                          <td className="py-2 px-3 font-mono text-indigo-300">{n.data?.binding || '-'}</td>
                          <td className="py-2 px-3 font-mono text-blue-300">{n.data?.rel_path || '-'}</td>
                        </tr>
                      ))
                    ) : (
                      <tr>
                        <td colSpan={4} className="py-4 text-center text-slate-500">
                          No matched steps found
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          )
        })()}

        {/* 4. Contradictions Table (stale_missing) */}
        {(() => {
          const staleMissing = (findingsData?.broken_unknown_findings || []).filter(
            (f: FlowIntegrityFindingRow) => f.ai_bucket === 'stale_missing'
          )
          return (
            <div className="rounded-xl bg-slate-900/80 border border-slate-800 p-5 space-y-3 shadow-md">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-bold text-slate-100 flex items-center gap-2">
                  <AlertTriangle className="w-4 h-4 text-red-400" />
                  <span>Contradictions & Stale Missing ({staleMissing.length})</span>
                </h2>
                <span className="text-xs text-slate-400">BD documents missing, but source code exists</span>
              </div>

              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs border-collapse">
                  <thead>
                    <tr className="border-b border-slate-800 text-slate-400 font-medium">
                      <th className="py-2.5 px-3">BD Span</th>
                      <th className="py-2.5 px-3">AI Bucket</th>
                      <th className="py-2.5 px-3">Contradiction Reason</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/60">
                    {staleMissing.length > 0 ? (
                      staleMissing.map((f: FlowIntegrityFindingRow) => (
                        <tr key={f.id} className="hover:bg-slate-800/40">
                          <td className="py-2 px-3 font-medium text-slate-200">{f.bd_span}</td>
                          <td className="py-2 px-3 font-mono text-purple-400">
                            <span className="bg-purple-950/80 px-2 py-0.5 rounded text-[10px] border border-purple-500/30">
                              {f.ai_bucket}
                            </span>
                          </td>
                          <td className="py-2 px-3 text-slate-300">{f.reason}</td>
                        </tr>
                      ))
                    ) : (
                      <tr>
                        <td colSpan={3} className="py-4 text-center text-slate-500">
                          No contradiction findings
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          )
        })()}

        {/* 5. Omissions Table (Code Only) */}
        {(() => {
          const codeOnly = findingsData?.code_only_findings || []
          return (
            <div className="rounded-xl bg-slate-900/80 border border-slate-800 p-5 space-y-3 shadow-md">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-bold text-slate-100 flex items-center gap-2">
                  <FileCode className="w-4 h-4 text-blue-400" />
                  <span>Omissions & Code-Only Assets ({codeOnly.length})</span>
                </h2>
                <span className="text-xs text-slate-400">Reachable code omitted from documentation</span>
              </div>

              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs border-collapse">
                  <thead>
                    <tr className="border-b border-slate-800 text-slate-400 font-medium">
                      <th className="py-2.5 px-3">Source File</th>
                      <th className="py-2.5 px-3">Kind</th>
                      <th className="py-2.5 px-3">Omission Reason</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/60">
                    {codeOnly.length > 0 ? (
                      codeOnly.map((f: FlowIntegrityFindingRow) => (
                        <tr key={f.id} className="hover:bg-slate-800/40">
                          <td className="py-2 px-3 font-mono text-blue-300">{f.rel_path}</td>
                          <td className="py-2 px-3 font-mono text-slate-400 uppercase">{f.node_kind || 'asset'}</td>
                          <td className="py-2 px-3 text-slate-300">{f.reason}</td>
                        </tr>
                      ))
                    ) : (
                      <tr>
                        <td colSpan={3} className="py-4 text-center text-slate-500">
                          No code-only omissions
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          )
        })()}

        {/* 6. BD References Without Source File Table */}
        <div className="rounded-xl bg-slate-900/80 border border-slate-800 p-5 space-y-3 shadow-md">
          <button
            onClick={() => setReportUnknownExpanded((prev) => !prev)}
            className="w-full flex items-center justify-between text-left focus:outline-none"
          >
            <h2 className="text-sm font-bold text-slate-100 flex items-center gap-2">
              <HelpCircle className="w-4 h-4 text-slate-400" />
              <span>
                BD References Without Source File / Below Route Altitude ({findingsData?.collapsed_unknown_count ?? 0})
              </span>
            </h2>
            <span className="text-slate-400 text-xs flex items-center gap-1 font-mono">
              {reportUnknownExpanded ? 'Hide table ▾' : 'Show table ▸'}
            </span>
          </button>

          {reportUnknownExpanded && (
            <div className="overflow-x-auto pt-2 border-t border-slate-800">
              <table className="w-full text-left text-xs border-collapse">
                <thead>
                  <tr className="border-b border-slate-800 text-slate-400 font-medium">
                    <th className="py-2.5 px-3">BD Span</th>
                    <th className="py-2.5 px-3">Evaluation Note</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/60">
                  {findingsData?.unknown_findings && findingsData.unknown_findings.length > 0 ? (
                    findingsData.unknown_findings.map((f: FlowIntegrityFindingRow) => (
                      <tr key={f.id} className="hover:bg-slate-800/40">
                        <td className="py-2 px-3 font-medium text-slate-200">{f.bd_span}</td>
                        <td className="py-2 px-3 text-slate-400">{f.reason}</td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan={2} className="py-4 text-center text-slate-500">
                        No unknown references listed
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
