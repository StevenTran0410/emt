import React from 'react'
import { Loader2 } from 'lucide-react'
import type { ImpactState } from './types'

interface ImpactPanelProps {
  impactState: ImpactState
  impactLoading: boolean
  inline?: boolean
}

export function ImpactPanel({ impactState, impactLoading, inline = false }: ImpactPanelProps): React.ReactElement {
  const { result, seedFiles } = impactState

  const wrapper = inline
    ? 'border-t border-zinc-700 pt-3 space-y-3'
    : 'w-80 border-r border-zinc-700 bg-zinc-900 p-4 overflow-y-auto text-xs space-y-4'

  return (
    <div className={wrapper}>
      {!inline && <div className="text-zinc-400 font-semibold">Impact Analysis</div>}

      {seedFiles.length > 0 && (
        <div>
          <div className="text-zinc-400 font-semibold mb-1">Seed files</div>
          <div className="space-y-1">
            {seedFiles.map((f) => (
              <div key={f} className="flex items-center gap-1">
                <div className="w-2 h-2 rounded-full bg-red-500 shrink-0" />
                <div className="text-[10px] text-zinc-300 truncate">{f.split('/').pop()}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {impactLoading && (
        <div className="flex items-center gap-2 text-zinc-500">
          <Loader2 size={12} className="animate-spin" />
          <span>Analyzing impact...</span>
        </div>
      )}

      {result && !impactLoading && (
        <>
          <div>
            <div className="text-zinc-400 font-semibold mb-1">Blast radius</div>
            <div className="text-zinc-300">
              <span className="font-mono">{result.blast_radius.total_affected}</span> files affected
            </div>
            {Object.entries(result.blast_radius.by_hop).sort(([a], [b]) => Number(a) - Number(b)).map(([hop, count]) => (
              <div key={hop} className="flex items-center gap-2 mt-1">
                <div
                  className="w-2 h-2 rounded-full shrink-0"
                  style={{ backgroundColor: result.subgraph.hop_colors[`__hop_${hop}`] ?? '#6b7280' }}
                />
                <span className="text-[10px] text-zinc-400">Hop {hop}: {count as number} files</span>
              </div>
            ))}
          </div>

          {result.blast_radius.high_risk_files.length > 0 && (
            <div>
              <div className="text-zinc-400 font-semibold mb-1">High-risk files</div>
              <div className="space-y-1">
                {result.blast_radius.high_risk_files.slice(0, 8).map((f) => (
                  <div key={f} className="text-[10px] text-red-400 truncate">{f.split('/').pop()}</div>
                ))}
                {result.blast_radius.high_risk_files.length > 8 && (
                  <div className="text-[10px] text-zinc-500 italic">
                    and {result.blast_radius.high_risk_files.length - 8} more...
                  </div>
                )}
              </div>
            </div>
          )}

          {result.blast_radius.affected_communities.length > 0 && (
            <div>
              <div className="text-zinc-400 font-semibold mb-1">Affected communities</div>
              <div className="space-y-1">
                {result.blast_radius.affected_communities.slice(0, 5).map((c) => (
                  <div key={c.community_id} className="text-[10px] text-zinc-400">
                    Community {c.community_id} ({c.member_count} members)
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
