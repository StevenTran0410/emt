import React from 'react'
import {
  Sparkles,
  ShieldCheck,
  AlertTriangle,
  AlertCircle,
  RefreshCw,
  Info,
  CheckCircle2,
  FileText,
  Lock
} from 'lucide-react'
import { Button, Badge, Spinner } from '../../components/ui'
import { EvidenceChip } from './EvidenceChip'
import type { AiAssessmentResult } from '../../types/electron'

// Map an AI evidence_ref to the DOM id of the Panel A/B row it cites, so the chip can
// scroll+highlight it. Must mirror the id formats produced by EntityTypeCard
// (`entity:<type>:<key>`) and RelationPredicateTable (`relation:<pred>:<subj>:<obj>`).
function refToTargetId(kind: string, ref: string): string | undefined {
  if (kind === 'entity') {
    // ref is a canonical key like "dd/CUSTFILE"; its namespace prefix is the entity type.
    const type = ref.split('/')[0]
    return type ? `entity:${type}:${ref}` : undefined
  }
  if (kind === 'relation') {
    // ref format: "<predicate> <subject> -> <object>", e.g. "calls program/A -> program/B".
    const m = ref.match(/^(\S+)\s+(.+?)\s*->\s*(.+)$/)
    if (m) return `relation:${m[1]}:${m[2]}:${m[3]}`
  }
  return undefined
}

interface AiAssessmentPanelProps {
  assessment: AiAssessmentResult | null
  loading: boolean
  error: string | null
  canRun: boolean
  onRunAssessment: () => void
}

