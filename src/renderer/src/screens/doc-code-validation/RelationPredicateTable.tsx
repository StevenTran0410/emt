import React, { useState } from 'react'
import { ChevronDown, ChevronRight, FileCode, CheckCircle, AlertTriangle } from 'lucide-react'
import { Badge } from '../../components/ui'
import { EvidenceChip } from './EvidenceChip'
import type { PredicateRelationResult, RelationComparisonDetail } from '../../types/electron'

interface RelationPredicateTableProps {
  predicateResult: PredicateRelationResult
}

export function RelationPredicateTable({
  predicateResult
}: RelationPredicateTableProps): React.ReactElement {
  const [expandedRows, setExpandedRows] = useState<Set<number>>(new Set())

  const toggleRow = (idx: number) => {
    setExpandedRows((prev) => {
      const next = new Set(prev)
      if (next.has(idx)) next.delete(idx)
      else next.add(idx)
      return next
    })
  }

  const getEndpointBadge = (verdict: string) => {
    switch (verdict) {
      case 'MATCH':
        return <Badge variant="success">MATCH</Badge>
      case 'DOC_ONLY':
        return <Badge variant="warning">DOC ONLY</Badge>
      case 'CODE_ONLY':
        return <Badge variant="warning">CODE ONLY</Badge>
      default:
        return <Badge variant="neutral">UNKNOWN</Badge>
    }
  }

  const getMultiplicityBadge = (verdict: string) => {
    switch (verdict) {
      case 'EXACT_SITE_MATCH':
        return <Badge variant="success">EXACT SITE</Badge>
      case 'COUNT_ONLY_MATCH':
        return <Badge variant="info">COUNT MATCH</Badge>
      case 'COUNT_MISMATCH':
        return <Badge variant="warning">COUNT MISMATCH</Badge>
      case 'NOT_APPLICABLE':
        return <Badge variant="neutral">N/A</Badge>
      default:
        return <Badge variant="neutral">UNKNOWN</Badge>
    }
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs border-collapse">
        <thead>
          <tr className="border-b border-zinc-800 bg-zinc-950/60 text-zinc-400 font-mono uppercase text-[11px]">
            <th className="py-2.5 px-3 w-8" />
            <th className="py-2.5 px-3">Subject Key</th>
            <th className="py-2.5 px-3">Object Key</th>
            <th className="py-2.5 px-3">Endpoint Verdict</th>
            <th className="py-2.5 px-3">Multiplicity</th>
            <th className="py-2.5 px-3 text-right">Doc/Code</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-800/60 font-mono">
          {predicateResult.details.length === 0 ? (
            <tr>
              <td colSpan={6} className="py-8 text-center text-zinc-500 italic">
                No relation assertions found for this predicate.
              </td>
            </tr>
          ) : (
            predicateResult.details.map((detail, idx) => {
              const isExpanded = expandedRows.has(idx)
              const domId = `relation:${predicateResult.predicate}:${detail.subject_key}:${detail.object_key}`

              return (
                <React.Fragment key={idx}>
                  <tr
                    id={domId}
                    onClick={() => toggleRow(idx)}
                    className="hover:bg-zinc-800/50 cursor-pointer transition-colors"
                  >
                    <td className="py-2.5 px-3 text-zinc-500">
                      {isExpanded ? (
                        <ChevronDown className="w-4 h-4" />
                      ) : (
                        <ChevronRight className="w-4 h-4" />
                      )}
                    </td>
                    <td className="py-2.5 px-3 font-semibold text-zinc-200">{detail.subject_key}</td>
                    <td className="py-2.5 px-3 text-zinc-300">{detail.object_key}</td>
                    <td className="py-2.5 px-3">{getEndpointBadge(detail.endpoint_verdict)}</td>
                    <td className="py-2.5 px-3">{getMultiplicityBadge(detail.multiplicity_verdict)}</td>
                    <td className="py-2.5 px-3 text-right text-zinc-400">
                      {detail.doc_count} / {detail.code_count}
                    </td>
                  </tr>

                  {/* Expanded evidence detail row */}
                  {isExpanded && (
                    <tr className="bg-zinc-950/80">
                      <td colSpan={6} className="p-4 space-y-3 font-sans border-b border-zinc-800">
                        <div className="flex flex-col md:flex-row md:items-center justify-between gap-2 text-xs">
                          <div className="text-zinc-400">
                            <strong className="text-zinc-300 font-mono">Eligibility:</strong>{' '}
                            {detail.eligibility} — {detail.reason}
                          </div>

                          <div className="flex items-center gap-2">
                            <EvidenceChip
                              kind="relation"
                              label={`${detail.subject_key} -> ${detail.object_key}`}
                              targetId={domId}
                            />
                          </div>
                        </div>

                        {/* Evidence list */}
                        {detail.evidence && detail.evidence.length > 0 && (
                          <div className="space-y-1.5 pt-1">
                            <span className="text-[11px] font-mono uppercase font-bold text-zinc-400 block">
                              Evidence Occurrences ({detail.evidence.length}):
                            </span>
                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                              {detail.evidence.map((ev, evIdx) => (
                                <div
                                  key={evIdx}
                                  className="p-2 rounded bg-zinc-900 border border-zinc-800 text-[11px] font-mono flex items-center justify-between gap-2"
                                >
                                  <div className="truncate text-zinc-300">
                                    <span className="text-zinc-500 font-bold">{ev.source_store}:</span>{' '}
                                    <span className="text-emerald-300">
                                      {ev.rel_path}
                                      {ev.line_start ? `:${ev.line_start}` : ''}
                                    </span>
                                  </div>
                                  {ev.match_kind && (
                                    <Badge variant="neutral" className="text-[9px]">
                                      {ev.match_kind}
                                    </Badge>
                                  )}
                                </div>
                              ))}
                            </div>
                          </div>
                        )}
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              )
            })
          )}
        </tbody>
      </table>
    </div>
  )
}
