import { useMemo } from 'react'
import type { DocCodeCompareResult, DocCodeRelationCompareResult } from '../../types/electron'

export interface ValidationMetrics {
  entityMatchPct: number
  relationMatchPct: number
  overallCoveragePct: number
  totalMatched: number
  totalComparable: number
  severityCounts: {
    error: number
    warning: number
    info: number
  }
}

export function useValidationMetrics(
  entityResult: DocCodeCompareResult | null,
  relationResult: DocCodeRelationCompareResult | null
): ValidationMetrics {
  return useMemo(() => {
    if (!entityResult && !relationResult) {
      return {
        entityMatchPct: 0,
        relationMatchPct: 0,
        overallCoveragePct: 0,
        totalMatched: 0,
        totalComparable: 0,
        severityCounts: { error: 0, warning: 0, info: 0 }
      }
    }

    // Entity match % = matched / max(1, matched + undocumented + missing)
    const eMatched = entityResult?.summary.matched || 0
    const eUndoc = entityResult?.summary.undocumented || 0
    const eMissing = entityResult?.summary.missing || 0
    const eUnknown = entityResult?.summary.unknown || 0
    const eComparable = eMatched + eUndoc + eMissing
    const entityMatchPct = Math.round((eMatched / Math.max(1, eComparable)) * 100)

    // Relation match % = matched / max(1, matched + doc_only + code_only)
    const rMatched = relationResult?.summary.matched || 0
    const rDocOnly = relationResult?.summary.doc_only || 0
    const rCodeOnly = relationResult?.summary.code_only || 0
    const rUnknown = relationResult?.summary.unknown || 0
    const rComparable = rMatched + rDocOnly + rCodeOnly
    const relationMatchPct = Math.round((rMatched / Math.max(1, rComparable)) * 100)

    // Overall coverage % = (entity_matched + relation_matched) / max(1, entity_comparable + relation_comparable)
    const totalMatched = eMatched + rMatched
    const totalComparable = eComparable + rComparable
    const overallCoveragePct = Math.round((totalMatched / Math.max(1, totalComparable)) * 100)

    // Derived Severity counts:
    // missing -> error
    // undocumented / doc_only / code_only / COUNT_MISMATCH -> warning
    // unknown / UNKNOWN verdicts -> info
    let countMismatchCount = 0
    if (relationResult?.per_predicate) {
      for (const p of relationResult.per_predicate) {
        for (const d of p.details || []) {
          if (d.multiplicity_verdict === 'COUNT_MISMATCH') {
            countMismatchCount++
          }
        }
      }
    }

    const error = eMissing
    const warning = eUndoc + rDocOnly + rCodeOnly + countMismatchCount
    const info = eUnknown + rUnknown

    return {
      entityMatchPct,
      relationMatchPct,
      overallCoveragePct,
      totalMatched,
      totalComparable,
      severityCounts: { error, warning, info }
    }
  }, [entityResult, relationResult])
}
