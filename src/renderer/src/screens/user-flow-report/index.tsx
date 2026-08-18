import React, { useState, useEffect, useCallback, useMemo } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  Handle,
  Position,
  useNodesState,
  useEdgesState,
  type Node,
  type NodeProps
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
  LayoutGrid,
  Ban,
  HelpCircle
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
  UserFlowBranchCoverageItem,
  UserFlowPairMatch,
  BusinessFlow,
  BusinessFlowStep,
  BusinessFlowBranch
} from '../../types/electron'
import { projectBusinessFlowSkeleton } from '../graph/layout'
import { nodeTypes, edgeTypes } from '../bd-flow'

// ---------------------------------------------------------------------------
// 1. Single Source of Truth for the Three-Question Taxonomy
// ---------------------------------------------------------------------------

/** Q3: Code Proof (Tier-2 Node & Verdict Styles) */
export const USER_VERDICT_TO_BD: Record<UserFlowStepVerdict | string, 'MATCH' | 'PARTIAL' | 'BROKEN' | 'UNKNOWN'> = {
  MATCHED: 'MATCH',
  COVERED: 'MATCH',
  PARTIAL: 'PARTIAL',
  DIVERGENT: 'BROKEN',
  CONTRADICTED: 'BROKEN',
  BD_MISSING: 'PARTIAL',
  BD_UNMAPPED: 'PARTIAL',
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
  // Flow & Activity Rollups (Rollup vocabulary)
  MATCHED: {
    label: 'Proven',
    color: 'text-emerald-400',
    bg: 'bg-emerald-950/80',
    border: 'border-emerald-500/40',
    nodeBg: 'bg-emerald-950/60',
    nodeBorder: 'border-emerald-500/80',
    icon: '🟢',
    description: 'All in-scope steps are proven by code and BD specification.'
  },
  PARTIAL: {
    label: 'Partly proven',
    color: 'text-amber-400',
    bg: 'bg-amber-950/80',
    border: 'border-amber-500/40',
    nodeBg: 'bg-amber-950/60',
    nodeBorder: 'border-amber-500/80',
    icon: '🟡',
    description: 'Some steps proven; some lack proof or BD specification.'
  },
  DIVERGENT: {
    label: 'Conflict',
    color: 'text-rose-400',
    bg: 'bg-rose-950/80',
    border: 'border-rose-500/40',
    nodeBg: 'bg-rose-950/60',
    nodeBorder: 'border-rose-500/80',
    icon: '🔴',
    description: 'Contains contradictions between customer flow and BD/code.'
  },
  UNCOVERED: {
    label: 'Unproven',
    color: 'text-slate-300',
    bg: 'bg-slate-900/80',
    border: 'border-slate-700/40',
    nodeBg: 'bg-slate-900/60',
    nodeBorder: 'border-slate-700/80',
    icon: '⚪',
    description: 'No in-scope steps proven by code.'
  },
  OUT_OF_SCOPE: {
    label: 'Excluded',
    color: 'text-slate-400',
    bg: 'bg-slate-900/80',
    border: 'border-dashed border-slate-700/60',
    nodeBg: 'bg-slate-900/60',
    nodeBorder: 'border-dashed border-slate-700/80',
    icon: '⊘',
    description: 'Excluded from PoC scope by customer.'
  },
  // Step Verdicts (Code Proof vocabulary)
  COVERED: {
    label: 'Proven',
    color: 'text-emerald-400',
    bg: 'bg-emerald-950/80',
    border: 'border-emerald-500/40',
    nodeBg: 'bg-emerald-950/40',
    nodeBorder: 'border-emerald-500/60',
    icon: '✓',
    description: 'Behavior confirmed in code with accepted citation.'
  },
  BD_MISSING: {
    label: 'Proven, not in BD',
    color: 'text-amber-400',
    bg: 'bg-amber-950/80',
    border: 'border-amber-500/40',
    nodeBg: 'bg-amber-950/40',
    nodeBorder: 'border-amber-500/60',
    icon: '!',
    description: 'Code proves the behavior, but BD specification does not describe it.'
  },
  CONTRADICTED: {
    label: 'Conflict',
    color: 'text-rose-400',
    bg: 'bg-rose-950/80',
    border: 'border-rose-500/40',
    nodeBg: 'bg-rose-950/40',
    nodeBorder: 'border-rose-500/60',
    icon: '✕',
    description: 'Behavior directly contradicts BD or code implementation.'
  },
  UNVERIFIABLE: {
    label: 'No proof',
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
    color: 'text-indigo-300',
    bg: 'bg-indigo-950/80',
    border: 'border-indigo-500/40',
    nodeBg: 'bg-indigo-950/40',
    nodeBorder: 'border-indigo-500/60',
    icon: '+',
    description: 'BD unit describes behavior not mentioned in customer user flow.'
  },
  BD_UNMAPPED: {
    label: 'BD Unmapped',
    color: 'text-indigo-300',
    bg: 'bg-indigo-950/60',
    border: 'border-dashed border-indigo-500/40',
    nodeBg: 'bg-indigo-950/40',
    nodeBorder: 'border-dashed border-indigo-500/50',
    icon: '○',
    description: 'BD unit belongs to a matched flow, but was not mapped by any user step.'
  },
  PENDING: {
    label: 'Pending match',
    color: 'text-indigo-300',
    bg: 'bg-indigo-950/60',
    border: 'border-indigo-500/40',
    nodeBg: 'bg-indigo-950/40',
    nodeBorder: 'border-indigo-500/60',
    icon: '⏳',
    description: 'Structure parsed; run match to calculate alignment verdicts.'
  }
}

/** Q2: BD Spec (Tier-1 Semantic - Indigo family ONLY, no green/amber/red) */
export const MATCH_STATUS_META: Record<
  string,
  { label: string; color: string; bg: string; border: string }
> = {
  FULLY: {
    label: 'BD describes · full',
    color: 'text-indigo-300',
    bg: 'bg-indigo-950/30',
    border: 'border-indigo-500/60'
  },
  PARTIAL: {
    label: 'BD describes · partly',
    color: 'text-indigo-300',
    bg: 'bg-indigo-950/30',
    border: 'border-indigo-500/40'
  },
  UNRESOLVED: {
    label: 'BD match undetermined',
    color: 'text-slate-400',
    bg: 'bg-slate-900/30',
    border: 'border-dashed border-slate-600'
  },
  NONE: {
    label: 'Not in BD',
    color: 'text-slate-400',
    bg: 'bg-slate-900/30',
    border: 'border-slate-700'
  }
}

function translateBasis(basis: string): string {
  switch (basis) {
    case 'own_citation':
      return 'Proven by its own code citation'
    case 'bd_verdict':
      return 'Proven via a BD unit whose BD↔code verdict is MATCH/PARTIAL'
    case 'tier1_none_match':
      return 'Skipped: no BD flow matched this activity'
    case 'tier1_unresolved':
      return 'Skipped: tier-1 matching did not resolve'
    default:
      return basis
  }
}

// ---------------------------------------------------------------------------
// 2. Custom Node Components for Graph Views (Task C)
// ---------------------------------------------------------------------------

