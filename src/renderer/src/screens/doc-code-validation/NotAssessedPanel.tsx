import React from 'react'
import { Info } from 'lucide-react'
import { Badge } from '../../components/ui'

interface NotAssessedPanelProps {
  entityNotAssessed: string[]
  relationNotAssessed: string[]
}

export function NotAssessedPanel({
  entityNotAssessed,
  relationNotAssessed
}: NotAssessedPanelProps): React.ReactElement {
  return (
    <div className="bg-zinc-900/30 border border-zinc-800 rounded-xl p-4 space-y-3 text-xs">
      <div className="flex items-center gap-2 text-zinc-400 font-semibold uppercase tracking-wider text-[11px]">
        <Info className="w-4 h-4 text-zinc-500" />
        <span>Panel C — Explicitly Excluded Scopes (Not Assessed)</span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="space-y-1.5">
          <span className="text-zinc-500 text-[11px] font-mono uppercase font-bold block">
            Entity Types Excluded:
          </span>
          <div className="flex flex-wrap gap-1.5">
            {entityNotAssessed.map((item, idx) => (
              <Badge key={idx} variant="neutral" className="text-[10px] font-mono">
                {item}
              </Badge>
            ))}
          </div>
        </div>

        <div className="space-y-1.5">
          <span className="text-zinc-500 text-[11px] font-mono uppercase font-bold block">
            Relation Types Excluded:
          </span>
          <div className="flex flex-wrap gap-1.5">
            {relationNotAssessed.map((item, idx) => (
              <Badge key={idx} variant="neutral" className="text-[10px] font-mono">
                {item}
              </Badge>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