export function AiAssessmentPanel({
  assessment,
  loading,
  error,
  canRun,
  onRunAssessment
}: AiAssessmentPanelProps): React.ReactElement {
  const getVerdictBadge = (verdict: string) => {
    switch (verdict) {
      case 'ADEQUATE':
        return (
          <Badge variant="success" className="text-xs px-2.5 py-1 font-bold">
            <CheckCircle2 className="w-3.5 h-3.5 mr-1" /> ADEQUATE
          </Badge>
        )
      case 'GAPS_FOUND':
        return (
          <Badge variant="warning" className="text-xs px-2.5 py-1 font-bold">
            <AlertTriangle className="w-3.5 h-3.5 mr-1" /> GAPS FOUND
          </Badge>
        )
      default:
        return (
          <Badge variant="error" className="text-xs px-2.5 py-1 font-bold">
            <AlertCircle className="w-3.5 h-3.5 mr-1" /> INSUFFICIENT EVIDENCE
          </Badge>
        )
    }
  }

  const getConfidenceBar = (conf: string) => {
    let width = '33%'
    let color = 'bg-rose-400'
    if (conf === 'high') {
      width = '100%'
      color = 'bg-emerald-400'
    } else if (conf === 'medium') {
      width = '66%'
      color = 'bg-amber-400'
    }

    return (
      <div className="flex items-center gap-2">
        <div className="w-20 bg-zinc-950 rounded-full h-1.5 overflow-hidden border border-zinc-800">
          <div className={`h-full ${color}`} style={{ width }} />
        </div>
        <span className="text-[11px] font-mono capitalize font-bold text-zinc-300">{conf}</span>
      </div>
    )
  }

  return (
    <div
      id="ai-assessment-panel"
      className="bg-violet-950/20 border border-violet-800/80 rounded-xl overflow-hidden shadow-lg space-y-0"
    >
      {/* Disclaimer Strip */}
      <div className="bg-violet-950/60 border-b border-violet-800/80 px-4 py-2 flex items-center justify-between text-xs text-violet-300 font-mono">
        <div className="flex items-center gap-2">
          <Lock className="w-3.5 h-3.5 text-violet-400 shrink-0" />
          <span>AI Assessment — Informational judgment grounded in Panel A + B evidence.</span>
        </div>
        <span className="text-[10px] text-violet-400 uppercase font-bold tracking-wider">Non-Authoritative</span>
      </div>

      {/* Panel Header */}
      <div className="px-5 py-4 flex flex-col md:flex-row md:items-center justify-between gap-3 border-b border-violet-900/60 bg-violet-950/40">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-xl bg-violet-500/20 border border-violet-500/40 text-violet-300">
            <Sparkles className="w-5 h-5 text-violet-400" />
          </div>
          <div>
            <h3 className="font-bold text-zinc-100 text-base">AI Specification Assessment</h3>
            <p className="text-xs text-violet-300/80">
              Evaluates specification completeness and structural link accuracy against physical implementation code
            </p>
          </div>
        </div>

        <Button
          variant="primary"
          onClick={onRunAssessment}
          disabled={!canRun || loading}
          leftIcon={loading ? <Spinner size="sm" /> : <Sparkles className="w-4 h-4 text-violet-200" />}
          className="bg-violet-600 hover:bg-violet-500 border-violet-500 text-white shadow-md font-semibold"
        >
          {loading ? 'Evaluating Evidence...' : assessment ? 'Re-run AI Assessment' : 'Run AI Assessment'}
        </Button>
      </div>

      {/* Main Body */}
      <div className="p-5 space-y-4">
        {error && (
          <div className="p-3 bg-rose-950/50 border border-rose-800/80 rounded-lg text-xs text-rose-300 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-rose-400 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {loading ? (
          <div className="py-12 text-center text-violet-300 space-y-3 flex flex-col items-center">
            <Spinner size="lg" className="text-violet-400" />
            <div className="font-mono text-xs font-semibold">Gathering Panel A + B Evidence & Consulting Auditor Model...</div>
            <div className="text-[11px] text-violet-400/80 italic">Checking completeness, relation link evidence, and calculating confidence.</div>
          </div>
        ) : assessment ? (
          <div className="space-y-5">
            {/* Stale Warning */}
            {assessment.stale && (
              <div className="p-3 bg-amber-950/40 border border-amber-800/60 rounded-lg text-xs text-amber-300 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0" />
                  <span>Docs or Code graph changed since this assessment was generated.</span>
                </div>
                <Button variant="ghost" size="sm" onClick={onRunAssessment} className="text-amber-300 underline">
                  Re-evaluate
                </Button>
              </div>
            )}

            {/* Verdict & Confidence Header */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3 bg-zinc-950/70 p-4 rounded-xl border border-violet-900/60">
              <div>
                <span className="text-[10px] font-mono uppercase text-violet-400 font-bold block mb-1">
                  Overall Audit Verdict
                </span>
                {getVerdictBadge(assessment.overall_verdict)}
              </div>

              <div>
                <span className="text-[10px] font-mono uppercase text-violet-400 font-bold block mb-1">
                  Auditor Confidence
                </span>
                {getConfidenceBar(assessment.confidence)}
              </div>

              <div>
                <span className="text-[10px] font-mono uppercase text-violet-400 font-bold block mb-1">
                  Evaluated Model
                </span>
                <span className="text-xs font-mono text-zinc-300">{assessment.model}</span>
                {assessment.from_cache && (
                  <span className="ml-2 text-[10px] font-mono px-1.5 py-0.2 rounded bg-zinc-800 text-zinc-400">
                    from cache
                  </span>
                )}
              </div>
            </div>

            {/* Notes Section */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="p-3.5 rounded-xl bg-zinc-950/60 border border-zinc-800/80 space-y-1">
                <span className="text-[11px] font-mono font-bold text-indigo-400 uppercase flex items-center gap-1.5">
                  <FileText className="w-3.5 h-3.5" /> Completeness Assessment
                </span>
                <p className="text-xs text-zinc-300 leading-relaxed">{assessment.completeness_note}</p>
              </div>

              <div className="p-3.5 rounded-xl bg-zinc-950/60 border border-zinc-800/80 space-y-1">
                <span className="text-[11px] font-mono font-bold text-sky-400 uppercase flex items-center gap-1.5">
                  <ShieldCheck className="w-3.5 h-3.5" /> Correctness Assessment
                </span>
                <p className="text-xs text-zinc-300 leading-relaxed">{assessment.correctness_note}</p>
              </div>
            </div>

            {/* Caveats */}
            {assessment.caveats && assessment.caveats.length > 0 && (
              <div className="p-3 bg-amber-950/20 border border-amber-800/40 rounded-xl space-y-1 text-xs">
                <span className="font-mono text-[10px] text-amber-400 font-bold uppercase block">
                  Caveats & Scope Limitations:
                </span>
                <ul className="list-disc list-inside text-zinc-300 space-y-0.5">
                  {assessment.caveats.map((cav, idx) => (
                    <li key={idx}>{cav}</li>
                  ))}
                </ul>
              </div>
            )}

            {/* Concerns List */}
            <div className="space-y-2.5 pt-2">
              <div className="flex items-center justify-between text-xs">
                <span className="font-bold text-zinc-200 uppercase tracking-wider font-mono">
                  Identified Concerns & Recommendations ({assessment.concerns.length})
                </span>
              </div>

              {assessment.concerns.length === 0 ? (
                <div className="p-4 bg-zinc-950/60 rounded-xl text-center text-zinc-400 text-xs italic flex items-center justify-center gap-2">
                  <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                  <span>No architectural concerns identified by AI assessment.</span>
                </div>
              ) : (
                <div className="space-y-2.5">
                  {assessment.concerns.map((c, idx) => {
                    const sevBadge =
                      c.severity === 'error'
                        ? 'bg-rose-500/20 text-rose-300 border-rose-500/30'
                        : c.severity === 'warning'
                        ? 'bg-amber-500/20 text-amber-300 border-amber-500/30'
                        : 'bg-zinc-500/20 text-zinc-300 border-zinc-500/30'

                    return (
                      <div
                        key={idx}
                        className="p-4 rounded-xl bg-zinc-950/70 border border-zinc-800 space-y-2.5 text-xs"
                      >
                        <div className="flex items-start justify-between gap-2">
                          <div className="flex items-center gap-2">
                            <span className="font-mono text-zinc-500 font-bold text-xs">{idx + 1}.</span>
                            <h4 className="font-bold text-zinc-100 text-xs">{c.title}</h4>
                          </div>
                          <span className={`px-2 py-0.5 rounded text-[10px] font-mono font-bold uppercase border ${sevBadge}`}>
                            {c.severity}
                          </span>
                        </div>

                        <p className="text-zinc-300 leading-relaxed">{c.detail}</p>

                        {/* Evidence Refs */}
                        {c.evidence_refs && c.evidence_refs.length > 0 && (
                          <div className="flex items-center gap-1.5 flex-wrap pt-1">
                            <span className="text-[10px] text-zinc-500 font-mono uppercase font-bold">
                              Evidence Citations:
                            </span>
                            {c.evidence_refs.map((refItem, rIdx) => (
                              <EvidenceChip
                                key={rIdx}
                                kind={refItem.kind}
                                label={refItem.ref}
                                targetId={refToTargetId(refItem.kind, refItem.ref)}
                              />
                            ))}
                          </div>
                        )}

                        {/* Recommendation */}
                        {c.recommendation && (
                          <div className="p-2.5 rounded-lg bg-violet-950/40 border border-violet-900/50 text-violet-200 text-[11px] font-mono">
                            <strong className="text-violet-400">Recommendation:</strong> {c.recommendation}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className="py-8 text-center text-zinc-400 space-y-3 flex flex-col items-center">
            <Sparkles className="w-8 h-8 text-violet-400/60" />
            <div className="text-xs max-w-md">
              Run AI Assessment to evaluate spec completeness and link correctness against implementation code.
            </div>
            <Button
              variant="primary"
              onClick={onRunAssessment}
              disabled={!canRun}
              leftIcon={<Sparkles className="w-4 h-4 text-violet-200" />}
              className="bg-violet-600 hover:bg-violet-500 border-violet-500 text-white font-semibold"
            >
              Run AI Assessment
            </Button>
          </div>
        )}
      </div>
    </div>
  )
}