function UserFlowActivityNode({ data }: NodeProps): React.ReactElement {
  const act = data.activity as UserFlowActivityReport | undefined
  const name = (data.name as string) || act?.name_en || 'Activity'
  const matchStatus = (data.matchStatus as string) || act?.match_status || 'NONE'
  const actMatch = (data.activityMatch as string) || act?.activity_match || 'UNCOVERED'
  const counts =
    (data.counts as UserFlowActivityReport['counts']) ||
    act?.counts || { COVERED: 0, BD_MISSING: 0, CONTRADICTED: 0, UNVERIFIABLE: 0, OUT_OF_SCOPE: 0 }
  const pairMatches = (data.pairMatches as UserFlowPairMatch[]) || act?.pair_matches || []

  const proofStyle = VERDICT_META[actMatch] || VERDICT_META.UNCOVERED
  const specMeta = MATCH_STATUS_META[matchStatus] || MATCH_STATUS_META.NONE

  const isOutOfScope = actMatch === 'OUT_OF_SCOPE'
  const totalSteps = act?.step_count || Object.values(counts).reduce((a, b) => a + b, 0) || 1

  // Max pair confidence
  const maxConfidence =
    pairMatches.length > 0 ? Math.max(...pairMatches.map((p) => p.confidence || 0)) : null

  // Reconciliation caption
  let reconciliationCaption: React.ReactNode = null
  if (isOutOfScope) {
    reconciliationCaption = (
      <span className="text-[10px] text-slate-400 font-mono truncate flex items-center gap-1">
        <Ban className="w-2.5 h-2.5 text-slate-500" />
        <span>⊘ Excluded by customer</span>
      </span>
    )
  } else if ((matchStatus === 'FULLY' || matchStatus === 'PARTIAL') && counts.COVERED === 0) {
    reconciliationCaption = (
      <span className="text-[10px] text-amber-400/90 font-medium truncate">
        BD describes it · no code proof accepted
      </span>
    )
  } else if (matchStatus === 'NONE') {
    reconciliationCaption = (
      <span className="text-[10px] text-slate-500 font-mono truncate">
        No BD flow matched · code check skipped
      </span>
    )
  } else if (matchStatus === 'UNRESOLVED') {
    reconciliationCaption = (
      <span className="text-[10px] text-slate-500 font-mono truncate">
        Tier-1 undetermined · code check skipped
      </span>
    )
  } else if (counts.COVERED > 0) {
    reconciliationCaption = (
      <span className="text-[10px] text-emerald-400 font-mono truncate">
        ✓ {counts.COVERED}/{totalSteps} steps proven
      </span>
    )
  }

  const pairTooltip =
    pairMatches.length > 0
      ? pairMatches
          .map(
            (p) =>
              `${p.bd_flow_name || 'BD Flow'} · ${MATCH_STATUS_META[p.match_status]?.label || p.match_status} · ${Math.round((p.confidence || 0) * 100)}%${p.reason ? ` — ${p.reason}` : ''}`
          )
          .join('\n')
      : specMeta.label

  return (
    <div
      className={`relative px-3 py-2 rounded-xl text-xs shadow-lg backdrop-blur-md w-[240px] min-h-[96px] bg-slate-900 border transition-all duration-150 hover:border-indigo-400 hover:ring-2 hover:ring-indigo-500/30 flex flex-col justify-between ${
        isOutOfScope
          ? 'border-dashed border-slate-700/80 bg-slate-900/40 opacity-80'
          : proofStyle.nodeBorder
      }`}
    >
      <Handle type="target" position={Position.Top} className="!bg-slate-400 !w-2 !h-2 !border-0" />
      <Handle type="source" position={Position.Bottom} className="!bg-slate-400 !w-2 !h-2 !border-0" />

      {/* Row 1: Ordinal + Tier-1 Outline Pill + Max Confidence */}
      <div className="flex items-center justify-between gap-1.5 mb-1">
        <span className="font-mono text-[11px] font-bold text-slate-400">
          A{act?.ordinal ?? (data.actOrdinal as number | undefined) ?? 1}
        </span>

        <div
          className={`flex items-center gap-1 px-2 py-0.5 rounded-full border text-[10px] font-medium truncate max-w-[170px] ${specMeta.bg} ${specMeta.color} ${specMeta.border}`}
          title={pairTooltip}
        >
          <Layers className="w-2.5 h-2.5 shrink-0 text-indigo-400" />
          <span className="truncate">{specMeta.label}</span>
          {maxConfidence !== null && maxConfidence > 0 && (
            <span className="font-mono text-[9px] text-indigo-400/80 shrink-0">
              {Math.round(maxConfidence * 100)}%
            </span>
          )}
        </div>
      </div>

      {/* Row 2: Name */}
      <div className="font-semibold text-slate-100 text-xs leading-tight line-clamp-2 my-0.5" title={name}>
        {name}
      </div>

      {/* Row 3: 5-segment mini bar + Dominant count / Reconciliation caption */}
      <div className="mt-auto pt-1.5 space-y-1">
        {totalSteps > 0 && (
          <div className="h-1.5 w-full bg-slate-800 rounded-full overflow-hidden flex">
            {counts.COVERED > 0 && (
              <div
                style={{ width: `${(counts.COVERED / totalSteps) * 100}%` }}
                className="bg-emerald-500 h-full"
                title={`Proven: ${counts.COVERED}`}
              />
            )}
            {counts.BD_MISSING > 0 && (
              <div
                style={{ width: `${(counts.BD_MISSING / totalSteps) * 100}%` }}
                className="bg-amber-500 h-full"
                title={`Proven, not in BD: ${counts.BD_MISSING}`}
              />
            )}
            {counts.CONTRADICTED > 0 && (
              <div
                style={{ width: `${(counts.CONTRADICTED / totalSteps) * 100}%` }}
                className="bg-rose-500 h-full"
                title={`Conflict: ${counts.CONTRADICTED}`}
              />
            )}
            {counts.UNVERIFIABLE > 0 && (
              <div
                style={{ width: `${(counts.UNVERIFIABLE / totalSteps) * 100}%` }}
                className="bg-slate-600 h-full"
                title={`No proof: ${counts.UNVERIFIABLE}`}
              />
            )}
            {counts.OUT_OF_SCOPE > 0 && (
              <div
                style={{ width: `${(counts.OUT_OF_SCOPE / totalSteps) * 100}%` }}
                className="bg-slate-700 h-full border-l border-slate-900"
                title={`Excluded: ${counts.OUT_OF_SCOPE}`}
              />
            )}
          </div>
        )}

        <div className="flex items-center justify-between text-[10px]">
          {reconciliationCaption}
          <span className="text-slate-500 font-mono shrink-0 ml-1">
            {act?.step_count || totalSteps}s
          </span>
        </div>
      </div>
    </div>
  )
}

function UserFlowStepNode({ data }: NodeProps): React.ReactElement {
  const step = data.step as UserFlowStepReport | undefined
  const name = (data.name as string) || step?.text_en || step?.text_ja || 'Step'
  const verdict = (data.verdict as string) || step?.verdict || 'UNVERIFIABLE'
  const isOutOfScope = !step?.in_scope || verdict === 'OUT_OF_SCOPE'
  const meta = VERDICT_META[verdict] || VERDICT_META.UNVERIFIABLE
  const citationsCount = step?.citations?.length || 0

  const hoverTooltip = isOutOfScope
    ? step?.scope_note
      ? `Excluded: ${step.scope_note}`
      : 'Excluded from PoC scope by customer'
    : step?.reason || meta.description

  return (
    <div
      className={`relative px-3 py-2 rounded-xl text-xs shadow-lg backdrop-blur-md w-[240px] min-h-[96px] bg-slate-900 border transition-all duration-150 hover:border-indigo-400 hover:ring-2 hover:ring-indigo-500/30 flex flex-col justify-between ${
        isOutOfScope
          ? 'border-dashed border-slate-700/80 bg-slate-900/40 opacity-80'
          : meta.nodeBorder
      }`}
      title={hoverTooltip}
    >
      <Handle type="target" position={Position.Top} className="!bg-slate-400 !w-2 !h-2 !border-0" />
      <Handle type="source" position={Position.Bottom} className="!bg-slate-400 !w-2 !h-2 !border-0" />

      {/* Row 1: Step # + Tier-2 Filled Badge */}
      <div className="flex items-center justify-between gap-1 mb-1">
        <span className="font-mono text-[11px] font-bold text-slate-400">
          S{step?.ordinal ?? (data.stepOrdinal as number | undefined) ?? 1}
        </span>

        {isOutOfScope ? (
          <span className="px-2 py-0.5 rounded-md border border-dashed border-slate-700 bg-slate-900/60 text-slate-400 font-bold text-[10px] flex items-center gap-1">
            <Ban className="w-2.5 h-2.5 text-slate-500" />
            <span>Excluded</span>
          </span>
        ) : (
          <span
            className={`px-2 py-0.5 rounded-md border font-bold text-[10px] flex items-center gap-1 ${meta.bg} ${meta.color} ${meta.border}`}
          >
            <span>{meta.icon}</span>
            <span>{meta.label}</span>
          </span>
        )}
      </div>

      {/* Row 2: Step text */}
      <div className="font-semibold text-slate-100 text-xs leading-tight line-clamp-2 my-0.5">
        {name}
      </div>

      {/* Row 3: Citation glyph + why snippet */}
      <div className="mt-auto pt-1 border-t border-slate-800/60 flex items-center justify-between text-[10px]">
        {citationsCount > 0 ? (
          <span className="text-indigo-300 font-mono flex items-center gap-1">
            <FileCode2 className="w-3 h-3 text-indigo-400" />
            <span>×{citationsCount}</span>
          </span>
        ) : (
          <span className="text-slate-500 font-mono">0 citations</span>
        )}

        <span className="text-slate-400 truncate max-w-[150px] font-sans">
          {isOutOfScope ? step?.scope_note || 'PoC対象外' : step?.reason || meta.description}
        </span>
      </div>
    </div>
  )
}

