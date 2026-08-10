import React from 'react'
import { Layers, HelpCircle } from 'lucide-react'
import { EntityTypeCard } from './EntityTypeCard'
import type { DocCodeCompareResult } from '../../types/electron'

interface EntityCompletenessPanelProps {
  entityResult: DocCodeCompareResult
  onOpenMethodology: (dimensionId?: string) => void
}

export function EntityCompletenessPanel({
  entityResult,
  onOpenMethodology
}: EntityCompletenessPanelProps): React.ReactElement {
  return (
    <div className="bg-zinc-900/40 border border-zinc-800 rounded-xl overflow-hidden space-y-0">
      {/* Panel Header */}
      <div className="px-5 py-3.5 border-b border-zinc-800 bg-zinc-900/80 flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <div className="p-1.5 rounded-lg bg-indigo-500/10 border border-indigo-500/30 text-indigo-400">
            <Layers className="w-4 h-4" />
          </div>
          <div>
            <h3 className="font-bold text-zinc-100 text-sm">Panel A — Entity Completeness</h3>
            <p className="text-xs text-zinc-400">
              Verifies presence of programs, jobs, steps, DD cards, and datasets across BD/DD specs vs implementation
            </p>
          </div>
        </div>

        <button
          onClick={() => onOpenMethodology('entity-program')}
          className="p-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-400 hover:text-zinc-200 transition-colors flex items-center gap-1 text-xs"
          title="Open Completeness Methodology"
        >
          <HelpCircle className="w-4 h-4 text-indigo-400" />
          <span className="hidden sm:inline">Methodology</span>
        </button>
      </div>

      {/* Entity Type Cards List */}
      <div className="p-5 space-y-3.5">
        {entityResult.per_type.map((typeRes) => (
          <EntityTypeCard key={typeRes.type} typeResult={typeRes} />
        ))}
      </div>
    </div>
  )
}
