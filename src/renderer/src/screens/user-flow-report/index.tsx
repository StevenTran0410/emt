import React, { useState, useEffect, useCallback, useMemo } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  useNodesState,
  useEdgesState,
  type Node
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import {
  Workflow,
  AlertTriangle,
  FileCode2,
  Play,
  RefreshCw,
  ChevronDown,
  ChevronRight,
  Eye,
  EyeOff,
  Layers,
  Sparkles,
  ArrowRight,
  ArrowLeft,
  X,
  UploadCloud,
  FileSpreadsheet,
  Search,
  LayoutGrid
} from 'lucide-react'
import type {
  DocGraphClusterSummary,
  UserFlowReport,
  UserFlowFlowReport,
  UserFlowActivityReport,
  UserFlowStepReport,
  UserFlowDocInfo,
  UserFlowStepVerdict,
  UserFlowKeptBdMapping,
  UserFlowCitation,
  UserFlowBdExtraItem,
  BusinessFlow,
  BusinessFlowStep,
  BusinessFlowBranch
} from '../../types/electron'
import { projectBusinessFlowSkeleton } from '../graph/layout'
import { nodeTypes, edgeTypes } from '../bd-flow'

// ---------------------------------------------------------------------------
// 1. Single Source of Truth for Verdict Translations & Metadata
// ---------------------------------------------------------------------------

/** Map User Flow 5-way step verdict to BD Flow 4-way visual color */
export const USER_VERDICT_TO_BD: Record<UserFlowStepVerdict | string, 'MATCH' | 'PARTIAL' | 'BROKEN' | 'UNKNOWN'> = {
  MATCHED: 'MATCH',
  COVERED: 'MATCH',
  PARTIAL: 'PARTIAL',
  DIVERGENT: 'BROKEN',
  CONTRADICTED: 'BROKEN',
  BD_MISSING: 'PARTIAL',
  UNCOVERED: 'UNKNOWN',
  UNVERIFIABLE: 'UNKNOWN',
  OUT_OF_SCOPE: 'UNKNOWN'
}

export const VERDICT_META: Record<
  string,
  {
    label: string
    color: string
    bg: string
    border: string
    nodeBg: string
    nodeBorder: string
    icon: string
    description: string
  }
> = {
  // Flow & Activity Rollups
  MATCHED: {
    label: 'Matched',
    color: 'text-emerald-400',
    bg: 'bg-emerald-950/80',
    border: 'border-emerald-500/40',
    nodeBg: 'bg-emerald-950/60',
    nodeBorder: 'border-emerald-500/80',
    icon: '🟢',
    description: 'All in-scope steps are covered in BD and source code.'
  },
  PARTIAL: {
    label: 'Partial',
    color: 'text-amber-400',
    bg: 'bg-amber-950/80',
    border: 'border-amber-500/40',
    nodeBg: 'bg-amber-950/60',
    nodeBorder: 'border-amber-500/80',
    icon: '🟡',
    description: 'Some steps covered, with missing BD units or unverifiable steps.'
  },
  DIVERGENT: {
    label: 'Divergent',
    color: 'text-red-400',
    bg: 'bg-red-950/80',
    border: 'border-red-500/40',
    nodeBg: 'bg-red-950/60',
    nodeBorder: 'border-red-500/80',
    icon: '🔴',
    description: 'Contains contradictions between customer flow and BD/code.'
  },
  UNCOVERED: {
    label: 'Uncovered',
    color: 'text-orange-400',
    bg: 'bg-orange-950/80',
    border: 'border-orange-500/40',
    nodeBg: 'bg-orange-950/60',
    nodeBorder: 'border-orange-500/80',
    icon: '🟠',
    description: 'No in-scope steps matched in BD specifications or code.'
  },
  OUT_OF_SCOPE: {
    label: 'Out of Scope',
    color: 'text-slate-400',
    bg: 'bg-slate-900/80',
    border: 'border-slate-700/40',
    nodeBg: 'bg-slate-900/60',
    nodeBorder: 'border-slate-700/80',
    icon: '⚪',
    description: 'Explicitly marked out of scope.'
  },
  // Step Verdicts
  COVERED: {
    label: 'Covered',
    color: 'text-emerald-400',
    bg: 'bg-emerald-950/80',
    border: 'border-emerald-500/40',
    nodeBg: 'bg-emerald-950/40',
    nodeBorder: 'border-emerald-500/60',
    icon: '✓',
    description: 'Behavior confirmed in code and matched to BD specification.'
  },
  BD_MISSING: {
    label: 'BD Missing',
    color: 'text-orange-400',
    bg: 'bg-orange-950/80',
    border: 'border-orange-500/40',
    nodeBg: 'bg-orange-950/40',
    nodeBorder: 'border-orange-500/60',
    icon: '!',
    description: 'Behavior exists in source code but missing from BD specification.'
  },
  CONTRADICTED: {
    label: 'Contradicted',
    color: 'text-red-400',
    bg: 'bg-red-950/80',
    border: 'border-red-500/40',
    nodeBg: 'bg-red-950/40',
    nodeBorder: 'border-red-500/60',
    icon: '✕',
    description: 'Behavior directly conflicts with BD or code implementation.'
  },
  UNVERIFIABLE: {
    label: 'Unverifiable',
    color: 'text-slate-300',
    bg: 'bg-slate-800/80',
    border: 'border-slate-600/40',
    nodeBg: 'bg-slate-800/40',
    nodeBorder: 'border-slate-600/60',
    icon: '?',
    description: 'Visual/presentation aspect or insufficient code citations.'
  },
  BD_EXTRA: {
    label: 'BD Extra',
    color: 'text-purple-400',
    bg: 'bg-purple-950/80',
    border: 'border-purple-500/40',
    nodeBg: 'bg-purple-950/40',
    nodeBorder: 'border-purple-500/60',
    icon: '+',
    description: 'BD unit describes behavior not mentioned in customer user flow.'
  },
  PENDING: {
    label: 'Parsed (Unmatched)',
    color: 'text-indigo-300',
    bg: 'bg-indigo-950/60',
    border: 'border-indigo-500/40',
    nodeBg: 'bg-indigo-950/40',
    nodeBorder: 'border-indigo-500/60',
    icon: '⏳',
    description: 'Structure parsed; run match to calculate alignment verdicts.'
  }
}

export const MATCH_STATUS_META: Record<
  string,
  { label: string; color: string; bg: string; border: string }
> = {
  FULLY: {
    label: 'BD MATCH: FULLY',
    color: 'text-emerald-300',
    bg: 'bg-emerald-950/90',
    border: 'border-emerald-500/50'
  },
  PARTIAL: {
    label: 'BD MATCH: PARTIAL',
    color: 'text-amber-300',
    bg: 'bg-amber-950/90',
    border: 'border-amber-500/50'
  },
  NONE: {
    label: 'NO BD FLOW',
    color: 'text-slate-400',
    bg: 'bg-slate-900',
    border: 'border-slate-700'
  }
}

// ---------------------------------------------------------------------------
// 2. Top-Level Helper Components
// ---------------------------------------------------------------------------

