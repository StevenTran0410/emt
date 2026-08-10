import React, { useEffect, useState, useCallback } from 'react'
import {
  CheckCircle2,
  Play,
  BookOpen,
  RefreshCw,
  Layers,
  Link2,
  Sparkles,
  AlertTriangle
} from 'lucide-react'
import { Button, SectionLoading, ErrorBanner } from '../../components/ui'

import { useValidationMetrics } from './useValidationMetrics'
import { useAiAssessment } from './useAiAssessment'
import { Scorecard } from './Scorecard'
import { EntityCompletenessPanel } from './EntityCompletenessPanel'
import { RelationLinkPanel } from './RelationLinkPanel'
import { NotAssessedPanel } from './NotAssessedPanel'
import { AiAssessmentPanel } from './AiAssessmentPanel'
import { MethodologyDrawer } from './MethodologyDrawer'

import type {
  DocGraphClusterSummary,
  DocCodeCompareResult,
  DocCodeRelationCompareResult,
  LocalRepo
} from '../../types/electron'

const CACHE_PREFIX = 'docCodeValidation:'
const cacheKey = (clusterId: string, snapshotId: string): string =>
  `${CACHE_PREFIX}${clusterId}::${snapshotId}`

interface CachedValidation {
  compareResult: DocCodeCompareResult | null
  relationResult: DocCodeRelationCompareResult | null
  savedAt: number
}

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
  const [restoredAt, setRestoredAt] = useState<number | null>(null)

  // Methodology drawer state
  const [isMethodologyOpen, setIsMethodologyOpen] = useState<boolean>(false)
  const [methodologyDimensionId, setMethodologyDimensionId] = useState<string | undefined>(undefined)

  const activeSnapshotId = customSnapshotId.trim() || selectedSnapshotId

  // AI Assessment hook
  const {
    assessment: aiAssessment,
    loading: aiLoading,
    error: aiError,
    runAssessment
  } = useAiAssessment(selectedClusterId, activeSnapshotId)

  const metrics = useValidationMetrics(compareResult, relationResult)

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

  const handleClusterChange = (clusterId: string) => {
    setSelectedClusterId(clusterId)
    setError(null)
  }

  const handleSnapshotChange = (snapshotId: string) => {
    setSelectedSnapshotId(snapshotId)
    setCustomSnapshotId('')
    setError(null)
  }

  // Restore saved validation result for current selection
  useEffect(() => {
    if (!selectedClusterId || !activeSnapshotId) {
      setCompareResult(null)
      setRelationResult(null)
      setRestoredAt(null)
      return
    }
    try {
      const raw = localStorage.getItem(cacheKey(selectedClusterId, activeSnapshotId))
      if (raw) {
        const parsed = JSON.parse(raw) as CachedValidation
        setCompareResult(parsed.compareResult ?? null)
        setRelationResult(parsed.relationResult ?? null)
        setRestoredAt(parsed.savedAt ?? null)
        return
      }
    } catch {
      // quiet fallback
    }
    setCompareResult(null)
    setRelationResult(null)
    setRestoredAt(null)
  }, [selectedClusterId, activeSnapshotId])

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
      setRestoredAt(null)
      try {
        const payload: CachedValidation = {
          compareResult: cRes,
          relationResult: rRes,
          savedAt: Date.now()
        }
        localStorage.setItem(cacheKey(selectedClusterId, activeSnapshotId), JSON.stringify(payload))
      } catch {
        // quota ignore
      }
    } catch (err) {
      console.error('Validation failed:', err)
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  const handleOpenMethodology = (dimensionId?: string) => {
    setMethodologyDimensionId(dimensionId)
    setIsMethodologyOpen(true)
  }

  const handleScrollToAi = () => {
    const aiPanel = document.getElementById('ai-assessment-panel')
    if (aiPanel) {
      aiPanel.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto text-zinc-100 bg-zinc-950 min-h-screen">
      {/* Header & Controls */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 bg-zinc-900 p-5 rounded-xl border border-zinc-800 shadow-sm">
        <div>
          <div className="flex items-center gap-2.5">
            <div className="p-2 rounded-xl bg-indigo-500/10 border border-indigo-500/30 text-indigo-400">
              <CheckCircle2 className="w-5 h-5" />
            </div>
            <h1 className="text-xl font-bold text-zinc-100 tracking-tight">Doc↔Code Validation</h1>
          </div>
          <p className="text-xs text-zinc-400 mt-1">
            Compare Document Graph clusters against Code Graph snapshots for entity completeness and structural link correctness.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          {/* Cluster Select */}
          <div className="flex flex-col gap-1">
            <label className="text-[10px] font-semibold text-zinc-400 uppercase tracking-wider">
              Document Cluster
            </label>
            <select
              value={selectedClusterId}
              onChange={(e) => handleClusterChange(e.target.value)}
              className="bg-zinc-950 border border-zinc-800 text-zinc-200 text-xs rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-indigo-500"
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
            <label className="text-[10px] font-semibold text-zinc-400 uppercase tracking-wider">
              Code Snapshot
            </label>
            {snapshots.length > 0 ? (
              <select
                value={selectedSnapshotId}
                onChange={(e) => handleSnapshotChange(e.target.value)}
                className="bg-zinc-950 border border-zinc-800 text-zinc-200 text-xs rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-indigo-500"
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
                placeholder="Snapshot ID"
                value={customSnapshotId}
                onChange={(e) => setCustomSnapshotId(e.target.value)}
                className="bg-zinc-950 border border-zinc-800 text-zinc-200 text-xs rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-indigo-500 font-mono w-44"
              />
            )}
          </div>

          {/* Methodology Drawer Button */}
          <Button
            variant="ghost"
            onClick={() => handleOpenMethodology()}
            leftIcon={<BookOpen className="w-4 h-4 text-indigo-400" />}
            className="self-end border border-zinc-800 hover:border-zinc-700 text-zinc-300"
          >
            Methodology
          </Button>

          {/* Run Validation Button */}
          <Button
            variant="primary"
            onClick={handleRunValidation}
            disabled={loading || !selectedClusterId || !activeSnapshotId}
            leftIcon={loading ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4 fill-current" />}
            className="self-end shadow-md font-semibold bg-indigo-600 hover:bg-indigo-500 border-indigo-500 text-white"
          >
            {loading ? 'Validating...' : 'Run validation'}
          </Button>
        </div>
      </div>

      {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}

      {/* Restored Cache Banner */}
      {restoredAt && compareResult && (
        <div className="px-4 py-2.5 bg-zinc-900 border border-zinc-800 rounded-xl text-xs text-zinc-400 flex items-center justify-between font-mono">
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-emerald-400" />
            <span>Restored validation result from session cache ({new Date(restoredAt).toLocaleTimeString()}).</span>
          </div>
          <button onClick={handleRunValidation} className="text-indigo-400 hover:underline">
            Re-run live
          </button>
        </div>
      )}

      {/* Relation STALE warning */}
      {relationResult?.status === 'STALE_INPUT' && (
        <div className="px-4 py-2.5 bg-amber-950/40 border border-amber-800/60 rounded-xl text-xs text-amber-300 flex items-center gap-2 font-mono">
          <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0" />
          <span>Warning: Underlying relation comparisons are STALE. Click "Run validation" to re-calculate.</span>
        </div>
      )}

      {/* Main Validation Dashboard Area */}
      {loading ? (
        <SectionLoading label="Running Doc↔Code completeness and structural link validation algorithms..." />
      ) : !compareResult || !relationResult ? (
        <div className="py-16 bg-zinc-900/60 border border-zinc-800 rounded-xl text-center space-y-3 flex flex-col items-center">
          <div className="p-3 rounded-full bg-zinc-800 text-zinc-400">
            <CheckCircle2 className="w-8 h-8 text-zinc-500" />
          </div>
          <div className="font-bold text-zinc-200 text-base">No Validation Results Yet</div>
          <div className="text-xs text-zinc-400 max-w-md">
            Select a Document Cluster and Code Snapshot above, then click <strong className="text-zinc-200">'Run validation'</strong> to execute the completeness & link correctness audit.
          </div>
        </div>
      ) : (
        <div className="space-y-6">
          {/* Top KPI Scorecard */}
          <Scorecard
            metrics={metrics}
            authoritative={compareResult.eligibility.authoritative}
            authoritativeReason={compareResult.eligibility.reason}
            aiAssessment={aiAssessment}
            onScrollToAi={handleScrollToAi}
          />

          {/* Panel A — Entity Completeness */}
          <EntityCompletenessPanel
            entityResult={compareResult}
            onOpenMethodology={handleOpenMethodology}
          />

          {/* Panel B — Structural Link Correctness */}
          <RelationLinkPanel
            relationResult={relationResult}
            onOpenMethodology={handleOpenMethodology}
          />

          {/* Panel C — Explicitly Excluded Scopes */}
          <NotAssessedPanel
            entityNotAssessed={compareResult.not_assessed}
            relationNotAssessed={relationResult.not_assessed}
          />

          {/* AI Assessment Panel */}
          <AiAssessmentPanel
            assessment={aiAssessment}
            loading={aiLoading}
            error={aiError}
            canRun={Boolean(compareResult && relationResult)}
            onRunAssessment={() => runAssessment()}
          />
        </div>
      )}

      {/* Methodology Slide-Over Drawer */}
      <MethodologyDrawer
        isOpen={isMethodologyOpen}
        onClose={() => setIsMethodologyOpen(false)}
        initialDimensionId={methodologyDimensionId}
      />
    </div>
  )
}
