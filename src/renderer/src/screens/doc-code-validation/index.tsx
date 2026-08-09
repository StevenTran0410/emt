import React, { useEffect, useState } from 'react'
import {
  CheckCircle2,
  AlertTriangle,
  Play,
  Layers,
  Link2,
  HelpCircle,
  ChevronDown,
  ChevronRight
} from 'lucide-react'
import type {
  DocGraphClusterSummary,
  DocCodeCompareResult,
  DocCodeRelationCompareResult,
  TypeComparisonResult,
  PredicateRelationResult,
  RelationComparisonDetail,
  LocalRepo
} from '../../types/electron'

export function DocCodeValidationScreen(): React.ReactElement {
  const [clusters, setClusters] = useState<DocGraphClusterSummary[]>([])
  const [selectedClusterId, setSelectedClusterId] = useState<string>('')
  const [snapshots, setSnapshots] = useState<string[]>([])
  const [selectedSnapshotId, setSelectedSnapshotId] = useState<string>('')
  const [customSnapshotId, setCustomSnapshotId] = useState<string>('')

  const [loading, setLoading] = useState<boolean>(false)
  const [error, setError] = useState<string | null>(null)

  const [compareResult, setCompareResult] = useState<DocCodeCompareResult | null>(null)
  const [relationResult, setRelationResult] = useState<DocCodeRelationCompareResult | null>(null)

  const [expandedTypes, setExpandedTypes] = useState<Record<string, boolean>>({})
  const [expandedRelations, setExpandedRelations] = useState<Record<string, boolean>>({})

  useEffect(() => {
    // Load available clusters
    window.api.docGraph
      .listClusters()
      .then((cls) => {
        setClusters(cls)
        if (cls.length > 0) {
          setSelectedClusterId(cls[0].cluster_id)
        }
      })
      .catch((err) => {
        console.error('Failed to list clusters:', err)
      })

    // Load available repos/snapshots
    window.api.folder
      .list()
      .then((repos: LocalRepo[]) => {
        const snapIds = repos
          .map((r) => r.active_snapshot_id)
          .filter((id): id is string => Boolean(id))
        setSnapshots(snapIds)
        if (snapIds.length > 0) {
          setSelectedSnapshotId(snapIds[0])
        }
      })
      .catch((err) => {
        console.error('Failed to list repos:', err)
      })
  }, [])

  // SHOULD-FIX 5: Reset results when selections change
  const handleClusterChange = (clusterId: string) => {
    setSelectedClusterId(clusterId)
    setCompareResult(null)
    setRelationResult(null)
    setError(null)
  }

  const handleSnapshotChange = (snapshotId: string) => {
    setSelectedSnapshotId(snapshotId)
    setCustomSnapshotId('')
    setCompareResult(null)
    setRelationResult(null)
    setError(null)
  }

  const handleCustomSnapshotChange = (val: string) => {
    setCustomSnapshotId(val)
    setCompareResult(null)
    setRelationResult(null)
    setError(null)
  }

  const activeSnapshotId = customSnapshotId.trim() || selectedSnapshotId

  const handleRunValidation = async () => {
    if (!selectedClusterId) {
      setError('Please select a document graph cluster.')
      return
    }
    if (!activeSnapshotId) {
      setError('Please select or enter a code snapshot ID.')
      return
    }

    setLoading(true)
    setError(null)
    setCompareResult(null)
    setRelationResult(null)

    try {
      const [cRes, rRes] = await Promise.all([
        window.api.docCode.compare({
          cluster_id: selectedClusterId,
          snapshot_id: activeSnapshotId
        }),
        window.api.docCode.compareRelations({
          cluster_id: selectedClusterId,
          snapshot_id: activeSnapshotId
        })
      ])
      setCompareResult(cRes)
      setRelationResult(rRes)
    } catch (err) {
      console.error('Validation failed:', err)
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  const toggleTypeExpand = (type: string) => {
    setExpandedTypes((prev) => ({ ...prev, [type]: !prev[type] }))
  }

  const toggleRelationExpand = (key: string) => {
    setExpandedRelations((prev) => ({ ...prev, [key]: !prev[key] }))
  }

  const formatProvenance = (prov: unknown): string => {
    if (!prov) return ''
    if (typeof prov === 'string') return prov
    if (typeof prov === 'object') {
      const p = prov as Record<string, unknown>
      const doc = p.doc_kind || p.doc_id || p.doc_path || ''
      const sec = p.section ? ` Sec: ${p.section}` : ''
      const span = p.doc_span ? ` (${p.doc_span})` : ''
      return `${doc}${sec}${span}`.trim()
    }
    return String(prov)
  }

  const allNotAssessed = Array.from(
    new Set([
      ...(compareResult?.not_assessed || []),
      ...(relationResult?.not_assessed || [])
    ])
  )

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto text-slate-100">
      {/* Header & Controls */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 bg-slate-900 p-5 rounded-xl border border-slate-800 shadow-sm">
        <div>
          <div className="flex items-center gap-2">
            <CheckCircle2 className="w-6 h-6 text-emerald-400" />
            <h1 className="text-xl font-bold text-slate-100 tracking-tight">Doc↔Code Validation</h1>
          </div>
          <p className="text-xs text-slate-400 mt-1">
            Compare Document Graph clusters against Code Graph snapshots for entity completeness and structural link correctness.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          {/* Cluster Select */}
          <div className="flex flex-col gap-1">
            <label className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider">
              Document Cluster
            </label>
            <select
              value={selectedClusterId}
              onChange={(e) => handleClusterChange(e.target.value)}
              className="bg-slate-800 border border-slate-700 text-slate-200 text-xs rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            >
              {clusters.length === 0 && <option value="">No clusters found</option>}
              {clusters.map((c) => (
                <option key={c.cluster_id} value={c.cluster_id}>
                  {c.cluster_name} ({c.cluster_id.substring(0, 8)}...)
                </option>
              ))}
            </select>
          </div>

          {/* Snapshot Select / Input */}
          <div className="flex flex-col gap-1">
            <label className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider">
              Code Snapshot
            </label>
            {snapshots.length > 0 ? (
              <select
                value={selectedSnapshotId}
                onChange={(e) => handleSnapshotChange(e.target.value)}
                className="bg-slate-800 border border-slate-700 text-slate-200 text-xs rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              >
                {snapshots.map((s) => (
                  <option key={s} value={s}>
                    {s.substring(0, 16)}...
                  </option>
                ))}
              </select>
            ) : (
              <input
                type="text"
                placeholder="Enter snapshot ID..."
                value={customSnapshotId}
                onChange={(e) => handleCustomSnapshotChange(e.target.value)}
                className="bg-slate-800 border border-slate-700 text-slate-200 text-xs rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-indigo-500 w-48"
              />
            )}
          </div>

          {/* Run Button */}
          <button
            onClick={handleRunValidation}
            disabled={loading || !selectedClusterId}
            className="mt-4 md:mt-0 px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white text-xs font-semibold flex items-center gap-2 transition-colors shadow-sm"
          >
            {loading ? (
              <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
            ) : (
              <Play className="w-3.5 h-3.5 fill-current" />
            )}
            Run validation
          </button>
        </div>
      </div>

      {error && (
        <div className="p-4 rounded-xl bg-red-950/40 border border-red-800/60 text-red-200 text-xs flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-red-400 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* Relation Status Warning Banner */}
      {relationResult && relationResult.status !== 'OK' && (
        <div className="p-4 rounded-xl bg-amber-950/40 border border-amber-800/60 text-amber-200 text-xs flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0" />
          <span>
            {relationResult.status === 'STALE_INPUT'
              ? 'Warning: Snapshot ID does not match the bound snapshot ID of this cluster.'
              : 'Cluster is not bound to a specific snapshot ID.'}
          </span>
        </div>
      )}

      {/* Results View */}
      {compareResult && (
        <div className="space-y-6">
          {/* PANEL A — Entity Completeness */}
          <div className="bg-slate-900 rounded-xl border border-slate-800 p-5 space-y-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Layers className="w-5 h-5 text-indigo-400" />
                <h2 className="text-sm font-bold text-slate-200 uppercase tracking-wide">
                  Panel A — Entity Completeness
                </h2>
              </div>
              <div
                className={`text-[11px] font-semibold px-2.5 py-1 rounded-full border ${
                  compareResult.eligibility.authoritative
                    ? 'bg-emerald-950/60 border-emerald-800/80 text-emerald-400'
                    : 'bg-amber-950/60 border-amber-800/80 text-amber-400'
                }`}
                title={compareResult.eligibility.reason}
              >
                {compareResult.eligibility.authoritative
                  ? 'Authoritative'
                  : 'Non-authoritative'}
              </div>
            </div>

            {/* Stat Cards */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <div className="bg-slate-800/50 p-3 rounded-lg border border-slate-700/60 text-center">
                <div className="text-xl font-bold text-emerald-400">
                  {compareResult.summary.matched}
                </div>
                <div className="text-[10px] font-medium text-slate-400 uppercase tracking-wider mt-0.5">
                  Matched
                </div>
              </div>
              <div className="bg-slate-800/50 p-3 rounded-lg border border-slate-700/60 text-center">
                <div className="text-xl font-bold text-amber-400">
                  {compareResult.summary.undocumented}
                </div>
                <div className="text-[10px] font-medium text-slate-400 uppercase tracking-wider mt-0.5">
                  Undocumented
                </div>
              </div>
              <div className="bg-slate-800/50 p-3 rounded-lg border border-slate-700/60 text-center">
                <div className="text-xl font-bold text-red-400">
                  {compareResult.summary.missing}
                </div>
                <div className="text-[10px] font-medium text-slate-400 uppercase tracking-wider mt-0.5">
                  Missing
                </div>
              </div>
              <div className="bg-slate-800/50 p-3 rounded-lg border border-slate-700/60 text-center">
                <div className="text-xl font-bold text-slate-400">
                  {compareResult.summary.unknown}
                </div>
                <div className="text-[10px] font-medium text-slate-400 uppercase tracking-wider mt-0.5">
                  Unknown
                </div>
              </div>
            </div>

            {/* Per-Type Table */}
            <div className="overflow-x-auto rounded-lg border border-slate-800">
              <table className="w-full text-left text-xs text-slate-300">
                <thead className="bg-slate-800/80 text-[10px] text-slate-400 uppercase tracking-wider border-b border-slate-700/80">
                  <tr>
                    <th className="p-3">Type</th>
                    <th className="p-3">Doc Count</th>
                    <th className="p-3">Code Count</th>
                    <th className="p-3">Matched</th>
                    <th className="p-3">Undocumented</th>
                    <th className="p-3">Missing</th>
                    <th className="p-3">Unknown</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/60">
                  {compareResult.per_type.map((tRes: TypeComparisonResult) => {
                    const isExpanded = Boolean(expandedTypes[tRes.type])
                    const hasFindings =
                      tRes.undocumented.length > 0 ||
                      tRes.missing.length > 0 ||
                      tRes.unknown.length > 0

                    return (
                      <React.Fragment key={tRes.type}>
                        <tr
                          className={`hover:bg-slate-800/40 transition-colors ${
                            hasFindings ? 'cursor-pointer' : ''
                          }`}
                          onClick={() => hasFindings && toggleTypeExpand(tRes.type)}
                        >
                          <td className="p-3 font-semibold text-slate-200 capitalize flex items-center gap-2">
                            {hasFindings && (
                              <span className="text-slate-500">
                                {isExpanded ? (
                                  <ChevronDown className="w-3.5 h-3.5" />
                                ) : (
                                  <ChevronRight className="w-3.5 h-3.5" />
                                )}
                              </span>
                            )}
                            {tRes.type}
                          </td>
                          <td className="p-3">{tRes.doc_count}</td>
                          <td className="p-3">{tRes.code_count}</td>
                          <td className="p-3 text-emerald-400 font-semibold">{tRes.matched}</td>
                          <td className="p-3 text-amber-400 font-semibold">{tRes.undocumented.length}</td>
                          <td className="p-3 text-red-400 font-semibold">{tRes.missing.length}</td>
                          <td className="p-3 text-slate-400 font-semibold">{tRes.unknown.length}</td>
                        </tr>

                        {isExpanded && (
                          <tr className="bg-slate-950/60">
                            <td colSpan={7} className="p-4 space-y-3">
                              {/* Undocumented List */}
                              {tRes.undocumented.length > 0 && (
                                <div className="space-y-1.5">
                                  <div className="text-[11px] font-bold text-amber-400 uppercase tracking-wider">
                                    Undocumented in Code ({tRes.undocumented.length})
                                  </div>
                                  <div className="space-y-1">
                                    {tRes.undocumented.map((item, idx) => (
                                      <div
                                        key={idx}
                                        className="text-[11px] bg-slate-900/80 p-2 rounded border border-slate-800 flex items-center justify-between"
                                      >
                                        <span className="font-mono text-slate-200">{item.key}</span>
                                        <span className="font-mono text-amber-300 text-[10px]">
                                          {item.rel_path}:{item.line_start}-{item.line_end}
                                        </span>
                                      </div>
                                    ))}
                                  </div>
                                </div>
                              )}

                              {/* Missing List (SHOULD-FIX 7: Render provenance) */}
                              {tRes.missing.length > 0 && (
                                <div className="space-y-1.5">
                                  <div className="text-[11px] font-bold text-red-400 uppercase tracking-wider">
                                    Missing from Code ({tRes.missing.length})
                                  </div>
                                  <div className="space-y-1">
                                    {tRes.missing.map((item, idx) => {
                                      const provStr = formatProvenance(item.provenance)
                                      return (
                                        <div
                                          key={idx}
                                          className="text-[11px] bg-slate-900/80 p-2 rounded border border-slate-800 flex items-center justify-between gap-4"
                                        >
                                          <span className="font-mono text-slate-200">{item.key}</span>
                                          <div className="text-right">
                                            <span className="text-slate-400 text-[10px] block">
                                              {item.display_name}
                                            </span>
                                            {provStr && (
                                              <span className="text-slate-500 font-mono text-[9px] block">
                                                {provStr}
                                              </span>
                                            )}
                                          </div>
                                        </div>
                                      )
                                    })}
                                  </div>
                                </div>
                              )}

                              {/* Unknown List (SHOULD-FIX 7: Render provenance) */}
                              {tRes.unknown.length > 0 && (
                                <div className="space-y-1.5">
                                  <div className="text-[11px] font-bold text-slate-400 uppercase tracking-wider">
                                    Unknown / Non-Authoritative ({tRes.unknown.length})
                                  </div>
                                  <div className="space-y-1">
                                    {tRes.unknown.map((item, idx) => {
                                      const provStr = formatProvenance(item.provenance)
                                      return (
                                        <div
                                          key={idx}
                                          className="text-[11px] bg-slate-900/80 p-2 rounded border border-slate-800 flex items-center justify-between gap-4"
                                        >
                                          <span className="font-mono text-slate-400">{item.key}</span>
                                          <div className="text-right">
                                            <span className="text-slate-500 text-[10px] block">
                                              {item.display_name}
                                            </span>
                                            {provStr && (
                                              <span className="text-slate-600 font-mono text-[9px] block">
                                                {provStr}
                                              </span>
                                            )}
                                          </div>
                                        </div>
                                      )
                                    })}
                                  </div>
                                </div>
                              )}
                            </td>
                          </tr>
                        )}
                      </React.Fragment>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>

          {/* PANEL B — Relation Link */}
          {relationResult && (
            <div className="bg-slate-900 rounded-xl border border-slate-800 p-5 space-y-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Link2 className="w-5 h-5 text-indigo-400" />
                  <h2 className="text-sm font-bold text-slate-200 uppercase tracking-wide">
                    Panel B — Relation Link
                  </h2>
                </div>
                <div className="text-xs text-slate-400">
                  Status: <span className="font-semibold text-slate-200">{relationResult.status}</span>
                </div>
              </div>

              {/* Stat Cards */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <div className="bg-slate-800/50 p-3 rounded-lg border border-slate-700/60 text-center">
                  <div className="text-xl font-bold text-emerald-400">
                    {relationResult.summary.matched}
                  </div>
                  <div className="text-[10px] font-medium text-slate-400 uppercase tracking-wider mt-0.5">
                    Matched
                  </div>
                </div>
                <div className="bg-slate-800/50 p-3 rounded-lg border border-slate-700/60 text-center">
                  <div className="text-xl font-bold text-amber-400">
                    {relationResult.summary.doc_only}
                  </div>
                  <div className="text-[10px] font-medium text-slate-400 uppercase tracking-wider mt-0.5">
                    Doc Only
                  </div>
                </div>
                <div className="bg-slate-800/50 p-3 rounded-lg border border-slate-700/60 text-center">
                  <div className="text-xl font-bold text-amber-400">
                    {relationResult.summary.code_only}
                  </div>
                  <div className="text-[10px] font-medium text-slate-400 uppercase tracking-wider mt-0.5">
                    Code Only
                  </div>
                </div>
                <div className="bg-slate-800/50 p-3 rounded-lg border border-slate-700/60 text-center">
                  <div className="text-xl font-bold text-slate-400">
                    {relationResult.summary.unknown}
                  </div>
                  <div className="text-[10px] font-medium text-slate-400 uppercase tracking-wider mt-0.5">
                    Unknown
                  </div>
                </div>
              </div>

              {/* Grouped by Predicate */}
              <div className="space-y-4">
                {relationResult.per_predicate.map((pGroup: PredicateRelationResult) => (
                  <div
                    key={pGroup.predicate}
                    className="border border-slate-800 rounded-lg bg-slate-900/40 p-4 space-y-3"
                  >
                    <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                      <div className="text-xs font-bold text-indigo-400 uppercase tracking-wider">
                        Predicate: {pGroup.predicate}
                      </div>
                      <div className="text-[10px] text-slate-400 flex items-center gap-3 font-mono">
                        <span className="text-emerald-400">matched: {pGroup.matched}</span>
                        <span className="text-amber-400">doc_only: {pGroup.doc_only}</span>
                        <span className="text-amber-400">code_only: {pGroup.code_only}</span>
                        <span className="text-slate-400">unknown: {pGroup.unknown}</span>
                      </div>
                    </div>

                    <div className="overflow-x-auto">
                      <table className="w-full text-left text-xs text-slate-300">
                        <thead className="text-[10px] text-slate-400 uppercase tracking-wider border-b border-slate-800">
                          <tr>
                            <th className="pb-2">Side</th>
                            <th className="pb-2">Subject → Object</th>
                            <th className="pb-2">Endpoint</th>
                            <th className="pb-2">Multiplicity</th>
                            <th className="pb-2">Doc / Code Count</th>
                            <th className="pb-2">Eligibility</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-800/40">
                          {pGroup.details.map((detail: RelationComparisonDetail, idx) => {
                            const relKey = `${pGroup.predicate}-${idx}`
                            const isRelExpanded = Boolean(expandedRelations[relKey])

                            return (
                              <React.Fragment key={relKey}>
                                <tr
                                  className="hover:bg-slate-800/40 transition-colors cursor-pointer"
                                  onClick={() => toggleRelationExpand(relKey)}
                                >
                                  {/* SHOULD-FIX 6: Render CODE when side is null for CODE_ONLY */}
                                  <td className="py-2.5">
                                    <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-slate-800 border border-slate-700 text-slate-300">
                                      {detail.side || 'CODE'}
                                    </span>
                                  </td>
                                  <td className="py-2.5 font-mono text-slate-200">
                                    {detail.subject_key}{' '}
                                    <span className="text-slate-500">→</span>{' '}
                                    {detail.object_key}
                                  </td>
                                  <td className="py-2.5">
                                    <span
                                      className={`text-[10px] font-bold px-2 py-0.5 rounded-full border ${
                                        detail.endpoint_verdict === 'MATCH'
                                          ? 'bg-emerald-950/60 border-emerald-800 text-emerald-400'
                                          : detail.endpoint_verdict === 'UNKNOWN'
                                          ? 'bg-slate-800 border-slate-700 text-slate-400'
                                          : 'bg-amber-950/60 border-amber-800 text-amber-400'
                                      }`}
                                    >
                                      {detail.endpoint_verdict}
                                    </span>
                                  </td>
                                  <td className="py-2.5">
                                    <span
                                      className={`text-[10px] font-bold px-2 py-0.5 rounded-full border ${
                                        detail.multiplicity_verdict === 'EXACT_SITE_MATCH'
                                          ? 'bg-emerald-600 text-white font-extrabold shadow-sm'
                                          : detail.multiplicity_verdict === 'COUNT_ONLY_MATCH'
                                          ? 'bg-blue-950/60 border-blue-800 text-blue-400'
                                          : detail.multiplicity_verdict === 'COUNT_MISMATCH'
                                          ? 'bg-amber-950/60 border-amber-800 text-amber-400'
                                          : 'bg-slate-800 border-slate-700 text-slate-400'
                                      }`}
                                    >
                                      {detail.multiplicity_verdict}
                                    </span>
                                  </td>
                                  <td className="py-2.5 font-mono">
                                    {detail.doc_count} / {detail.code_count}
                                  </td>
                                  <td className="py-2.5 text-[10px] text-slate-400">
                                    {detail.eligibility}
                                  </td>
                                </tr>

                                {isRelExpanded && detail.evidence.length > 0 && (
                                  <tr className="bg-slate-950/60">
                                    <td colSpan={6} className="p-3 space-y-1">
                                      <div className="text-[10px] font-bold text-indigo-400 uppercase tracking-wider">
                                        Evidence Details
                                      </div>
                                      <div className="space-y-1">
                                        {detail.evidence.map((ev, evIdx) => {
                                          const lineStr =
                                            ev.line_start || ev.line_end
                                              ? `:${ev.line_start || 0}-${ev.line_end || 0}`
                                              : ''
                                          return (
                                            <div
                                              key={evIdx}
                                              className="text-[11px] font-mono bg-slate-900 p-1.5 rounded border border-slate-800 flex items-center justify-between text-slate-300"
                                            >
                                              <span>
                                                [{ev.match_kind}] {ev.occurrence_key || ev.source_store}
                                              </span>
                                              {ev.rel_path && (
                                                <span className="text-indigo-300">
                                                  {ev.rel_path}{lineStr}
                                                </span>
                                              )}
                                            </div>
                                          )
                                        })}
                                      </div>
                                    </td>
                                  </tr>
                                )}
                              </React.Fragment>
                            )
                          })}
                        </tbody>
                      </table>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* PANEL C — Not Assessed */}
          {allNotAssessed.length > 0 && (
            <div className="bg-slate-900 rounded-xl border border-slate-800 p-5 space-y-3">
              <div className="flex items-center gap-2">
                <HelpCircle className="w-5 h-5 text-slate-400" />
                <h2 className="text-sm font-bold text-slate-200 uppercase tracking-wide">
                  Panel C — Not Assessed (Known Exclusions)
                </h2>
              </div>
              <p className="text-xs text-slate-400">
                These entity types and relation boundaries are intentionally excluded from completeness scores:
              </p>
              <div className="flex flex-wrap gap-2">
                {allNotAssessed.map((item) => (
                  <span
                    key={item}
                    className="px-2.5 py-1 rounded-md bg-slate-800 border border-slate-700 text-slate-300 text-xs font-mono"
                  >
                    {item}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