function OriginalJapaneseToggle({
  textJa,
  triggerJa,
  expectedJa,
  screenNameJa,
  scopeNote
}: {
  textJa?: string | null
  triggerJa?: string | null
  expectedJa?: string | null
  screenNameJa?: string | null
  scopeNote?: string | null
}): React.ReactElement | null {
  const [open, setOpen] = useState(false)
  if (!textJa && !triggerJa && !expectedJa && !screenNameJa && !scopeNote) return null

  return (
    <div className="mt-2">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-slate-200 transition-colors font-medium"
      >
        {open ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
        <span>{open ? 'Hide original (原文を隠す)' : '原文 / show original'}</span>
      </button>

      {open && (
        <div className="mt-2 p-3 rounded-lg bg-slate-950/80 border border-slate-800 space-y-2 text-xs text-slate-300">
          {textJa && (
            <div>
              <span className="text-[10px] uppercase font-bold text-slate-500 block">Text (原文):</span>
              <span className="text-slate-200">{textJa}</span>
            </div>
          )}
          {triggerJa && (
            <div>
              <span className="text-[10px] uppercase font-bold text-slate-500 block">Trigger (操作):</span>
              <span className="text-slate-200">{triggerJa}</span>
            </div>
          )}
          {expectedJa && (
            <div>
              <span className="text-[10px] uppercase font-bold text-slate-500 block">Expected (期待結果):</span>
              <span className="text-slate-200">{expectedJa}</span>
            </div>
          )}
          {screenNameJa && (
            <div>
              <span className="text-[10px] uppercase font-bold text-slate-500 block">Screen (画面名):</span>
              <span className="text-slate-200">{screenNameJa}</span>
            </div>
          )}
          {scopeNote && (
            <div>
              <span className="text-[10px] uppercase font-bold text-slate-500 block">Scope Note:</span>
              <span className="text-slate-400">{scopeNote}</span>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function CodeCitationSnippet({ citation }: { citation: UserFlowCitation }): React.ReactElement {
  const [open, setOpen] = useState(true)

  return (
    <div className="rounded-lg border border-slate-800 bg-slate-950/90 overflow-hidden text-xs">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-3 py-2 bg-slate-900/80 hover:bg-slate-900 text-left transition-colors font-mono text-[11px]"
      >
        <div className="flex items-center gap-1.5 text-indigo-300 truncate">
          <FileCode2 className="w-3.5 h-3.5 shrink-0 text-indigo-400" />
          <span className="font-semibold">{citation.rel_path}</span>
          <span className="text-slate-400">:{citation.line_start}-{citation.line_end}</span>
        </div>
        {open ? <ChevronDown className="w-3.5 h-3.5 text-slate-400" /> : <ChevronRight className="w-3.5 h-3.5 text-slate-400" />}
      </button>

      {open && (
        <div className="p-3 bg-slate-950 font-mono text-[11px] text-slate-300 overflow-x-auto whitespace-pre leading-relaxed border-t border-slate-900">
          {citation.fetched_text ? (
            <code>{citation.fetched_text}</code>
          ) : (
            <span className="text-slate-500 italic">No citation preview text available.</span>
          )}
        </div>
      )}
    </div>
  )
}

function StepEvidenceDrawer({
  step,
  onClose
}: {
  step: UserFlowStepReport | null
  onClose: () => void
}): React.ReactElement | null {
  if (!step) return null
  const meta = VERDICT_META[step.verdict] || VERDICT_META.UNVERIFIABLE

  return (
    <div className="w-96 shrink-0 bg-slate-900 border-l border-slate-800 flex flex-col h-full overflow-hidden shadow-2xl z-20">
      {/* Header */}
      <div className="p-4 border-b border-slate-800 flex items-start justify-between gap-2 bg-slate-900/90">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="text-xs font-mono font-bold text-slate-400">Step #{step.ordinal}</span>
            <span className={`text-[11px] font-bold px-2 py-0.5 rounded-full border ${meta.bg} ${meta.color} ${meta.border}`}>
              {meta.icon} {meta.label}
            </span>
          </div>
          <div className="text-sm font-semibold text-slate-100 leading-snug">
            {step.text_en || step.text_ja}
          </div>
          {!step.text_en && (
            <span className="inline-block mt-0.5 text-[10px] text-amber-400 bg-amber-950/60 px-1 rounded">
              untranslated
            </span>
          )}
        </div>
        <button
          onClick={onClose}
          className="p-1 rounded-md text-slate-400 hover:text-slate-200 hover:bg-slate-800 transition-colors"
        >
          <X className="w-5 h-5" />
        </button>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* Verification Status Banner */}
        {step.corrected && (
          <div className="p-2.5 rounded-lg bg-amber-950/40 border border-amber-500/40 flex items-start gap-2">
            <Sparkles className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
            <div className="text-xs text-amber-200">
              <span className="font-bold">Corrected by Verifier:</span> Upstream mapper hypothesis was updated after inspecting code evidence.
            </div>
          </div>
        )}

        {/* Reason / Divergence */}
        <div className="space-y-1">
          <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wide">Verdict Rationale</span>
          <div className="p-3 rounded-lg bg-slate-950/70 border border-slate-800 text-xs text-slate-300 leading-relaxed">
            {step.reason || meta.description}
            {step.divergence && (
              <div className="mt-1 text-[11px] text-amber-400 font-mono">
                Divergence Axis: {step.divergence}
              </div>
            )}
            {step.basis && (
              <div className="mt-1 text-[11px] text-indigo-400 font-mono">
                Evidence Basis: {step.basis}
              </div>
            )}
          </div>
        </div>

        {/* Sheet & Section info */}
        <div className="grid grid-cols-2 gap-2 text-xs">
          <div className="p-2 rounded bg-slate-950/50 border border-slate-800/80">
            <span className="text-[10px] text-slate-500 block uppercase">Sheet & Rows</span>
            <span className="font-mono text-slate-300 truncate block">
              {step.sheet} : r{step.row_start}-{step.row_end}
            </span>
          </div>
          <div className="p-2 rounded bg-slate-950/50 border border-slate-800/80">
            <span className="text-[10px] text-slate-500 block uppercase">Section ID</span>
            <span className="font-mono text-slate-300 truncate block">
              {step.section_id || '—'}
            </span>
          </div>
        </div>

        {/* Japanese Original Toggle */}
        <OriginalJapaneseToggle
          textJa={step.text_ja}
          triggerJa={step.trigger_ja}
          expectedJa={step.expected_ja}
          screenNameJa={step.screen_name_ja}
          scopeNote={step.scope_note}
        />

        {/* Kept BD Mappings */}
        <div className="space-y-2 pt-2 border-t border-slate-800">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wide flex items-center gap-1.5">
              <Layers className="w-3.5 h-3.5 text-indigo-400" />
              <span>Corresponding BD Units ({step.kept_bd_mappings.length})</span>
            </span>
          </div>

          {step.kept_bd_mappings.length === 0 ? (
            <div className="p-3 rounded-lg bg-slate-950/50 border border-slate-800/60 text-xs text-slate-500 italic">
              {step.verdict === 'BD_MISSING'
                ? 'No matching BD specification unit found (BD_MISSING).'
                : 'No active BD mappings linked to this step.'}
            </div>
          ) : (
            <div className="space-y-2">
              {step.kept_bd_mappings.map((m: UserFlowKeptBdMapping) => {
                const bdChip = m.bd_verdict ? VERDICT_META[m.bd_verdict] || VERDICT_META.UNVERIFIABLE : null
                return (
                  <div key={m.bd_id} className="p-3 rounded-lg bg-slate-950 border border-slate-800 space-y-1.5">
                    <div className="flex items-start justify-between gap-1">
                      <span className="font-semibold text-xs text-slate-200">{m.name}</span>
                      {bdChip && (
                        <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded border ${bdChip.bg} ${bdChip.color} ${bdChip.border}`}>
                          {bdChip.icon} BD↔Code {m.bd_verdict}
                        </span>
                      )}
                    </div>
                    {m.functionality && (
                      <div className="text-xs text-slate-400 line-clamp-3 leading-relaxed">
                        {m.functionality}
                      </div>
                    )}
                    <div className="flex items-center justify-between text-[10px] text-slate-500 font-mono pt-1">
                      <span>Relation: {m.relation}</span>
                      <span>Confidence: {Math.round(m.confidence * 100)}%</span>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>

        {/* Code Citations with Verbatim Text */}
        <div className="space-y-2 pt-2 border-t border-slate-800">
          <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wide flex items-center gap-1.5">
            <FileCode2 className="w-3.5 h-3.5 text-indigo-400" />
            <span>Code Citations ({step.citations.length})</span>
          </span>

          {step.citations.length === 0 ? (
            <div className="p-3 rounded-lg bg-slate-950/50 border border-slate-800/60 text-xs text-slate-500 italic">
              No code citations recorded for this step.
            </div>
          ) : (
            <div className="space-y-2">
              {step.citations.map((c: UserFlowCitation, idx: number) => (
                <CodeCitationSnippet key={`${c.rel_path}:${c.line_start}-${idx}`} citation={c} />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function BdExtraSection({
  bdExtraList,
  isOpen,
  onToggle
}: {
  bdExtraList: UserFlowBdExtraItem[]
  isOpen: boolean
  onToggle: () => void
}): React.ReactElement {
  return (
    <div className="border-t border-slate-800 bg-slate-950">
      <button
        onClick={onToggle}
        className="w-full px-4 py-2.5 flex items-center justify-between text-left hover:bg-slate-900 transition-colors"
      >
        <div className="flex items-center gap-2 text-xs font-bold text-purple-300">
          <span className="w-2 h-2 rounded-full bg-purple-500"></span>
          <span>BD Extra Units ({bdExtraList.length})</span>
          <span className="text-slate-400 font-normal">
            — BD specifications from code that have no corresponding customer user flow
          </span>
        </div>
        {isOpen ? <ChevronDown className="w-4 h-4 text-slate-400" /> : <ChevronRight className="w-4 h-4 text-slate-400" />}
      </button>

      {isOpen && (
        <div className="p-4 pt-0 max-h-64 overflow-y-auto grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {bdExtraList.length === 0 ? (
            <div className="col-span-full py-4 text-center text-xs text-slate-500 italic">
              No extra BD units found. All BD specifications correspond to customer user flows.
            </div>
          ) : (
            bdExtraList.map((item: UserFlowBdExtraItem) => (
              <div
                key={item.bd_id}
                className="p-3 rounded-lg bg-slate-900 border border-purple-900/40 space-y-1.5"
              >
                <div className="flex items-start justify-between gap-1">
                  <span className="font-semibold text-xs text-slate-200 truncate">{item.name}</span>
                  <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-purple-950 text-purple-400 border border-purple-500/30">
                    + BD_EXTRA
                  </span>
                </div>
                {item.functionality && (
                  <div className="text-xs text-slate-400 line-clamp-2">{item.functionality}</div>
                )}
                <div className="text-[10px] text-slate-500 font-mono flex items-center justify-between">
                  <span>Kind: {item.bd_kind}</span>
                  {item.bd_verdict && <span>BD↔Code: {item.bd_verdict}</span>}
                </div>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  )
}

/** Level 1: Flow Overview Card */
function FlowOverviewCard({
  businessFlow,
  flowReport,
  onSelect
}: {
  businessFlow: BusinessFlow
  flowReport?: UserFlowFlowReport
  onSelect: () => void
}): React.ReactElement {
  const matchVerdict = flowReport?.flow_match || 'PENDING'
  const meta = VERDICT_META[matchVerdict] || VERDICT_META.OUT_OF_SCOPE
  const nameEn = flowReport?.name_en || businessFlow.name
  const nameJa = flowReport?.name_ja
  const stepCount = flowReport?.steps?.length ?? businessFlow.steps?.length ?? 0
  const actCount = flowReport?.activities?.length ?? 0
  const coveredCount = flowReport?.counts?.COVERED ?? 0
  const inScopeCount = flowReport ? flowReport.steps.filter((s) => s.in_scope).length : stepCount

  return (
    <div
      onClick={onSelect}
      className={`group relative rounded-xl border ${meta.border} ${meta.nodeBg} p-4 hover:border-indigo-400 hover:shadow-xl hover:shadow-indigo-950/30 transition-all cursor-pointer flex flex-col justify-between`}
    >
      <div>
        <div className="flex items-start justify-between gap-2 mb-2">
          <div className="flex items-center gap-1.5 font-mono text-xs text-slate-400">
            <span className="px-1.5 py-0.5 rounded bg-slate-900 border border-slate-800 font-bold text-slate-300">
              #{businessFlow.ordinal || flowReport?.ordinal || 1}
            </span>
            <span className="text-[11px] text-slate-400 truncate max-w-[140px]" title={businessFlow.block_key || flowReport?.sheet}>
              {businessFlow.block_key || flowReport?.sheet || 'Flow'}
            </span>
          </div>
          <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full border ${meta.bg} ${meta.color} ${meta.border} shrink-0`}>
            {meta.icon} {meta.label}
          </span>
        </div>

        <h4 className="font-semibold text-sm text-slate-100 group-hover:text-indigo-300 transition-colors leading-snug line-clamp-2">
          {nameEn || nameJa || `Flow #${businessFlow.ordinal}`}
        </h4>

        {nameJa && nameEn && nameJa !== nameEn && (
          <p className="text-[11px] text-slate-400 line-clamp-1 mt-1 font-sans">
            {nameJa}
          </p>
        )}
      </div>

      <div className="mt-4 pt-3 border-t border-slate-800/80 flex items-center justify-between text-xs">
        <div className="flex items-center gap-2 text-slate-300 font-medium">
          {actCount > 0 && <span className="text-indigo-300">{actCount} activities ·</span>}
          <span className="text-slate-400">{stepCount} steps</span>
          {flowReport && (
            <span className="text-[11px] text-emerald-400 font-semibold">
              ({coveredCount}/{inScopeCount} covered)
            </span>
          )}
        </div>

        <span className="text-xs font-semibold text-indigo-400 group-hover:translate-x-0.5 transition-transform flex items-center gap-1">
          <span>Drill in</span>
          <ArrowRight className="w-3.5 h-3.5" />
        </span>
      </div>
    </div>
  )
}

/** Breadcrumb navigation bar for Phase U2 Activity Drilldown */
function BreadcrumbTrail({
  flowName,
  flowOrdinal,
  activityName,
  currentStepCount,
  onNavigateFlows,
  onNavigateFlow
}: {
  flowName: string
  flowOrdinal: number
  activityName?: string | null
  currentStepCount: number
  onNavigateFlows: () => void
  onNavigateFlow: () => void
}): React.ReactElement {
  return (
    <div className="flex items-center gap-1.5 text-xs text-slate-300 font-medium overflow-x-auto py-0.5">
      <button
        onClick={onNavigateFlows}
        className="text-indigo-400 hover:text-indigo-300 hover:underline transition-colors shrink-0"
      >
        All Flows
      </button>

      <ChevronRight className="w-3.5 h-3.5 text-slate-600 shrink-0" />

      <button
        onClick={onNavigateFlow}
        className={`hover:text-indigo-300 hover:underline transition-colors truncate max-w-[200px] ${
          !activityName ? 'text-slate-100 font-semibold' : 'text-slate-400'
        }`}
        title={`Flow #${flowOrdinal}: ${flowName}`}
      >
        Flow #{flowOrdinal}: {flowName}
      </button>

      {activityName && (
        <>
          <ChevronRight className="w-3.5 h-3.5 text-slate-600 shrink-0" />
          <span className="text-slate-100 font-semibold truncate max-w-[220px]" title={activityName}>
            Activity: {activityName}
          </span>
        </>
      )}

      <span className="ml-2 px-2 py-0.5 rounded-full bg-slate-800 text-[11px] text-slate-400 font-mono shrink-0">
        {currentStepCount} {activityName ? 'steps' : 'activities'} in view
      </span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 3. Main UserFlowReportScreen Component (Phase U2 Activity-Based Two-Tier UI)
// ---------------------------------------------------------------------------

export function UserFlowReportScreen(): React.ReactElement {
  // Selectors State
  const [clusters, setClusters] = useState<DocGraphClusterSummary[]>([])
  const [selectedClusterId, setSelectedClusterId] = useState<string>('')
  const [snapshots, setSnapshots] = useState<{ id: string; label: string }[]>([])
  const [snapshotId, setSnapshotId] = useState<string>('')
  const [providers, setProviders] = useState<{ id: string; name: string }[]>([])
  const [selectedProviderId, setSelectedProviderId] = useState<string>('')

  // Imported Docs State
  const [docs, setDocs] = useState<UserFlowDocInfo[]>([])
  const [selectedDocId, setSelectedDocId] = useState<string>('')
  const [pickedFilePaths, setPickedFilePaths] = useState<string[]>([])

  // Pipeline Execution State
  const [isParsing, setIsParsing] = useState<boolean>(false)
  const [isRunningMatch, setIsRunningMatch] = useState<boolean>(false)
  const [elapsedSeconds, setElapsedSeconds] = useState<number>(0)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  // Report & Graph State
  const [report, setReport] = useState<UserFlowReport | null>(null)
  const [businessFlows, setBusinessFlows] = useState<BusinessFlow[]>([])
  const [selectedGraphFlowId, setSelectedGraphFlowId] = useState<string | null>(null) // Level 1 vs Level 2
  const [selectedActivityId, setSelectedActivityId] = useState<string | null>(null) // Level 2 (activities) vs Level 3 (leaf steps)
  const [selectedStep, setSelectedStep] = useState<UserFlowStepReport | null>(null)
  const [showBdExtra, setShowBdExtra] = useState<boolean>(false)
  const [viewMode, setViewMode] = useState<'graph' | 'tree'>('graph')
  const [searchQuery, setSearchQuery] = useState<string>('')

  // Lookup maps
  const stepMap = useMemo(() => {
    const map: Record<string, UserFlowStepReport> = {}
    if (report?.flows) {
      for (const flow of report.flows) {
        for (const step of flow.steps) {
          map[step.id] = step
        }
      }
    }
    return map
  }, [report])

  const flowReportMap = useMemo(() => {
    const map: Record<string, UserFlowFlowReport> = {}
    if (report?.flows) {
      for (const flow of report.flows) {
        map[flow.id] = flow
      }
    }
    return map
  }, [report])

  const activityMap = useMemo(() => {
    const map: Record<string, UserFlowActivityReport> = {}
    if (report?.flows) {
      for (const flow of report.flows) {
        if (flow.activities) {
          for (const act of flow.activities) {
            map[act.id] = act
          }
        }
      }
    }
    return map
  }, [report])

  // Timer for execution
  useEffect(() => {
    let timer: any
    if (isRunningMatch || isParsing) {
      setElapsedSeconds(0)
      timer = setInterval(() => setElapsedSeconds((s) => s + 1), 1000)
    }
    return () => clearInterval(timer)
  }, [isRunningMatch, isParsing])

  // Load initial data
  const loadInitialData = useCallback(async () => {
    try {
      const [clusterList, providerList, repoList, docList] = await Promise.all([
        window.api?.docGraph?.listClusters?.() || [],
        window.api?.provider?.list?.() || [],
        window.api?.folder?.list?.() || [],
        window.api?.userFlow?.listDocs?.() || []
      ])

      setClusters(clusterList || [])
      if (clusterList && clusterList.length > 0) {
        setSelectedClusterId(clusterList[0].cluster_id)
        if (clusterList[0].snapshot_id) setSnapshotId(clusterList[0].snapshot_id)
      }

      setProviders((providerList || []).map((p: any) => ({ id: p.id, name: p.display_name || p.model_id || p.id })))
      if (providerList && providerList.length > 0) setSelectedProviderId(providerList[0].id)

      const snapList = (repoList || [])
        .filter((r: any) => r.active_snapshot_id)
        .map((r: any) => ({
          id: r.active_snapshot_id as string,
          label: `${r.name || 'Repo'} (${(r.active_snapshot_id as string).slice(0, 8)})`
        }))
      setSnapshots(snapList)
      if (snapList.length > 0 && !snapshotId) setSnapshotId(snapList[0].id)

      setDocs(docList || [])
      if (docList && docList.length > 0) {
        setSelectedDocId(docList[0].id)
      }
    } catch (e: any) {
      console.error('Failed to load initial data:', e)
    }
  }, [snapshotId])

  useEffect(() => {
    loadInitialData()
  }, [loadInitialData])

  // Load graph structure
  const loadGraph = useCallback(async () => {
    if (!selectedDocId) {
      setBusinessFlows([])
      setSelectedGraphFlowId(null)
      setSelectedActivityId(null)
      return
    }
    try {
      const res = await window.api?.userFlow?.graph?.(selectedDocId)
      if (res?.business_flows) {
        setBusinessFlows(res.business_flows)
      } else {
        setBusinessFlows([])
      }
    } catch (e: any) {
      console.error('Failed to load user flow graph:', e)
    }
  }, [selectedDocId])

  useEffect(() => {
    loadGraph()
  }, [loadGraph])

  // Load report data
  const loadReport = useCallback(async () => {
    if (!selectedDocId || !selectedClusterId || !snapshotId) return
    setErrorMessage(null)
    try {
      const res = await window.api?.userFlow?.report?.(selectedDocId, selectedClusterId, snapshotId)
      if (res) {
        setReport(res)
      }
    } catch (e: any) {
      console.error('Failed to load user flow report:', e)
      setErrorMessage(e.message || String(e))
    }
  }, [selectedDocId, selectedClusterId, snapshotId])

  useEffect(() => {
    if (selectedDocId && selectedClusterId && snapshotId) {
      loadReport()
    }
  }, [selectedDocId, selectedClusterId, snapshotId, loadReport])

  // Pick Files Action
  const handlePickFiles = async () => {
    try {
      const files = await window.api?.userFlow?.pickFiles?.()
      if (files && files.length > 0) {
        setPickedFilePaths(files)
      }
    } catch (e: any) {
      setErrorMessage(e.message || String(e))
    }
  }

  // Parse Action (Phase 1 + LLM#5 Condensation)
  const handleParse = async () => {
    if (pickedFilePaths.length === 0) return
    setIsParsing(true)
    setErrorMessage(null)
    try {
      const res = await window.api?.userFlow?.import?.({
        paths: pickedFilePaths,
        provider_id: selectedProviderId || null
      })
      if (res && res.doc_id) {
        setSelectedDocId(res.doc_id)
        setSelectedGraphFlowId(null)
        setSelectedActivityId(null)
        const updatedDocs = await window.api?.userFlow?.listDocs?.()
        setDocs(updatedDocs || [])
      }
    } catch (e: any) {
      setErrorMessage(`Parse failed: ${e.message || String(e)}`)
    } finally {
      setIsParsing(false)
    }
  }

  // Run Match Action (LLM#6 Tier-1 Matcher + Rescoped Tier-2 Align)
  const handleRunMatch = async () => {
    if (!selectedDocId || !selectedClusterId || !snapshotId) return
    setIsRunningMatch(true)
    setErrorMessage(null)
    try {
      await window.api?.userFlow?.run?.({
        doc_id: selectedDocId,
        cluster_id: selectedClusterId,
        snapshot_id: snapshotId,
        provider_id: selectedProviderId || null
      })
      await loadReport()
      await loadGraph()
    } catch (e: any) {
      setErrorMessage(`Match execution failed: ${e.message || String(e)}`)
    } finally {
      setIsRunningMatch(false)
    }
  }

  // Active Flow for Level 2 & 3
  const activeDetailFlow = useMemo(() => {
    if (!selectedGraphFlowId) return null
    return businessFlows.find((f) => f.id === selectedGraphFlowId) || null
  }, [businessFlows, selectedGraphFlowId])

  const activeDetailFlowReport = useMemo(() => {
    if (!selectedGraphFlowId) return null
    return flowReportMap[selectedGraphFlowId] || null
  }, [flowReportMap, selectedGraphFlowId])

  const activeActivity = useMemo(() => {
    if (!selectedActivityId) return null
    return activityMap[selectedActivityId] || null
  }, [activityMap, selectedActivityId])

  // Filter flows by search query
  const filteredBusinessFlows = useMemo(() => {
    if (!businessFlows) return []
    if (!searchQuery.trim()) return businessFlows
    const q = searchQuery.toLowerCase()
    return businessFlows.filter((f) => {
      const reportFlow = flowReportMap[f.id]
      const nameEn = reportFlow?.name_en || f.name
      const nameJa = reportFlow?.name_ja || ''
      const sheet = f.block_key || reportFlow?.sheet || ''
      return (
        nameEn.toLowerCase().includes(q) ||
        nameJa.toLowerCase().includes(q) ||
        sheet.toLowerCase().includes(q)
      )
    })
  }, [businessFlows, flowReportMap, searchQuery])

  const filteredReportFlows = useMemo(() => {
    if (!report?.flows) return []
    if (!searchQuery.trim()) return report.flows
    const q = searchQuery.toLowerCase()
    return report.flows.filter((f) =>
      f.name_en.toLowerCase().includes(q) ||
      f.name_ja.toLowerCase().includes(q) ||
      f.sheet.toLowerCase().includes(q) ||
      f.steps.some((s) => s.text_en.toLowerCase().includes(q) || s.text_ja.toLowerCase().includes(q))
    )
  }, [report, searchQuery])

  // -------------------------------------------------------------------------
  // 4. Two-Tier Graph Construction (Level 2: Activities vs Level 3: Leaf Steps)
  // -------------------------------------------------------------------------

  const { graphNodes, graphEdges, currentVisibleCount } = useMemo(() => {
    if (!activeDetailFlow) {
      return { graphNodes: [], graphEdges: [], currentVisibleCount: 0 }
    }

    // Level 2: Default Flow View -> Renders Activities
    if (!selectedActivityId) {
      const activitiesList = activeDetailFlowReport?.activities || []

      // If no activities available yet (e.g. before full parse), render fallback steps
      if (activitiesList.length === 0) {
        const skeleton = projectBusinessFlowSkeleton([activeDetailFlow])
        return { graphNodes: skeleton.nodes, graphEdges: skeleton.edges, currentVisibleCount: activeDetailFlow.steps.length }
      }

      // Build synthetic business flow of activities
      const synthSteps: BusinessFlowStep[] = activitiesList.map((act) => ({
        id: `act:${act.id}`,
        flow_id: activeDetailFlow.id,
        name: `${act.name_en} (${act.step_count} steps)`,
        functionality: act.summary_en,
        ordinal: act.ordinal,
        source_node_ids: act.member_step_ids,
        created_at: ''
      }))

      const synthBranches: BusinessFlowBranch[] = []
      for (let i = 0; i < synthSteps.length - 1; i++) {
        synthBranches.push({
          id: `br:${synthSteps[i].id}->${synthSteps[i + 1].id}`,
          flow_id: activeDetailFlow.id,
          source_step_id: synthSteps[i].id,
          target_step_id: synthSteps[i + 1].id,
          branch_kind: 'SUCCESS',
          guard_description: '',
          source_edge_ids: [],
          created_at: ''
        })
      }

      const synthFlow: BusinessFlow = {
        ...activeDetailFlow,
        steps: synthSteps,
        branches: synthBranches
      }

      const skeleton = projectBusinessFlowSkeleton([synthFlow])
      const decoratedNodes = skeleton.nodes.map((n: Node) => {
        if (n.type === 'businessStep') {
          const actId = n.id.replace(/^act:/, '')
          const act = activityMap[actId]
          const bdVerdict = act?.activity_match ? USER_VERDICT_TO_BD[act.activity_match] : 'UNKNOWN'
          return {
            ...n,
            data: {
              ...n.data,
              verdict: bdVerdict
            }
          }
        }
        return n
      })

      return { graphNodes: decoratedNodes, graphEdges: skeleton.edges, currentVisibleCount: activitiesList.length }
    }

    // Level 3: Leaf Steps for Selected Activity
    const act = activityMap[selectedActivityId]
    const memberIdsSet = new Set(act?.member_step_ids || [])
    const allLeafSteps = activeDetailFlow.leaf_steps || activeDetailFlow.steps || []
    const memberSteps = allLeafSteps.filter((s) => memberIdsSet.has(s.id))

    const leafBranches: BusinessFlowBranch[] = []
    for (let i = 0; i < memberSteps.length - 1; i++) {
      leafBranches.push({
        id: `br:${memberSteps[i].id}->${memberSteps[i + 1].id}`,
        flow_id: activeDetailFlow.id,
        source_step_id: memberSteps[i].id,
        target_step_id: memberSteps[i + 1].id,
        branch_kind: 'SUCCESS',
        guard_description: '',
        source_edge_ids: [],
        created_at: ''
      })
    }

    const leafFlow: BusinessFlow = {
      ...activeDetailFlow,
      steps: memberSteps,
      branches: leafBranches
    }

    const skeleton = projectBusinessFlowSkeleton([leafFlow])
    const decoratedNodes = skeleton.nodes.map((n: Node) => {
      if (n.type === 'businessStep') {
        const step = stepMap[n.id]
        const stepVerdict = step?.verdict
        const bdVerdict = stepVerdict ? USER_VERDICT_TO_BD[stepVerdict] : 'UNKNOWN'
        const isSelected = selectedStep?.id === n.id
        return {
          ...n,
          className: isSelected ? 'ring-2 ring-indigo-400 ring-offset-2 ring-offset-slate-950 rounded-lg' : '',
          data: { ...n.data, verdict: bdVerdict }
        }
      }
      return n
    })

    return { graphNodes: decoratedNodes, graphEdges: skeleton.edges, currentVisibleCount: memberSteps.length }
  }, [activeDetailFlow, activeDetailFlowReport, selectedActivityId, activityMap, stepMap, selectedStep])

  const [nodes, setNodes, onNodesChange] = useNodesState(graphNodes)
  const [edges, setEdges, onEdgesChange] = useEdgesState(graphEdges)

  useEffect(() => {
    setNodes(graphNodes)
    setEdges(graphEdges)
  }, [graphNodes, graphEdges, setNodes, setEdges])

  return (
    <div className="flex flex-col h-full bg-slate-950 text-slate-100 overflow-hidden select-none">
      {/* 1. Header Toolbar */}
      <div className="p-3 bg-slate-900 border-b border-slate-800 flex flex-wrap items-center justify-between gap-3 shrink-0">
        {/* Left: Title & File Importer */}
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 font-bold text-slate-100">
            <Workflow className="w-5 h-5 text-indigo-400" />
            <span className="tracking-wide">User Flow ⟷ BD Reconciliation</span>
          </div>

          <div className="h-5 w-px bg-slate-800 hidden sm:block"></div>

          {/* Doc Picker & File Select */}
          <div className="flex items-center gap-2">
            <button
              onClick={handlePickFiles}
              disabled={isParsing || isRunningMatch}
              className="px-2.5 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-xs font-semibold text-slate-200 border border-slate-700 flex items-center gap-1.5 transition-colors disabled:opacity-50"
            >
              <UploadCloud className="w-3.5 h-3.5 text-indigo-400" />
              <span>
                {pickedFilePaths.length === 0
                  ? 'Pick .xlsx'
                  : `${pickedFilePaths.length} file${pickedFilePaths.length > 1 ? 's' : ''}`}
              </span>
            </button>

            <button
              onClick={handleParse}
              disabled={isParsing || isRunningMatch || pickedFilePaths.length === 0}
              className="px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs font-semibold text-white flex items-center gap-1.5 transition-colors shadow disabled:opacity-50"
            >
              {isParsing ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <FileSpreadsheet className="w-3.5 h-3.5" />}
              <span>{isParsing ? 'Parsing...' : 'Parse'}</span>
            </button>

            {docs.length > 0 && (
              <select
                value={selectedDocId}
                onChange={(e) => {
                  setSelectedDocId(e.target.value)
                  setSelectedGraphFlowId(null)
                  setSelectedActivityId(null)
                }}
                disabled={isParsing || isRunningMatch}
                className="bg-slate-800 border border-slate-700 text-xs rounded-lg px-2.5 py-1.5 text-slate-200 max-w-[180px] truncate"
              >
                {docs.map((d: UserFlowDocInfo) => (
                  <option key={d.id} value={d.id}>
                    {d.source_name}
                  </option>
                ))}
              </select>
            )}
          </div>
        </div>

        {/* Right: Selectors & Run Action */}
        <div className="flex items-center gap-2">
          {/* Cluster Picker */}
          <select
            value={selectedClusterId}
            onChange={(e) => setSelectedClusterId(e.target.value)}
            disabled={isParsing || isRunningMatch}
            className="bg-slate-800 border border-slate-700 text-xs rounded-lg px-2.5 py-1.5 text-slate-200 max-w-[160px] truncate"
            title="Select BD Flow Cluster"
          >
            {clusters.map((c: DocGraphClusterSummary) => (
              <option key={c.cluster_id} value={c.cluster_id}>
                Cluster: {c.cluster_name || c.cluster_id.slice(0, 10)}
              </option>
            ))}
          </select>

          {/* Snapshot Picker */}
          <select
            value={snapshotId}
            onChange={(e) => setSnapshotId(e.target.value)}
            disabled={isParsing || isRunningMatch}
            className="bg-slate-800 border border-slate-700 text-xs rounded-lg px-2.5 py-1.5 text-slate-200 max-w-[160px] truncate"
            title="Select Code Repository Snapshot"
          >
            {snapshots.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label}
              </option>
            ))}
          </select>

          {/* Provider Picker */}
          <select
            value={selectedProviderId}
            onChange={(e) => setSelectedProviderId(e.target.value)}
            disabled={isParsing || isRunningMatch}
            className="bg-slate-800 border border-slate-700 text-xs rounded-lg px-2.5 py-1.5 text-slate-200 max-w-[140px] truncate"
            title="Select LLM Provider"
          >
            {providers.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>

          {/* Run Match Button */}
          <button
            onClick={handleRunMatch}
            disabled={isRunningMatch || isParsing || !selectedDocId || !selectedClusterId || !snapshotId}
            className="px-4 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-xs font-bold text-white flex items-center gap-1.5 transition-colors shadow-lg disabled:opacity-50"
          >
            {isRunningMatch ? (
              <>
                <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                <span>Matching ({elapsedSeconds}s)...</span>
              </>
            ) : (
              <>
                <Play className="w-3.5 h-3.5 fill-current" />
                <span>Run Match</span>
              </>
            )}
          </button>
        </div>
      </div>

      {/* 2. Error Banner */}
      {errorMessage && (
        <div className="p-3 bg-red-950/80 border-b border-red-800 flex items-center justify-between text-xs text-red-200">
          <div className="flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-red-400 shrink-0" />
            <span>{errorMessage}</span>
          </div>
          <button onClick={() => setErrorMessage(null)} className="text-red-400 hover:text-red-200">
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* 3. KPI Header Summary Tiles */}
      {report?.summary && (
        <div className="px-4 py-2.5 bg-slate-900/60 border-b border-slate-800/80 flex flex-wrap items-center justify-between gap-3 text-xs shrink-0">
          {/* Flow Level Breakdown */}
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wider">Flows:</span>
            <span className="px-2 py-0.5 rounded-full bg-emerald-950/80 text-emerald-400 border border-emerald-500/30 font-semibold">
              🟢 {report.summary.flow_counts.MATCHED} Matched
            </span>
            <span className="px-2 py-0.5 rounded-full bg-amber-950/80 text-amber-400 border border-amber-500/30 font-semibold">
              🟡 {report.summary.flow_counts.PARTIAL} Partial
            </span>
            <span className="px-2 py-0.5 rounded-full bg-red-950/80 text-red-400 border border-red-500/30 font-semibold">
              🔴 {report.summary.flow_counts.DIVERGENT} Divergent
            </span>
            <span className="px-2 py-0.5 rounded-full bg-orange-950/80 text-orange-400 border border-orange-500/30 font-semibold">
              🟠 {report.summary.flow_counts.UNCOVERED} Uncovered
            </span>
          </div>

          {/* Activities Summary */}
          {report.summary.activity_counts && (
            <div className="flex items-center gap-2">
              <span className="text-[11px] font-bold text-indigo-400 uppercase tracking-wider">Activities:</span>
              <span className="px-2 py-0.5 rounded-full bg-slate-900 text-slate-300 border border-slate-700 font-semibold">
                {report.summary.total_activities || 0} Total ({report.summary.activity_counts.MATCHED} 🟢 / {report.summary.activity_counts.PARTIAL} 🟡 / {report.summary.activity_counts.DIVERGENT} 🔴)
              </span>
            </div>
          )}

          {/* Step Level Breakdown */}
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wider">Steps:</span>
            <span className="px-2 py-0.5 rounded-full bg-emerald-950/80 text-emerald-400 border border-emerald-500/30 font-semibold">
              {report.summary.step_counts.COVERED}/{report.summary.in_scope_steps} in-scope covered
            </span>
            {report.summary.step_counts.BD_MISSING > 0 && (
              <span className="px-2 py-0.5 rounded-full bg-orange-950/80 text-orange-400 border border-orange-500/30 font-semibold">
                ! {report.summary.step_counts.BD_MISSING} BD Missing
              </span>
            )}
            {report.summary.step_counts.CONTRADICTED > 0 && (
              <span className="px-2 py-0.5 rounded-full bg-red-950/80 text-red-400 border border-red-500/30 font-semibold">
                ✕ {report.summary.step_counts.CONTRADICTED} Contradicted
              </span>
            )}
            <span className="px-2 py-0.5 rounded-full bg-purple-950/80 text-purple-400 border border-purple-500/30 font-semibold">
              + {report.summary.bd_extra_count} BD Extra
            </span>
          </div>

          {/* View Mode Toggle */}
          <div className="flex items-center rounded-lg bg-slate-800 p-0.5 border border-slate-700">
            <button
              onClick={() => setViewMode('graph')}
              className={`px-2.5 py-1 rounded text-xs font-medium flex items-center gap-1.5 transition-colors ${
                viewMode === 'graph' ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <LayoutGrid className="w-3.5 h-3.5" />
              <span>Process Graph</span>
            </button>
            <button
              onClick={() => setViewMode('tree')}
              className={`px-2.5 py-1 rounded text-xs font-medium flex items-center gap-1.5 transition-colors ${
                viewMode === 'tree' ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <Layers className="w-3.5 h-3.5" />
              <span>Hierarchical Tree</span>
            </button>
          </div>
        </div>
      )}

      {/* 4. Main Body: Level 1 Overview vs Level 2 Flow Activities vs Level 3 Leaf Steps */}
      <div className="flex-1 flex min-h-0 relative">
        {/* Empty State */}
        {businessFlows.length === 0 && (!report || report.flows.length === 0) ? (
          <div className="flex-1 flex flex-col items-center justify-center p-8 text-center text-slate-400">
            <Workflow className="w-16 h-16 text-indigo-400/40 mb-4 stroke-1" />
            <h3 className="text-lg font-bold text-slate-200 mb-1">No User Flow Loaded</h3>
            <p className="text-xs max-w-md text-slate-400 mb-6">
              Pick customer Excel scenario files (.xlsx/.xlsm) above to parse the flow structure into condensed activities,
              then click &quot;Run Match&quot; to evaluate two-tier alignment.
            </p>
            <button
              onClick={handlePickFiles}
              className="px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-bold flex items-center gap-2 transition-colors shadow-lg"
            >
              <UploadCloud className="w-4 h-4" />
              <span>Select Customer Excel Scenario</span>
            </button>
          </div>
        ) : viewMode === 'graph' ? (
          selectedGraphFlowId === null ? (
            /* LEVEL 1: OVERVIEW (Flow Cards Grid) */
            <div className="flex-1 h-full overflow-y-auto p-6 bg-slate-950 flex flex-col space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-3 shrink-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-bold text-slate-200">
                    User Flows ({filteredBusinessFlows.length})
                  </span>
                  <span className="text-xs text-slate-400">
                    — Click any flow card to inspect its condensed activities
                  </span>
                </div>

                <div className="relative w-64">
                  <Search className="w-4 h-4 absolute left-3 top-2.5 text-slate-500" />
                  <input
                    type="text"
                    placeholder="Filter flows by name or sheet..."
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    className="w-full bg-slate-900 border border-slate-800 rounded-lg pl-9 pr-3 py-1.5 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
                  />
                </div>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
                {filteredBusinessFlows.map((f: BusinessFlow) => (
                  <FlowOverviewCard
                    key={f.id}
                    businessFlow={f}
                    flowReport={flowReportMap[f.id]}
                    onSelect={() => {
                      setSelectedGraphFlowId(f.id)
                      setSelectedActivityId(null)
                      setSelectedStep(null)
                    }}
                  />
                ))}
              </div>
            </div>
          ) : (
            /* LEVEL 2 & 3: DETAIL (Activities & Leaf Steps) */
            <div className="flex-1 h-full bg-slate-950 flex flex-col relative">
              {/* Detail Top Sub-Toolbar with Breadcrumbs & Flow Switcher */}
              <div className="px-4 py-2 bg-slate-900/90 border-b border-slate-800 flex items-center justify-between gap-3 shrink-0 z-10">
                <div className="flex items-center gap-3 min-w-0">
                  {/* Primary Back Button */}
                  <button
                    onClick={() => {
                      if (selectedActivityId) {
                        setSelectedActivityId(null)
                      } else {
                        setSelectedGraphFlowId(null)
                      }
                      setSelectedStep(null)
                    }}
                    className="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-xs font-bold text-slate-200 border border-slate-700 flex items-center gap-1.5 transition-colors shadow shrink-0"
                    title={selectedActivityId ? 'Back to Activities' : 'Back to all flows'}
                  >
                    <ArrowLeft className="w-3.5 h-3.5 text-indigo-400" />
                    <span>{selectedActivityId ? '← Activities' : '← All Flows'}</span>
                  </button>

                  <div className="h-4 w-px bg-slate-800 shrink-0"></div>

                  {/* Interactive Breadcrumb Trail */}
                  <BreadcrumbTrail
                    flowName={activeDetailFlowReport?.name_en || activeDetailFlow?.name || 'Flow Detail'}
                    flowOrdinal={activeDetailFlow?.ordinal || activeDetailFlowReport?.ordinal || 1}
                    activityName={activeActivity?.name_en}
                    currentStepCount={currentVisibleCount}
                    onNavigateFlows={() => {
                      setSelectedGraphFlowId(null)
                      setSelectedActivityId(null)
                      setSelectedStep(null)
                    }}
                    onNavigateFlow={() => {
                      setSelectedActivityId(null)
                      setSelectedStep(null)
                    }}
                  />
                </div>

                <div className="flex items-center gap-2 shrink-0">
                  {activeActivity ? (
                    <span
                      className={`text-[10px] font-bold px-2 py-0.5 rounded-full border ${
                        MATCH_STATUS_META[activeActivity.match_status]?.bg || 'bg-slate-900'
                      } ${MATCH_STATUS_META[activeActivity.match_status]?.color || 'text-slate-300'} ${
                        MATCH_STATUS_META[activeActivity.match_status]?.border || 'border-slate-700'
                      }`}
                    >
                      {MATCH_STATUS_META[activeActivity.match_status]?.label}
                    </span>
                  ) : activeDetailFlowReport && (
                    <span
                      className={`text-[10px] font-bold px-2 py-0.5 rounded-full border ${
                        VERDICT_META[activeDetailFlowReport.flow_match]?.bg || 'bg-slate-900'
                      } ${VERDICT_META[activeDetailFlowReport.flow_match]?.color || 'text-slate-300'} ${
                        VERDICT_META[activeDetailFlowReport.flow_match]?.border || 'border-slate-700'
                      }`}
                    >
                      {VERDICT_META[activeDetailFlowReport.flow_match]?.icon}{' '}
                      {VERDICT_META[activeDetailFlowReport.flow_match]?.label || activeDetailFlowReport.flow_match}
                    </span>
                  )}

                  <select
                    value={selectedGraphFlowId || ''}
                    onChange={(e) => {
                      setSelectedGraphFlowId(e.target.value)
                      setSelectedActivityId(null)
                      setSelectedStep(null)
                    }}
                    className="bg-slate-800 border border-slate-700 text-xs rounded-lg px-2.5 py-1 text-slate-200 max-w-[160px] truncate"
                  >
                    {businessFlows.map((f: BusinessFlow) => (
                      <option key={f.id} value={f.id}>
                        Flow #{f.ordinal}: {f.name}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              {/* ReactFlow Canvas */}
              <div className="flex-1 h-full relative">
                <ReactFlow
                  nodes={nodes}
                  edges={edges}
                  onNodesChange={onNodesChange}
                  onEdgesChange={onEdgesChange}
                  nodeTypes={nodeTypes}
                  edgeTypes={edgeTypes}
                  onNodeClick={(_evt, node) => {
                    // Click on an Activity node -> drill into its member steps
                    if (node.id.startsWith('act:')) {
                      const actId = node.id.slice(4)
                      setSelectedActivityId(actId)
                      setSelectedStep(null)
                      return
                    }

                    // Click on a Step node -> open evidence drawer
                    if (stepMap[node.id]) {
                      setSelectedStep(stepMap[node.id])
                    }
                  }}
                  fitView
                  className="bg-slate-950"
                >
                  <Background color="#1e293b" gap={20} size={1} />
                  <Controls className="!bg-slate-900 !border-slate-800 !text-slate-300" />
                </ReactFlow>

                {/* Map Legend */}
                <div className="absolute bottom-4 left-4 bg-slate-900/90 border border-slate-800 p-3 rounded-xl text-xs space-y-2 backdrop-blur-md shadow-2xl max-w-xs">
                  <div className="flex items-center justify-between">
                    <span className="font-bold text-slate-300 text-[11px]">
                      {!selectedActivityId ? 'Activity Level (LLM#5)' : 'Leaf Steps Level'}
                    </span>
                    <span className="text-[10px] text-indigo-400 font-mono">
                      {!selectedActivityId ? 'Click activity to drill' : 'Click step for drawer'}
                    </span>
                  </div>
                  <div className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-[11px]">
                    <span className="flex items-center gap-1.5 text-emerald-400">🟢 MATCHED / COVERED</span>
                    <span className="flex items-center gap-1.5 text-amber-400">🟡 PARTIAL / BD_MISSING</span>
                    <span className="flex items-center gap-1.5 text-red-400">🔴 DIVERGENT / CONTRADICTED</span>
                    <span className="flex items-center gap-1.5 text-slate-400">⚪ UNVERIFIABLE / OOS</span>
                  </div>
                </div>
              </div>
            </div>
          )
        ) : (
          /* Tree View */
          <div className="flex-1 h-full overflow-y-auto p-4 space-y-4 bg-slate-950">
            <div className="relative max-w-md">
              <Search className="w-4 h-4 absolute left-3 top-2.5 text-slate-500" />
              <input
                type="text"
                placeholder="Search flows or steps..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full bg-slate-900 border border-slate-800 rounded-lg pl-9 pr-3 py-1.5 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
              />
            </div>

            <div className="space-y-3">
              {filteredReportFlows.map((flow: UserFlowFlowReport) => {
                const meta = VERDICT_META[flow.flow_match] || VERDICT_META.OUT_OF_SCOPE

                return (
                  <div
                    key={flow.id}
                    className={`rounded-xl border ${meta.nodeBg} ${meta.nodeBorder} overflow-hidden shadow-lg transition-all`}
                  >
                    <div className="p-3.5 flex items-center justify-between">
                      <div className="flex items-center gap-2.5 min-w-0">
                        <span className="text-xs font-mono font-bold text-slate-400">#{flow.ordinal}</span>
                        <div className="min-w-0">
                          <div className="font-semibold text-sm text-slate-100 truncate">{flow.name_en || flow.name_ja}</div>
                          <div className="text-[11px] text-slate-400 font-mono mt-0.5">
                            Sheet: {flow.sheet} · {flow.activities?.length || 0} activities
                          </div>
                        </div>
                      </div>

                      <div className="flex items-center gap-3 shrink-0">
                        <span className="text-xs text-slate-300 font-semibold">
                          {flow.counts.COVERED}/{flow.steps.filter((s) => s.in_scope).length} covered
                        </span>
                        <span className={`text-xs font-bold px-2.5 py-0.5 rounded-full border ${meta.bg} ${meta.color} ${meta.border}`}>
                          {meta.icon} {meta.label}
                        </span>
                        <button
                          onClick={() => {
                            setSelectedGraphFlowId(flow.id)
                            setSelectedActivityId(null)
                            setViewMode('graph')
                          }}
                          className="px-2 py-1 rounded bg-indigo-600/80 hover:bg-indigo-600 text-white text-xs font-semibold flex items-center gap-1 transition-colors"
                        >
                          <span>Graph</span>
                          <ArrowRight className="w-3 h-3" />
                        </button>
                      </div>
                    </div>

                    {/* Activities inside tree */}
                    {flow.activities && flow.activities.length > 0 && (
                      <div className="border-t border-slate-800/80 bg-slate-950/90 divide-y divide-slate-800/50">
                        {flow.activities.map((act) => {
                          const aMeta = VERDICT_META[act.activity_match] || VERDICT_META.UNVERIFIABLE
                          const matchMeta = MATCH_STATUS_META[act.match_status] || MATCH_STATUS_META.NONE

                          return (
                            <div key={act.id} className="p-3">
                              <div className="flex items-center justify-between mb-2">
                                <div className="flex items-center gap-2">
                                  <span className="text-xs font-mono font-bold text-indigo-400">A{act.ordinal}</span>
                                  <span className="text-xs font-semibold text-slate-200">{act.name_en}</span>
                                  <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded border ${matchMeta.bg} ${matchMeta.color} ${matchMeta.border}`}>
                                    {matchMeta.label}
                                  </span>
                                </div>
                                <span className={`text-[10px] font-bold px-2 py-0.5 rounded border ${aMeta.bg} ${aMeta.color} ${aMeta.border}`}>
                                  {aMeta.icon} {aMeta.label} ({act.counts.COVERED}/{act.step_count})
                                </span>
                              </div>

                              {/* Member steps list */}
                              <div className="pl-4 space-y-1.5 border-l border-slate-800 ml-2 mt-2">
                                {flow.steps
                                  .filter((s) => act.member_step_ids.includes(s.id))
                                  .map((step) => {
                                    const sMeta = VERDICT_META[step.verdict] || VERDICT_META.UNVERIFIABLE
                                    const isSelected = selectedStep?.id === step.id
                                    return (
                                      <div
                                        key={step.id}
                                        onClick={() => setSelectedStep(step)}
                                        className={`p-2 rounded flex items-center justify-between cursor-pointer hover:bg-slate-900 transition-colors ${
                                          isSelected ? 'bg-indigo-950/40 border border-indigo-500/50' : ''
                                        }`}
                                      >
                                        <div className="flex items-center gap-2 min-w-0">
                                          <span className="text-[10px] font-mono text-slate-500">S{step.ordinal}</span>
                                          <span className="text-xs text-slate-300 truncate">{step.text_en || step.text_ja}</span>
                                        </div>
                                        <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded border shrink-0 ${sMeta.bg} ${sMeta.color} ${sMeta.border}`}>
                                          {sMeta.icon} {sMeta.label}
                                        </span>
                                      </div>
                                    )
                                  })}
                              </div>
                            </div>
                          )
                        })}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        )}

        {/* Step Evidence Drawer */}
        <StepEvidenceDrawer step={selectedStep} onClose={() => setSelectedStep(null)} />
      </div>

      {/* 5. BD_EXTRA Panel */}
      {report?.bd_extra && (
        <BdExtraSection
          bdExtraList={report.bd_extra}
          isOpen={showBdExtra}
          onToggle={() => setShowBdExtra(!showBdExtra)}
        />
      )}
    </div>
  )
}