const ufNodeTypes = {
  ...nodeTypes,
  ufActivity: UserFlowActivityNode,
  ufStep: UserFlowStepNode
}

// ---------------------------------------------------------------------------
// 3. Top-Level Helper Components (Task E, Legend Popover, etc.)
// ---------------------------------------------------------------------------

function OriginalJapaneseToggle({
  textJa,
  triggerJa,
  expectedJa,
  screenNameJa
}: {
  textJa?: string | null
  triggerJa?: string | null
  expectedJa?: string | null
  screenNameJa?: string | null
}): React.ReactElement | null {
  const [open, setOpen] = useState(false)
  if (!textJa && !triggerJa && !expectedJa && !screenNameJa) return null

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
        </div>
      )}
    </div>
  )
}

function CodeCitationSnippet({ citation }: { citation: UserFlowCitation }): React.ReactElement {
  const [open, setOpen] = useState(true)
  const isValid = citation.valid !== false

  return (
    <div className={`rounded-lg border overflow-hidden text-xs ${isValid ? 'border-slate-800 bg-slate-950/90' : 'border-amber-900/50 bg-amber-950/20'}`}>
      <button
        onClick={() => setOpen(!open)}
        className={`w-full flex items-center justify-between px-3 py-2 text-left transition-colors font-mono text-[11px] ${isValid ? 'bg-slate-900/80 hover:bg-slate-900' : 'bg-amber-950/40 hover:bg-amber-950/60'}`}
      >
        <div className="flex items-center gap-1.5 truncate">
          <FileCode2 className={`w-3.5 h-3.5 shrink-0 ${isValid ? 'text-indigo-400' : 'text-amber-400'}`} />
          <span className={`font-semibold ${isValid ? 'text-indigo-300' : 'text-amber-300 line-through'}`}>
            {citation.rel_path}
          </span>
          <span className="text-slate-400">
            :{citation.line_start}-{citation.line_end}
          </span>
          {!isValid && (
            <span className="ml-1 text-[9px] font-sans font-bold px-1.5 py-0.2 rounded bg-amber-950 text-amber-300 border border-amber-500/40">
              rejected citation
            </span>
          )}
        </div>
        {open ? <ChevronDown className="w-3.5 h-3.5 text-slate-400" /> : <ChevronRight className="w-3.5 h-3.5 text-slate-400" />}
      </button>

      {open && (
        <div className="p-3 bg-slate-950 font-mono text-[11px] text-slate-300 overflow-x-auto whitespace-pre leading-relaxed border-t border-slate-900 space-y-2">
          {citation.fetched_text ? (
            <code>{citation.fetched_text}</code>
          ) : (
            <span className="text-slate-500 italic">No citation preview text available.</span>
          )}
          {citation.reason && (
            <div className="text-[10px] text-slate-400 font-sans border-t border-slate-900 pt-1">
              Note: {citation.reason}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function StepEvidenceDrawer({
  step,
  parentActivity,
  onClose
}: {
  step: UserFlowStepReport | null
  parentActivity?: UserFlowActivityReport | null
  onClose: () => void
}): React.ReactElement | null {
  if (!step) return null
  const isOutOfScope = !step.in_scope || step.verdict === 'OUT_OF_SCOPE'
  const meta = VERDICT_META[step.verdict] || VERDICT_META.UNVERIFIABLE

  return (
    <div className="w-96 shrink-0 bg-slate-900 border-l border-slate-800 flex flex-col h-full overflow-hidden shadow-2xl z-20">
      {/* Header */}
      <div className="p-4 border-b border-slate-800 flex items-start justify-between gap-2 bg-slate-900/90">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="text-xs font-mono font-bold text-slate-400">Step #{step.ordinal}</span>
            {isOutOfScope ? (
              <span className="text-[11px] font-bold px-2 py-0.5 rounded-full border border-dashed border-slate-700 bg-slate-900 text-slate-400 flex items-center gap-1">
                <Ban className="w-3 h-3 text-slate-500" />
                <span>Excluded</span>
              </span>
            ) : (
              <span className={`text-[11px] font-bold px-2 py-0.5 rounded-full border ${meta.bg} ${meta.color} ${meta.border}`}>
                {meta.icon} {meta.label}
              </span>
            )}
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
        {/* Q1 Excluded Customer Scope Block */}
        {!step.in_scope && (
          <div className="p-3 rounded-lg bg-slate-950 border border-dashed border-slate-700 space-y-1">
            <div className="flex items-center gap-1.5 text-xs font-bold text-slate-300">
              <Ban className="w-3.5 h-3.5 text-slate-400" />
              <span>⊘ Excluded by the customer</span>
            </div>
            <div className="text-xs text-slate-300 font-sans">
              &ldquo;{step.scope_note || 'PoC対象外'}&rdquo;
            </div>
            <div className="text-[10px] text-slate-500">
              This is the customer&apos;s own PoC対象外 marker, not an analysis result.
            </div>
          </div>
        )}

        {/* Q2 Tier-1 BD Spec Context Block */}
        {parentActivity && (
          <div className="p-3 rounded-lg bg-indigo-950/20 border border-indigo-500/30 space-y-1.5">
            <span className="text-[10px] font-bold text-indigo-400 uppercase tracking-wider flex items-center gap-1">
              <Layers className="w-3 h-3 text-indigo-400" />
              <span>Tier-1 BD Spec Context</span>
            </span>
            <div className="flex items-center justify-between gap-1">
              <span className="text-xs font-semibold text-slate-200">
                Activity: {parentActivity.name_en}
              </span>
              <span
                className={`text-[10px] font-bold px-1.5 py-0.5 rounded-full border ${MATCH_STATUS_META[parentActivity.match_status]?.bg} ${MATCH_STATUS_META[parentActivity.match_status]?.color} ${MATCH_STATUS_META[parentActivity.match_status]?.border}`}
              >
                {MATCH_STATUS_META[parentActivity.match_status]?.label}
              </span>
            </div>
            {parentActivity.matched_bd_flows && parentActivity.matched_bd_flows.length > 0 && (
              <div className="text-[11px] text-slate-400">
                Matched BD Flow: <span className="text-slate-200">{parentActivity.matched_bd_flows.map((f) => f.name).join(', ')}</span>
              </div>
            )}
            {parentActivity.reason && (
              <div className="text-[10px] text-slate-400/90 leading-tight">
                {parentActivity.reason}
              </div>
            )}
          </div>
        )}

        {/* Verification Status Banner */}
        {step.corrected && (
          <div className="p-2.5 rounded-lg bg-amber-950/40 border border-amber-500/40 flex items-start gap-2">
            <Sparkles className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
            <div className="text-xs text-amber-200">
              <span className="font-bold">Corrected by Verifier:</span> Upstream mapper hypothesis was updated after inspecting code evidence.
            </div>
          </div>
        )}

        {/* Q3: Why this code-proof verdict */}
        <div className="space-y-1">
          <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wide">Why this code-proof verdict</span>
          <div className="p-3 rounded-lg bg-slate-950/70 border border-slate-800 text-xs text-slate-300 leading-relaxed space-y-1.5">
            <div>{step.reason || meta.description}</div>
            {step.divergence && (
              <div className="text-[11px] text-amber-400 font-mono">
                Divergence Axis: {step.divergence}
              </div>
            )}
            {step.basis && (
              <div className="text-[11px] text-indigo-300 font-mono">
                Basis: <span className="text-slate-200">{translateBasis(step.basis)}</span>
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
                const isNonCoverage = m.relation === 'related' || m.coverage_bearing === false

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
                      <span>
                        {typeof m.confidence === 'number'
                          ? `Confidence: ${Math.round(m.confidence * 100)}%`
                          : 'Added by verifier'}
                      </span>
                    </div>
                    {isNonCoverage && (
                      <div className="text-[10px] text-slate-400 bg-slate-900 px-2 py-0.5 rounded border border-slate-800">
                        adjacency only — cannot carry coverage
                      </div>
                    )}
                    {m.guard_class_flag && (
                      <div className="text-[10px] text-amber-400/90 flex items-center gap-1">
                        <span>⚠</span>
                        <span>Guard class not corroborated — review this branch mapping</span>
                      </div>
                    )}
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
        <div className="flex items-center gap-2 text-xs font-bold text-indigo-300">
          <Layers className="w-3.5 h-3.5 text-indigo-400" />
          <span>BD Extra Units ({bdExtraList.length})</span>
          <span className="text-slate-400 font-normal">
            — The BD describes this, no user case asks for it
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
                className="p-3 rounded-lg bg-slate-900 border border-indigo-900/40 space-y-1.5"
              >
                <div className="flex items-start justify-between gap-1">
                  <span className="font-semibold text-xs text-slate-200 truncate">{item.name}</span>
                  <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-indigo-950 text-indigo-300 border border-indigo-500/30">
                    + BD_EXTRA
                  </span>
                </div>
                {item.functionality && (
                  <div className="text-xs text-slate-400 line-clamp-2">{item.functionality}</div>
                )}
                {item.reason && (
                  <div className="text-[10px] text-slate-500">{item.reason}</div>
                )}
                <div className="text-[10px] text-slate-500 font-mono flex items-center justify-between pt-1">
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

function BdUnmappedSection({
  bdUnmappedList,
  isOpen,
  onToggle
}: {
  bdUnmappedList: UserFlowBdExtraItem[]
  isOpen: boolean
  onToggle: () => void
}): React.ReactElement {
  return (
    <div className="border-t border-slate-800 bg-slate-950">
      <button
        onClick={onToggle}
        className="w-full px-4 py-2.5 flex items-center justify-between text-left hover:bg-slate-900 transition-colors"
      >
        <div className="flex items-center gap-2 text-xs font-bold text-indigo-300">
          <Layers className="w-3.5 h-3.5 text-indigo-400" />
          <span>BD Unmapped Units ({bdUnmappedList.length})</span>
          <span className="text-slate-400 font-normal">
            — The BD flow was matched, but no user step reached this unit (recall artifact — verify before reporting as a gap)
          </span>
        </div>
        {isOpen ? <ChevronDown className="w-4 h-4 text-slate-400" /> : <ChevronRight className="w-4 h-4 text-slate-400" />}
      </button>

      {isOpen && (
        <div className="p-4 pt-0 max-h-64 overflow-y-auto grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {bdUnmappedList.length === 0 ? (
            <div className="col-span-full py-4 text-center text-xs text-slate-500 italic">
              No unmapped BD units in matched flows.
            </div>
          ) : (
            bdUnmappedList.map((item: UserFlowBdExtraItem) => (
              <div
                key={item.bd_id}
                className="p-3 rounded-lg bg-slate-900 border border-indigo-900/40 space-y-1.5"
              >
                <div className="flex items-start justify-between gap-1">
                  <span className="font-semibold text-xs text-slate-200 truncate">{item.name}</span>
                  <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-indigo-950 text-indigo-300 border border-dashed border-indigo-500/40">
                    ○ BD_UNMAPPED
                  </span>
                </div>
                {item.flow_name && (
                  <div className="text-[11px] text-indigo-400 font-medium truncate">
                    Flow: {item.flow_name}
                  </div>
                )}
                {item.functionality && (
                  <div className="text-xs text-slate-400 line-clamp-2">{item.functionality}</div>
                )}
                {item.reason && (
                  <div className="text-[10px] text-slate-500">{item.reason}</div>
                )}
                <div className="text-[10px] text-slate-500 font-mono flex items-center justify-between pt-1">
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

function BranchCoverageSection({
  branchCoverage,
  isOpen,
  onToggle
}: {
  branchCoverage: UserFlowBranchCoverageItem[]
  isOpen: boolean
  onToggle: () => void
}): React.ReactElement {
  const exercised = branchCoverage.filter((b) => b.incoming_step_count > 0)
  const gaps = branchCoverage.filter((b) => b.incoming_step_count === 0 && b.is_user_flow_gap)
  const notAssessed = branchCoverage.filter((b) => b.incoming_step_count === 0 && !b.is_user_flow_gap)

  // Sort: gaps -> not assessed -> exercised
  const sorted = [...branchCoverage].sort((a, b) => {
    const rank = (item: UserFlowBranchCoverageItem) => {
      if (item.incoming_step_count === 0 && item.is_user_flow_gap) return 0
      if (item.incoming_step_count === 0) return 1
      return 2
    }
    return rank(a) - rank(b) || b.incoming_step_count - a.incoming_step_count
  })

  return (
    <div className="border-t border-slate-800 bg-slate-950">
      <button
        onClick={onToggle}
        className="w-full px-4 py-2.5 flex items-center justify-between text-left hover:bg-slate-900 transition-colors"
      >
        <div className="flex items-center gap-2 text-xs font-bold text-slate-200">
          <span className="w-2 h-2 rounded-full bg-indigo-500"></span>
          <span>Branch Coverage ({branchCoverage.length})</span>
          <span className="text-slate-400 font-normal">
            — {exercised.length} exercised · {gaps.length} gaps · {notAssessed.length} not assessed
          </span>
        </div>
        {isOpen ? <ChevronDown className="w-4 h-4 text-slate-400" /> : <ChevronRight className="w-4 h-4 text-slate-400" />}
      </button>

      {isOpen && (
        <div className="p-4 pt-0 max-h-64 overflow-y-auto grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {sorted.map((b) => {
            const hasIncoming = b.incoming_step_count > 0
            const isGap = !hasIncoming && b.is_user_flow_gap
            const isUndetermined = !hasIncoming && b.parent_tier1_status === 'UNRESOLVED'

            const incomingTooltip =
              b.incoming_steps && b.incoming_steps.length > 0
                ? b.incoming_steps
                    .map((st) => `Flow: ${st.flow_name} · S${st.ordinal}: ${st.text_en}`)
                    .join('\n')
                : `Fan-in: ${b.incoming_step_count} cases`

            return (
              <div
                key={b.branch_id}
                className={`p-3 rounded-lg bg-slate-900 border space-y-1.5 ${
                  isGap
                    ? 'border-rose-800/60 bg-rose-950/20'
                    : isUndetermined
                    ? 'border-amber-800/60 bg-amber-950/20'
                    : hasIncoming
                    ? 'border-emerald-800/40 bg-emerald-950/10'
                    : 'border-dashed border-slate-800 bg-slate-900/40'
                }`}
              >
                <div className="flex items-start justify-between gap-1">
                  <span className="font-semibold text-xs text-slate-200 truncate">
                    {b.branch_kind} · {b.flow_name}
                  </span>

                  {hasIncoming ? (
                    <span
                      className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-emerald-950 text-emerald-300 border border-emerald-500/30 shrink-0"
                      title={incomingTooltip}
                    >
                      ✓ exercised by {b.incoming_step_count} user case{b.incoming_step_count === 1 ? '' : 's'}
                    </span>
                  ) : isGap ? (
                    <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-rose-950 text-rose-300 border border-rose-500/30 shrink-0">
                      ✕ USER-FLOW GAP · no incoming
                    </span>
                  ) : isUndetermined ? (
                    <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-amber-950/60 text-amber-300 border border-amber-500/40 shrink-0">
                      ? tier-1 undetermined
                    </span>
                  ) : (
                    <span className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-slate-900 text-slate-400 border border-dashed border-slate-700 shrink-0">
                      — not assessed
                    </span>
                  )}
                </div>

                {b.guard_description && (
                  <div className="text-xs text-slate-400 line-clamp-2">{b.guard_description}</div>
                )}

                <div className="text-[10px] text-slate-500 font-mono flex items-center justify-between">
                  <span>Parent Tier-1: {b.parent_tier1_status || 'UNKNOWN'}</span>
                  {b.bd_verdict && <span>BD↔Code: {b.bd_verdict}</span>}
                </div>

                {isGap && (
                  <div className="text-[10px] text-rose-400/90 leading-tight">
                    No user case exercises this guard although its parent BD flow is described.
                  </div>
                )}
                {!hasIncoming && !isGap && !isUndetermined && (
                  <div className="text-[10px] text-slate-500 leading-tight">
                    Parent BD flow was not matched, guard never in scope.
                  </div>
                )}
                {isUndetermined && (
                  <div className="text-[10px] text-amber-400/80 leading-tight">
                    Parent BD flow matching is unresolved.
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

/** Level 1: Flow Overview Card (7-Row Layout, Task B) */
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
  const isOutOfScope =
    matchVerdict === 'OUT_OF_SCOPE' ||
    ((flowReport?.counts?.OUT_OF_SCOPE ?? 0) === (flowReport?.steps?.length ?? 0) &&
      (flowReport?.steps?.length ?? 0) > 0)
  const meta = VERDICT_META[matchVerdict] || VERDICT_META.OUT_OF_SCOPE
  const nameEn = flowReport?.name_en || businessFlow.name
  const nameJa = flowReport?.name_ja
  const stepCount = flowReport?.steps?.length ?? businessFlow.steps?.length ?? 0
  const actCount = flowReport?.activities?.length ?? 0
  const describedActCount =
    flowReport?.activities?.filter((a) => a.match_status === 'FULLY' || a.match_status === 'PARTIAL').length ?? 0
  const coveredCount = flowReport?.counts?.COVERED ?? 0
  const oosStepsCount = flowReport?.counts?.OUT_OF_SCOPE ?? 0
  const inScopeCount = flowReport ? flowReport.steps.filter((s) => s.in_scope).length : stepCount
  const hasExcludedSteps = oosStepsCount > 0

  // Scope quote
  const scopeQuote =
    flowReport?.scope_note ||
    flowReport?.steps?.find((s) => s.scope_note)?.scope_note ||
    (oosStepsCount > 0 ? `${oosStepsCount} steps excluded: PoC対象外` : '')

  // Row 4 Primary Status Strip reason/why
  let primaryReason = ''
  if (isOutOfScope) {
    primaryReason = scopeQuote || 'Excluded from PoC scope by customer'
  } else if (flowReport?.reason) {
    primaryReason = flowReport.reason
  } else if (flowReport) {
    primaryReason =
      inScopeCount > 0 ? `${coveredCount} of ${inScopeCount} in-scope steps proven by code` : 'No in-scope steps'
  }

  // Reconciliation line: BD describes activities, but no code citation proves them yet
  const showReconciliationLine = describedActCount > 0 && coveredCount === 0 && inScopeCount > 0

  return (
    <div
      onClick={onSelect}
      className={`group relative rounded-xl border ${
        isOutOfScope
          ? 'border-dashed border-slate-700/80 bg-slate-900/40 opacity-85'
          : `${meta.border} ${meta.nodeBg}`
      } p-4 hover:border-indigo-400 hover:shadow-xl hover:shadow-indigo-950/30 transition-all cursor-pointer flex flex-col justify-between gap-3`}
    >
      <div className="space-y-2">
        {/* Row 1: Ordinal · Sheet · Scope pill */}
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-1.5 font-mono text-xs text-slate-400 min-w-0">
            <span className="px-1.5 py-0.5 rounded bg-slate-900 border border-slate-800 font-bold text-slate-300 shrink-0">
              #{businessFlow.ordinal || flowReport?.ordinal || 1}
            </span>
            <span className="text-[11px] text-slate-400 truncate" title={businessFlow.block_key || flowReport?.sheet}>
              {businessFlow.block_key || flowReport?.sheet || 'Flow'}
            </span>
          </div>

          {hasExcludedSteps && (
            <span
              className="px-2 py-0.5 rounded-full border border-dashed border-slate-700 bg-slate-900/60 text-slate-400 text-[10px] font-medium flex items-center gap-1 shrink-0"
              title={scopeQuote || 'Some steps excluded from PoC scope'}
            >
              <Ban className="w-2.5 h-2.5 text-slate-500" />
              <span>Excluded</span>
            </span>
          )}
        </div>

        {/* Row 2 & 3: Names */}
        <div>
          <h4 className="font-semibold text-sm text-slate-100 group-hover:text-indigo-300 transition-colors leading-snug line-clamp-2">
            {nameEn || nameJa || `Flow #${businessFlow.ordinal}`}
          </h4>
          {nameJa && nameEn && nameJa !== nameEn && (
            <p className="text-[11px] text-slate-400 line-clamp-1 mt-0.5 font-sans">{nameJa}</p>
          )}
        </div>

        {/* Row 4: Primary Status Strip (Icon + Label — WHY) */}
        <div
          className={`p-2 rounded-lg border text-xs flex items-center gap-1.5 ${meta.bg} ${meta.color} ${meta.border}`}
          title={primaryReason}
        >
          <span className="font-bold shrink-0">
            {meta.icon} {meta.label}
          </span>
          <span className="text-slate-400 font-normal shrink-0">—</span>
          <span className="truncate text-slate-300 font-normal">{primaryReason}</span>
        </div>
      </div>

      <div className="space-y-2 pt-2 border-t border-slate-800/80">
        {/* Row 5: Two Labelled Stats (BD SPEC vs CODE PROOF) */}
        <div className="grid grid-cols-2 gap-2 text-xs">
          {/* Left: BD SPEC */}
          <div className="p-2 rounded-lg border border-indigo-500/30 bg-indigo-950/20 flex flex-col justify-between">
            <span className="text-[10px] font-bold text-indigo-400 uppercase tracking-wider flex items-center gap-1">
              <Layers className="w-2.5 h-2.5" />
              <span>BD SPEC</span>
            </span>
            <span
              className="text-xs font-semibold text-slate-200 mt-1 truncate"
              title={`${describedActCount} of ${actCount} activities described`}
            >
              {actCount > 0 ? `${describedActCount}/${actCount} activities described` : 'no activities parsed'}
            </span>
          </div>

          {/* Right: CODE PROOF */}
          <div className="p-2 rounded-lg border border-emerald-500/30 bg-emerald-950/20 flex flex-col justify-between">
            <span className="text-[10px] font-bold text-emerald-400 uppercase tracking-wider flex items-center gap-1">
              <FileCode2 className="w-2.5 h-2.5" />
              <span>CODE PROOF</span>
            </span>
            <span
              className="text-xs font-semibold text-slate-200 mt-1 truncate"
              title={`${coveredCount} of ${inScopeCount} in-scope steps proven`}
            >
              {inScopeCount > 0 ? `${coveredCount}/${inScopeCount} steps proven` : 'no in-scope steps'}
            </span>
          </div>
        </div>

        {/* Reconciliation banner when SPEC > 0 && COVERED == 0 */}
        {showReconciliationLine && (
          <div className="p-1.5 rounded-md bg-amber-950/40 border border-amber-500/40 text-[10px] text-amber-300 flex items-start gap-1 leading-snug">
            <AlertTriangle className="w-3 h-3 text-amber-400 shrink-0 mt-0.5" />
            <span>The BD describes these activities, but no accepted code citation proves them yet.</span>
          </div>
        )}

        {/* Row 6: Exception chips only when count > 0 */}
        {flowReport &&
          (flowReport.counts.CONTRADICTED > 0 ||
            flowReport.counts.BD_MISSING > 0 ||
            flowReport.counts.UNVERIFIABLE > 0 ||
            flowReport.counts.OUT_OF_SCOPE > 0) && (
            <div className="flex flex-wrap items-center gap-1.5 pt-1">
              {flowReport.counts.CONTRADICTED > 0 && (
                <span className="px-1.5 py-0.5 rounded bg-rose-950/80 text-rose-300 border border-rose-500/30 text-[10px] font-medium">
                  ✕ {flowReport.counts.CONTRADICTED} conflict
                </span>
              )}
              {flowReport.counts.BD_MISSING > 0 && (
                <span className="px-1.5 py-0.5 rounded bg-amber-950/80 text-amber-300 border border-amber-500/30 text-[10px] font-medium">
                  ! {flowReport.counts.BD_MISSING} not in BD
                </span>
              )}
              {flowReport.counts.UNVERIFIABLE > 0 && (
                <span className="px-1.5 py-0.5 rounded bg-slate-800 text-slate-300 border border-slate-700 text-[10px] font-medium">
                  ? {flowReport.counts.UNVERIFIABLE} no proof
                </span>
              )}
              {flowReport.counts.OUT_OF_SCOPE > 0 && (
                <span className="px-1.5 py-0.5 rounded bg-slate-900 text-slate-400 border border-dashed border-slate-700 text-[10px] font-medium">
                  ⊘ {flowReport.counts.OUT_OF_SCOPE} excluded
                </span>
              )}
            </div>
          )}

        {/* Row 7: Drill In CTA */}
        <div className="flex items-center justify-end pt-1">
          <span className="text-xs font-semibold text-indigo-400 group-hover:translate-x-0.5 transition-transform flex items-center gap-1">
            <span>Drill in</span>
            <ArrowRight className="w-3.5 h-3.5" />
          </span>
        </div>
      </div>
    </div>
  )
}

/** Breadcrumb navigation bar for Activity Drilldown */
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

/** How to Read Popover Modal (Task H) */
function HowToReadModal({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }): React.ReactElement | null {
  if (!isOpen) return null

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4">
      <div className="bg-slate-900 border border-slate-800 rounded-2xl max-w-2xl w-full p-6 shadow-2xl space-y-5 text-slate-200">
        <div className="flex items-center justify-between border-b border-slate-800 pb-3">
          <div className="flex items-center gap-2">
            <HelpCircle className="w-5 h-5 text-indigo-400" />
            <h3 className="text-base font-bold text-slate-100">How to Read User Flow Reconciliation</h3>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-200 p-1">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="space-y-4 text-xs leading-relaxed">
          <p className="text-slate-300">
            Every element answers one of <span className="font-bold text-slate-100">Three Distinct Questions</span>, each with its own visual channel and vocabulary:
          </p>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {/* Q1 */}
            <div className="p-3 rounded-xl border border-dashed border-slate-700 bg-slate-950/60 space-y-1.5">
              <div className="flex items-center gap-1.5 font-bold text-slate-300">
                <Ban className="w-3.5 h-3.5 text-slate-400" />
                <span>① SCOPE</span>
              </div>
              <p className="text-[11px] text-slate-400 font-medium">Does customer want this tested?</p>
              <div className="pt-1 text-[10px] text-slate-500">
                Dashed slate pills indicate customer&apos;s own PoC対象外 scope exclusion.
              </div>
            </div>

            {/* Q2 */}
            <div className="p-3 rounded-xl border border-indigo-500/40 bg-indigo-950/30 space-y-1.5">
              <div className="flex items-center gap-1.5 font-bold text-indigo-300">
                <Layers className="w-3.5 h-3.5 text-indigo-400" />
                <span>② BD SPEC</span>
              </div>
              <p className="text-[11px] text-indigo-200/90 font-medium">Does the BD describe it?</p>
              <div className="pt-1 text-[10px] text-slate-400 leading-snug">
                Indigo outline pills. Judged by LLM from activity text vs BD flows — no code involved.
              </div>
            </div>

            {/* Q3 */}
            <div className="p-3 rounded-xl border border-emerald-500/40 bg-emerald-950/30 space-y-1.5">
              <div className="flex items-center gap-1.5 font-bold text-emerald-300">
                <FileCode2 className="w-3.5 h-3.5 text-emerald-400" />
                <span>③ CODE PROOF</span>
              </div>
              <p className="text-[11px] text-emerald-200/90 font-medium">Does the code prove it?</p>
              <div className="pt-1 text-[10px] text-slate-400 leading-snug">
                Filled colored badges (green/amber/red). Requires accepted code citations — this colours the graph.
              </div>
            </div>
          </div>

          <div className="p-3.5 rounded-xl bg-indigo-950/40 border border-indigo-500/40 space-y-1 text-xs text-indigo-200 leading-relaxed">
            <span className="font-bold block text-indigo-100">💡 Understanding Gray / Unproven Nodes:</span>
            <p>
              Gray node? It only means ③ found no proof in source code. Read the ▢ pill inside the node: if the BD describes it, the spec is fine and the evidence is missing — not the same as &ldquo;the BD is wrong&rdquo;.
            </p>
          </div>
        </div>

        <div className="flex justify-end pt-2">
          <button
            onClick={onClose}
            className="px-4 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold"
          >
            Got it
          </button>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 4. Main UserFlowReportScreen Component
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
  const [selectedGraphFlowId, setSelectedGraphFlowId] = useState<string | null>(null)
  const [selectedActivityId, setSelectedActivityId] = useState<string | null>(null)
  const [selectedStep, setSelectedStep] = useState<UserFlowStepReport | null>(null)
  const [showBdExtra, setShowBdExtra] = useState<boolean>(false)
  const [showBdUnmapped, setShowBdUnmapped] = useState<boolean>(false)
  const [showBranchCoverage, setShowBranchCoverage] = useState<boolean>(false)
  const [viewMode, setViewMode] = useState<'graph' | 'tree'>('graph')
  const [searchQuery, setSearchQuery] = useState<string>('')
  const [hideExcluded, setHideExcluded] = useState<boolean>(false)
  const [showHowToRead, setShowHowToRead] = useState<boolean>(false)

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

  // Step's parent activity lookup
  const stepParentActivityMap = useMemo(() => {
    const map: Record<string, UserFlowActivityReport> = {}
    if (report?.flows) {
      for (const flow of report.flows) {
        if (flow.activities) {
          for (const act of flow.activities) {
            for (const sId of act.member_step_ids || []) {
              map[sId] = act
            }
          }
        }
      }
    }
    return map
  }, [report])

  // Client-side aggregate of BD Spec (Tier-1 described activities)
  const specStats = useMemo(() => {
    let totalActivities = 0
    let describedActivities = 0
    if (report?.flows) {
      for (const flow of report.flows) {
        if (flow.activities) {
          for (const act of flow.activities) {
            totalActivities += 1
            if (act.match_status === 'FULLY' || act.match_status === 'PARTIAL') {
              describedActivities += 1
            }
          }
        }
      }
    }
    return { totalActivities, describedActivities }
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

  // Parse Action
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

  // Run Match Action
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

  // Count excluded flows
  const excludedFlowsCount = useMemo(() => {
    return businessFlows.filter((f) => {
      const rep = flowReportMap[f.id]
      return (
        rep?.flow_match === 'OUT_OF_SCOPE' ||
        ((rep?.counts?.OUT_OF_SCOPE ?? 0) === (rep?.steps?.length ?? 0) && (rep?.steps?.length ?? 0) > 0)
      )
    }).length
  }, [businessFlows, flowReportMap])

  // Filter flows by search query & excluded toggle
  const filteredBusinessFlows = useMemo(() => {
    if (!businessFlows) return []
    let list = businessFlows

    if (hideExcluded) {
      list = list.filter((f) => {
        const rep = flowReportMap[f.id]
        const isOos =
          rep?.flow_match === 'OUT_OF_SCOPE' ||
          ((rep?.counts?.OUT_OF_SCOPE ?? 0) === (rep?.steps?.length ?? 0) && (rep?.steps?.length ?? 0) > 0)
        return !isOos
      })
    }

    if (!searchQuery.trim()) return list
    const q = searchQuery.toLowerCase()
    return list.filter((f) => {
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
  }, [businessFlows, flowReportMap, searchQuery, hideExcluded])

  const filteredReportFlows = useMemo(() => {
    if (!report?.flows) return []
    let list = report.flows

    if (hideExcluded) {
      list = list.filter(
        (f) =>
          f.flow_match !== 'OUT_OF_SCOPE' &&
          !((f.counts?.OUT_OF_SCOPE ?? 0) === f.steps.length && f.steps.length > 0)
      )
    }

    if (!searchQuery.trim()) return list
    const q = searchQuery.toLowerCase()
    return list.filter(
      (f) =>
        f.name_en.toLowerCase().includes(q) ||
        f.name_ja.toLowerCase().includes(q) ||
        f.sheet.toLowerCase().includes(q) ||
        f.steps.some((s) => s.text_en.toLowerCase().includes(q) || s.text_ja.toLowerCase().includes(q))
    )
  }, [report, searchQuery, hideExcluded])

  // -------------------------------------------------------------------------
  // 5. Two-Tier Graph Construction (Level 2: Activities vs Level 3: Leaf Steps)
  // -------------------------------------------------------------------------

  const { graphNodes, graphEdges, currentVisibleCount } = useMemo(() => {
    if (!activeDetailFlow) {
      return { graphNodes: [], graphEdges: [], currentVisibleCount: 0 }
    }

    // Level 2: Default Flow View -> Renders Activities
    if (!selectedActivityId) {
      const activitiesList = activeDetailFlowReport?.activities || []

      if (activitiesList.length === 0) {
        const skeleton = projectBusinessFlowSkeleton([activeDetailFlow])
        return {
          graphNodes: skeleton.nodes,
          graphEdges: skeleton.edges,
          currentVisibleCount: activeDetailFlow.steps.length
        }
      }

      const synthSteps: BusinessFlowStep[] = activitiesList.map((act) => ({
        id: `act:${act.id}`,
        flow_id: activeDetailFlow.id,
        name: act.name_en,
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
          return {
            ...n,
            type: 'ufActivity',
            style: { width: 240, height: 96 },
            data: {
              ...n.data,
              activity: act,
              actOrdinal: act?.ordinal,
              name: act?.name_en || n.data.name,
              matchStatus: act?.match_status,
              activityMatch: act?.activity_match,
              counts: act?.counts,
              pairMatches: act?.pair_matches
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
        const isSelected = selectedStep?.id === n.id
        return {
          ...n,
          type: 'ufStep',
          style: { width: 240, height: 96 },
          className: isSelected ? 'ring-2 ring-indigo-400 ring-offset-2 ring-offset-slate-950 rounded-xl' : '',
          data: {
            ...n.data,
            step,
            stepOrdinal: step?.ordinal,
            name: step?.text_en || step?.text_ja || n.data.name,
            verdict: step?.verdict
          }
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

          {report?.run_id && (
            <span className="px-2 py-0.5 rounded bg-slate-800 border border-slate-700 text-[10px] font-mono text-slate-300">
              run {report.run_id.slice(0, 8)} {report.doc ? `· ${report.doc.source_name}` : ''}
            </span>
          )}

          <button
            onClick={() => setShowHowToRead(true)}
            className="px-2 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-xs font-semibold text-indigo-300 border border-slate-700 flex items-center gap-1 transition-colors"
            title="How to read the three-question reconciliation"
          >
            <HelpCircle className="w-3.5 h-3.5 text-indigo-400" />
            <span>How to read</span>
          </button>

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
        <div className="p-3 bg-red-950/80 border-b border-red-800 flex items-center justify-between text-xs text-red-200 shrink-0">
          <div className="flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-red-400 shrink-0" />
            <span>{errorMessage}</span>
          </div>
          <button onClick={() => setErrorMessage(null)} className="text-red-400 hover:text-red-200">
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* 3. Run-State Banner (when run_id is null, Task F) */}
      {!report?.run_id && report && (
        <div className="p-3 bg-amber-950/60 border-b border-amber-800/80 flex items-center justify-between text-xs text-amber-200 shrink-0">
          <div className="flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0" />
            <span>
              No completed match run. The statuses below are defaults, not findings. Click &ldquo;Run Match&rdquo; to evaluate.
            </span>
          </div>
        </div>
      )}

      {/* 4. Three-Question Grouped KPI Header Tiles (Task F - Only when run_id exists) */}
      {report?.summary && report.run_id && (
        <div className="px-4 py-2.5 bg-slate-900/70 border-b border-slate-800 flex flex-wrap items-center justify-between gap-3 text-xs shrink-0">
          {/* Q1: SCOPE */}
          <div
            className="flex items-center gap-1.5 p-1 px-2 rounded-lg bg-slate-950/60 border border-slate-800"
            title="Q1 SCOPE: Does the customer want this tested?"
          >
            <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider flex items-center gap-1">
              <Ban className="w-3 h-3 text-slate-500" />
              <span>Scope:</span>
            </span>
            <span className="text-slate-200 font-semibold font-mono">
              {report.summary.in_scope_steps}/{report.summary.total_steps} in-scope steps
            </span>
            {report.summary.flow_counts.OUT_OF_SCOPE > 0 && (
              <span className="px-1.5 py-0.2 rounded bg-slate-900 border border-dashed border-slate-700 text-slate-400 text-[10px]">
                {report.summary.flow_counts.OUT_OF_SCOPE} excluded flows
              </span>
            )}
          </div>

          {/* Q2: BD SPEC */}
          <div
            className="flex items-center gap-1.5 p-1 px-2 rounded-lg bg-indigo-950/30 border border-indigo-500/30"
            title="Q2 BD SPEC: Does the BD describe it?"
          >
            <span className="text-[10px] font-bold text-indigo-400 uppercase tracking-wider flex items-center gap-1">
              <Layers className="w-3 h-3 text-indigo-400" />
              <span>BD Spec:</span>
            </span>
            <span className="text-indigo-200 font-semibold font-mono">
              {specStats.describedActivities}/{specStats.totalActivities || report.summary.total_activities || 0} activities described
            </span>
          </div>

          {/* Q3: CODE PROOF */}
          <div
            className="flex items-center gap-1.5 p-1 px-2 rounded-lg bg-emerald-950/30 border border-emerald-500/30"
            title="Q3 CODE PROOF: Does the code prove it?"
          >
            <span className="text-[10px] font-bold text-emerald-400 uppercase tracking-wider flex items-center gap-1">
              <FileCode2 className="w-3 h-3 text-emerald-400" />
              <span>Code Proof:</span>
            </span>
            <span className="text-emerald-300 font-semibold font-mono">
              {report.summary.step_counts.COVERED}/{report.summary.in_scope_steps} proven
            </span>
            {report.summary.step_counts.BD_MISSING > 0 && (
              <span className="px-1.5 py-0.2 rounded bg-amber-950 text-amber-400 border border-amber-500/30 text-[10px] font-semibold">
                ! {report.summary.step_counts.BD_MISSING} not in BD
              </span>
            )}
            {report.summary.step_counts.CONTRADICTED > 0 && (
              <span className="px-1.5 py-0.2 rounded bg-rose-950 text-rose-400 border border-rose-500/30 text-[10px] font-semibold">
                ✕ {report.summary.step_counts.CONTRADICTED} conflict
              </span>
            )}
            {report.summary.step_counts.UNVERIFIABLE > 0 && (
              <span className="px-1.5 py-0.2 rounded bg-slate-800 text-slate-300 border border-slate-700 text-[10px]">
                ? {report.summary.step_counts.UNVERIFIABLE} no proof
              </span>
            )}
          </div>

          {/* Residuals: BD Extra / Unmapped */}
          <div className="flex items-center gap-2">
            {report.summary.bd_unmapped_count !== undefined && report.summary.bd_unmapped_count > 0 && (
              <span className="px-2 py-0.5 rounded-full bg-indigo-950/60 text-indigo-300 border border-dashed border-indigo-500/40 text-[11px] font-semibold">
                ○ {report.summary.bd_unmapped_count} BD Unmapped
              </span>
            )}
            <span className="px-2 py-0.5 rounded-full bg-indigo-950 text-indigo-300 border border-indigo-500/30 text-[11px] font-semibold">
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

      {/* 5. Main Body: Level 1 Overview vs Level 2 Flow Activities vs Level 3 Leaf Steps */}
      <div className="flex-1 flex min-h-0 relative">
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

                <div className="flex items-center gap-2">
                  {excludedFlowsCount > 0 && (
                    <button
                      onClick={() => setHideExcluded(!hideExcluded)}
                      className={`px-2.5 py-1.5 rounded-lg border text-xs font-medium flex items-center gap-1.5 transition-colors ${
                        hideExcluded
                          ? 'bg-slate-800 text-indigo-300 border-indigo-500/40'
                          : 'bg-slate-900 text-slate-400 border-slate-800 hover:text-slate-200'
                      }`}
                    >
                      <Ban className="w-3.5 h-3.5" />
                      <span>{hideExcluded ? 'Show excluded' : `Hide excluded (${excludedFlowsCount})`}</span>
                    </button>
                  )}

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
              {/* Detail Top Sub-Toolbar (Always Pair: SPEC & PROOF) */}
              <div className="px-4 py-2 bg-slate-900/90 border-b border-slate-800 flex items-center justify-between gap-3 shrink-0 z-10">
                <div className="flex items-center gap-3 min-w-0">
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

                {/* Sub-toolbar dual pair: [▢ SPEC ...][● PROOF ...] */}
                <div className="flex items-center gap-2 shrink-0">
                  {activeActivity ? (
                    <>
                      {/* SPEC */}
                      <span
                        className={`text-[10px] font-bold px-2 py-0.5 rounded-full border flex items-center gap-1 ${
                          MATCH_STATUS_META[activeActivity.match_status]?.bg || 'bg-slate-900'
                        } ${MATCH_STATUS_META[activeActivity.match_status]?.color || 'text-slate-300'} ${
                          MATCH_STATUS_META[activeActivity.match_status]?.border || 'border-slate-700'
                        }`}
                      >
                        <Layers className="w-3 h-3 text-indigo-400" />
                        <span>{MATCH_STATUS_META[activeActivity.match_status]?.label}</span>
                      </span>

                      {/* PROOF */}
                      <span
                        className={`text-[10px] font-bold px-2 py-0.5 rounded-md border flex items-center gap-1 ${
                          VERDICT_META[activeActivity.activity_match]?.bg || 'bg-slate-900'
                        } ${VERDICT_META[activeActivity.activity_match]?.color || 'text-slate-300'} ${
                          VERDICT_META[activeActivity.activity_match]?.border || 'border-slate-700'
                        }`}
                      >
                        <span>{VERDICT_META[activeActivity.activity_match]?.icon}</span>
                        <span>{VERDICT_META[activeActivity.activity_match]?.label}</span>
                      </span>
                    </>
                  ) : activeDetailFlowReport ? (
                    <>
                      {/* Flow-level SPEC side: client aggregate over activities */}
                      <span className="text-[10px] font-bold px-2 py-0.5 rounded-full border border-indigo-500/40 bg-indigo-950/30 text-indigo-300 flex items-center gap-1">
                        <Layers className="w-3 h-3 text-indigo-400" />
                        <span>
                          BD describes ·{' '}
                          {activeDetailFlowReport.activities?.filter((a) => a.match_status === 'FULLY' || a.match_status === 'PARTIAL').length ?? 0}/
                          {activeDetailFlowReport.activities?.length ?? 0} activities
                        </span>
                      </span>

                      {/* Flow-level PROOF side */}
                      <span
                        className={`text-[10px] font-bold px-2 py-0.5 rounded-md border flex items-center gap-1 ${
                          VERDICT_META[activeDetailFlowReport.flow_match]?.bg || 'bg-slate-900'
                        } ${VERDICT_META[activeDetailFlowReport.flow_match]?.color || 'text-slate-300'} ${
                          VERDICT_META[activeDetailFlowReport.flow_match]?.border || 'border-slate-700'
                        }`}
                      >
                        <span>{VERDICT_META[activeDetailFlowReport.flow_match]?.icon}</span>
                        <span>{VERDICT_META[activeDetailFlowReport.flow_match]?.label}</span>
                      </span>
                    </>
                  ) : null}

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
                  nodeTypes={ufNodeTypes}
                  edgeTypes={edgeTypes}
                  onNodeClick={(_evt, node) => {
                    if (node.id.startsWith('act:')) {
                      const actId = node.id.slice(4)
                      setSelectedActivityId(actId)
                      setSelectedStep(null)
                      return
                    }

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

                {/* Map Legend Overlay replaced with Help Modal button */}
                <div className="absolute bottom-4 left-4 bg-slate-900/90 border border-slate-800 p-2.5 rounded-xl text-xs flex items-center gap-2 backdrop-blur-md shadow-xl">
                  <span className="text-[11px] text-slate-400 font-medium">
                    {!selectedActivityId ? 'Activity Level' : 'Leaf Steps Level'}
                  </span>
                  <span className="text-slate-600">·</span>
                  <span className="text-[10px] text-indigo-400 font-mono">
                    {!selectedActivityId ? 'Click activity to drill in' : 'Click step to view proof drawer'}
                  </span>
                </div>
              </div>
            </div>
          )
        ) : (
          /* Tree View */
          <div className="flex-1 h-full overflow-y-auto p-4 space-y-4 bg-slate-950">
            <div className="flex items-center gap-3">
              <div className="relative max-w-md flex-1">
                <Search className="w-4 h-4 absolute left-3 top-2.5 text-slate-500" />
                <input
                  type="text"
                  placeholder="Search flows or steps..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="w-full bg-slate-900 border border-slate-800 rounded-lg pl-9 pr-3 py-1.5 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
                />
              </div>

              {excludedFlowsCount > 0 && (
                <button
                  onClick={() => setHideExcluded(!hideExcluded)}
                  className={`px-2.5 py-1.5 rounded-lg border text-xs font-medium flex items-center gap-1.5 transition-colors ${
                    hideExcluded
                      ? 'bg-slate-800 text-indigo-300 border-indigo-500/40'
                      : 'bg-slate-900 text-slate-400 border-slate-800 hover:text-slate-200'
                  }`}
                >
                  <Ban className="w-3.5 h-3.5" />
                  <span>{hideExcluded ? 'Show excluded' : `Hide excluded (${excludedFlowsCount})`}</span>
                </button>
              )}
            </div>

            <div className="space-y-3">
              {filteredReportFlows.map((flow: UserFlowFlowReport) => {
                const meta = VERDICT_META[flow.flow_match] || VERDICT_META.OUT_OF_SCOPE
                const isOutOfScope =
                  flow.flow_match === 'OUT_OF_SCOPE' ||
                  ((flow.counts?.OUT_OF_SCOPE ?? 0) === flow.steps.length && flow.steps.length > 0)

                return (
                  <div
                    key={flow.id}
                    className={`rounded-xl border ${
                      isOutOfScope
                        ? 'border-dashed border-slate-700/80 bg-slate-950/60'
                        : `${meta.nodeBg} ${meta.nodeBorder}`
                    } overflow-hidden shadow-lg transition-all`}
                  >
                    <div className="p-3.5 flex items-center justify-between">
                      <div className="flex items-center gap-2.5 min-w-0">
                        <span className="text-xs font-mono font-bold text-slate-400">#{flow.ordinal}</span>
                        <div className="min-w-0">
                          <div className="font-semibold text-sm text-slate-100 truncate">
                            {flow.name_en || flow.name_ja}
                          </div>
                          <div className="text-[11px] text-slate-400 font-mono mt-0.5">
                            Sheet: {flow.sheet} · {flow.activities?.length || 0} activities
                          </div>
                        </div>
                      </div>

                      <div className="flex items-center gap-3 shrink-0">
                        <span className="text-xs text-slate-300 font-semibold">
                          {flow.counts.COVERED}/{flow.steps.filter((s) => s.in_scope).length} proven
                        </span>
                        <span
                          className={`text-xs font-bold px-2.5 py-0.5 rounded-full border ${meta.bg} ${meta.color} ${meta.border}`}
                        >
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
                                  <span
                                    className={`text-[10px] font-bold px-2 py-0.5 rounded-full border ${matchMeta.bg} ${matchMeta.color} ${matchMeta.border}`}
                                  >
                                    {matchMeta.label}
                                  </span>
                                </div>
                                <span
                                  className={`text-[10px] font-bold px-2 py-0.5 rounded border ${aMeta.bg} ${aMeta.color} ${aMeta.border}`}
                                >
                                  {aMeta.icon} {aMeta.label} ({act.counts.COVERED}/{act.step_count})
                                </span>
                              </div>

                              {/* Per-(activity, BD flow) pair cells with confidence */}
                              {act.pair_matches && act.pair_matches.length > 0 && (
                                <div className="flex flex-wrap items-center gap-1.5 mb-2 pl-6">
                                  {act.pair_matches.map((pair, pairIx) => {
                                    const pMeta = MATCH_STATUS_META[pair.match_status] || MATCH_STATUS_META.NONE
                                    return (
                                      <span
                                        key={`${act.id}:${pair.bd_flow_id ?? 'none'}:${pairIx}`}
                                        title={pair.reason}
                                        className={`text-[9px] px-1.5 py-0.5 rounded-full border ${pMeta.bg} ${pMeta.color} ${pMeta.border}`}
                                      >
                                        {pair.bd_flow_name || '—'} · {pMeta.label}
                                        {typeof pair.confidence === 'number' && pair.confidence > 0 && (
                                          <span className="ml-1 opacity-80">
                                            ({Math.round(pair.confidence * 100)}%)
                                          </span>
                                        )}
                                      </span>
                                    )
                                  })}
                                </div>
                              )}

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
                                          <span className="text-xs text-slate-300 truncate">
                                            {step.text_en || step.text_ja}
                                          </span>
                                        </div>
                                        <span
                                          className={`text-[9px] font-bold px-1.5 py-0.5 rounded border shrink-0 ${sMeta.bg} ${sMeta.color} ${sMeta.border}`}
                                        >
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
        <StepEvidenceDrawer
          step={selectedStep}
          parentActivity={selectedStep ? stepParentActivityMap[selectedStep.id] : null}
          onClose={() => setSelectedStep(null)}
        />
      </div>

      {/* 6. Branch coverage, BD_UNMAPPED & BD_EXTRA Panels */}
      {report?.branch_coverage && report.branch_coverage.length > 0 && (
        <BranchCoverageSection
          branchCoverage={report.branch_coverage}
          isOpen={showBranchCoverage}
          onToggle={() => setShowBranchCoverage(!showBranchCoverage)}
        />
      )}
      {report?.bd_unmapped && report.bd_unmapped.length > 0 && (
        <BdUnmappedSection
          bdUnmappedList={report.bd_unmapped}
          isOpen={showBdUnmapped}
          onToggle={() => setShowBdUnmapped(!showBdUnmapped)}
        />
      )}
      {report?.bd_extra && (
        <BdExtraSection
          bdExtraList={report.bd_extra}
          isOpen={showBdExtra}
          onToggle={() => setShowBdExtra(!showBdExtra)}
        />
      )}

      {/* 7. How to Read Popover Modal */}
      <HowToReadModal isOpen={showHowToRead} onClose={() => setShowHowToRead(false)} />
    </div>
  )
}
