import React from 'react'
import {
  ShieldCheck,
  ShieldAlert,
  Layers,
  Link,
  Sparkles,
  AlertTriangle,
  AlertCircle,
  Info
} from 'lucide-react'
import type { ValidationMetrics } from './useValidationMetrics'
import type { AiAssessmentResult } from '../../types/electron'

interface ScorecardProps {
  metrics: ValidationMetrics
  authoritative: boolean
  authoritativeReason?: string
  aiAssessment: AiAssessmentResult | null
  onScrollToAi: () => void
}

export function Scorecard({
  metrics,
  authoritative,
  authoritativeReason,
  aiAssessment,
  onScrollToAi
}: ScorecardProps): React.ReactElement {
  const getCoverageColor = (pct: number) => {
    if (pct >= 90) return 'text-emerald-400 border-emerald-500/40 bg-emerald-950/40'
    if (pct >= 70) return 'text-amber-400 border-amber-500/40 bg-amber-950/40'
    return 'text-rose-400 border-rose-500/40 bg-rose-950/40'
  }

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-6 gap-3">
      {/* 1. Overall Coverage % */}
      <div className={`p-4 rounded-xl border flex flex-col justify-between ${getCoverageColor(metrics.overallCoveragePct)}`}>
        <div className="flex items-center justify-between text-xs font-semibold uppercase tracking-wider text-zinc-400">
          <span>Overall Coverage</span>
          <ShieldCheck className="w-4 h-4" />
        </div>
        <div className="my-2">
          <span className="text-3xl font-extrabold font-mono">{metrics.overallCoveragePct}%</span>
          <span className="text-xs text-zinc-400 ml-1.5 font-normal">
            ({metrics.totalMatched}/{metrics.totalComparable})
          </span>
        </div>
        <div className="w-full bg-zinc-950/60 rounded-full h-1.5 overflow-hidden">
          <div
            className={`h-full transition-all duration-500 ${
              metrics.overallCoveragePct >= 90
                ? 'bg-emerald-400'
                : metrics.overallCoveragePct >= 70
                ? 'bg-amber-400'
                : 'bg-rose-400'
            }`}
            style={{ width: `${Math.min(100, metrics.overallCoveragePct)}%` }}
          />
        </div>
      </div>

      {/* 2. Entity Match % */}
      <div className="p-4 rounded-xl border border-zinc-800 bg-zinc-900/60 flex flex-col justify-between">
        <div className="flex items-center justify-between text-xs font-semibold uppercase tracking-wider text-zinc-400">
          <span>Entity Match</span>
          <Layers className="w-4 h-4 text-indigo-400" />
        </div>
        <div className="my-2">
          <span className="text-3xl font-bold font-mono text-indigo-300">{metrics.entityMatchPct}%</span>
        </div>
        <div className="text-[11px] text-zinc-500 flex items-center justify-between">
          <span>Panel A Completeness</span>
          <span className="text-zinc-400 font-mono">Derived</span>
        </div>
      </div>

      {/* 3. Relation Match % */}
      <div className="p-4 rounded-xl border border-zinc-800 bg-zinc-900/60 flex flex-col justify-between">
        <div className="flex items-center justify-between text-xs font-semibold uppercase tracking-wider text-zinc-400">
          <span>Relation Match</span>
          <Link className="w-4 h-4 text-sky-400" />
        </div>
        <div className="my-2">
          <span className="text-3xl font-bold font-mono text-sky-300">{metrics.relationMatchPct}%</span>
        </div>
        <div className="text-[11px] text-zinc-500 flex items-center justify-between">
          <span>Panel B Link Correctness</span>
          <span className="text-zinc-400 font-mono">Derived</span>
        </div>
      </div>

      {/* 4. Authoritative Badge */}
      <div className="p-4 rounded-xl border border-zinc-800 bg-zinc-900/60 flex flex-col justify-between">
        <div className="flex items-center justify-between text-xs font-semibold uppercase tracking-wider text-zinc-400">
          <span>Parse Scope</span>
          {authoritative ? (
            <ShieldCheck className="w-4 h-4 text-emerald-400" />
          ) : (
            <ShieldAlert className="w-4 h-4 text-amber-400" />
          )}
        </div>
        <div className="my-2">
          <span
            className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-bold font-mono ${
              authoritative
                ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'
                : 'bg-amber-500/20 text-amber-300 border border-amber-500/30'
            }`}
          >
            {authoritative ? 'AUTHORITATIVE' : 'NON-AUTHORITATIVE'}
          </span>
        </div>
        <div className="text-[11px] text-zinc-400 truncate" title={authoritativeReason}>
          {authoritativeReason || (authoritative ? 'Complete scope parsed' : 'Partial scope parsed')}
        </div>
      </div>

      {/* 5. Findings Severity Chip */}
      <div className="p-4 rounded-xl border border-zinc-800 bg-zinc-900/60 flex flex-col justify-between">
        <div className="flex items-center justify-between text-xs font-semibold uppercase tracking-wider text-zinc-400">
          <span>Findings</span>
          <AlertTriangle className="w-4 h-4 text-amber-400" />
        </div>
        <div className="my-1.5 space-y-1">
          <div className="flex items-center justify-between text-[11px]">
            <span className="text-rose-400 flex items-center gap-1 font-semibold">
              <AlertCircle className="w-3 h-3" /> Error
            </span>
            <span className="font-mono text-zinc-200 font-bold">{metrics.severityCounts.error}</span>
          </div>
          <div className="flex items-center justify-between text-[11px]">
            <span className="text-amber-400 flex items-center gap-1 font-semibold">
              <AlertTriangle className="w-3 h-3" /> Warning
            </span>
            <span className="font-mono text-zinc-200 font-bold">{metrics.severityCounts.warning}</span>
          </div>
          <div className="flex items-center justify-between text-[11px]">
            <span className="text-zinc-400 flex items-center gap-1">
              <Info className="w-3 h-3" /> Info
            </span>
            <span className="font-mono text-zinc-300">{metrics.severityCounts.info}</span>
          </div>
        </div>
        <div className="text-[10px] text-zinc-500">UI-derived severity</div>
      </div>

      {/* 6. AI Assessment Tile */}
      <button
        onClick={onScrollToAi}
        className="p-4 rounded-xl border border-violet-800/80 bg-violet-950/40 hover:bg-violet-900/40 transition-colors text-left flex flex-col justify-between group"
      >
        <div className="flex items-center justify-between text-xs font-semibold uppercase tracking-wider text-violet-300">
          <span>AI Verdict</span>
          <Sparkles className="w-4 h-4 text-violet-400 group-hover:scale-110 transition-transform" />
        </div>
        <div className="my-2">
          {aiAssessment ? (
            <span
              className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-extrabold font-mono ${
                aiAssessment.overall_verdict === 'ADEQUATE'
                  ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'
                  : aiAssessment.overall_verdict === 'GAPS_FOUND'
                  ? 'bg-amber-500/20 text-amber-300 border border-amber-500/30'
                  : 'bg-rose-500/20 text-rose-300 border border-rose-500/30'
              }`}
            >
              {aiAssessment.overall_verdict}
            </span>
          ) : (
            <span className="text-xs text-zinc-400 italic">Not run yet</span>
          )}
        </div>
        <div className="text-[11px] text-violet-300 font-medium flex items-center justify-between">
          <span>{aiAssessment ? `${aiAssessment.confidence} confidence` : 'Click to inspect AI Assessment'}</span>
          <span className="text-violet-400 font-bold">&rarr;</span>
        </div>
      </button>
    </div>
  )
}
