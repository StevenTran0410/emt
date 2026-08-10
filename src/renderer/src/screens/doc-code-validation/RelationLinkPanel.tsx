import React, { useState } from 'react'
import { Link, HelpCircle } from 'lucide-react'
import { RelationPredicateTable } from './RelationPredicateTable'
import type { DocCodeRelationCompareResult } from '../../types/electron'

interface RelationLinkPanelProps {
  relationResult: DocCodeRelationCompareResult
  onOpenMethodology: (dimensionId?: string) => void
}

export function RelationLinkPanel({
  relationResult,
  onOpenMethodology
}: RelationLinkPanelProps): React.ReactElement {
  const predicates = relationResult.per_predicate
  const [activePredicate, setActivePredicate] = useState<string>(
    predicates[0]?.predicate || 'calls'
  )

  const activeResult =
    predicates.find((p) => p.predicate === activePredicate) || predicates[0]

  return (
    <div className="bg-zinc-900/40 border border-zinc-800 rounded-xl overflow-hidden space-y-0">
      {/* Panel Header */}
      <div className="px-5 py-3.5 border-b border-zinc-800 bg-zinc-900/80 flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <div className="p-1.5 rounded-lg bg-sky-500/10 border border-sky-500/30 text-sky-400">
            <Link className="w-4 h-4" />
          </div>
          <div>
            <h3 className="font-bold text-zinc-100 text-sm">Panel B — Structural Link Correctness</h3>
            <p className="text-xs text-zinc-400">
              Validates call graphs, copybook inclusions, job-to-program executions, and DD dataset bindings
            </p>
          </div>
        </div>

        <button
          onClick={() => onOpenMethodology('relation-calls')}
          className="p-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-400 hover:text-zinc-200 transition-colors flex items-center gap-1 text-xs"
          title="Open Structural Link Methodology"
        >
          <HelpCircle className="w-4 h-4 text-sky-400" />
          <span className="hidden sm:inline">Methodology</span>
        </button>
      </div>

      {/* Predicate Tabs */}
      <div className="px-5 pt-3 bg-zinc-900 flex items-center gap-2 border-b border-zinc-800 overflow-x-auto">
        {predicates.map((pRes) => {
          const isActive = pRes.predicate === activePredicate
          const totalDetails = pRes.details.length

          return (
            <button
              key={pRes.predicate}
              onClick={() => setActivePredicate(pRes.predicate)}
              className={`px-3 py-2 border-b-2 text-xs font-mono font-semibold transition-colors flex items-center gap-2 shrink-0 ${
                isActive
                  ? 'border-sky-400 text-sky-300 bg-zinc-800/60'
                  : 'border-transparent text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/30'
              }`}
            >
              <span className="uppercase">{pRes.predicate}</span>
              <span
                className={`px-1.5 py-0.2 rounded-full text-[10px] ${
                  isActive ? 'bg-sky-500/20 text-sky-200 font-bold' : 'bg-zinc-800 text-zinc-400'
                }`}
              >
                {totalDetails}
              </span>
            </button>
          )
        })}
      </div>

      {/* Table Content */}
      <div className="p-4">
        {activeResult && <RelationPredicateTable predicateResult={activeResult} />}
      </div>
    </div>
  )
}
