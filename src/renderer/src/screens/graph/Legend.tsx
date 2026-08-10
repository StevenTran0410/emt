import React from 'react'
import { Panel } from '@xyflow/react'
import { COMMUNITY_COLORS } from './utils'

interface LegendProps {
  communityCount: number
  impactActive?: boolean
}

export function Legend({ communityCount, impactActive }: LegendProps): React.ReactElement {
  const shown = Math.min(10, communityCount)
  const extra = communityCount - shown
  return (
    <Panel position="top-right">
    <div className="bg-zinc-800/90 border border-zinc-700 rounded p-3 text-xs space-y-1 pointer-events-none">
      {impactActive ? (
        <>
          <div className="font-semibold text-zinc-300 mb-1.5">Impact Mode</div>
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: '#ef4444' }} />
            <span className="text-zinc-400">Seed (hop 0)</span>
          </div>
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: '#f97316' }} />
            <span className="text-zinc-400">Hop 1</span>
          </div>
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: '#eab308' }} />
            <span className="text-zinc-400">Hop 2</span>
          </div>
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: '#6b7280' }} />
            <span className="text-zinc-400">Hop 3+</span>
          </div>
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: '#52525b' }} />
            <span className="text-zinc-400">Not in cone</span>
          </div>
        </>
      ) : (
        <>
          <div className="font-semibold text-zinc-300 mb-1.5">Legend</div>
          {Array.from({ length: shown }).map((_, i) => (
            <div key={i} className="flex items-center gap-2">
              <div
                className="w-2 h-2 rounded-full shrink-0"
                style={{ backgroundColor: COMMUNITY_COLORS[i % COMMUNITY_COLORS.length] }}
              />
              <span className="text-zinc-400">Community {i}</span>
            </div>
          ))}
          {extra > 0 && (
            <div className="text-zinc-500 italic">+{extra} more</div>
          )}
          <div className="border-t border-zinc-700 pt-1 mt-1" />
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-red-500 shrink-0" />
            <span className="text-zinc-400">Circular import</span>
          </div>
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-amber-400 shrink-0" />
            <span className="text-zinc-400">Selected</span>
          </div>
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full border border-amber-400 shrink-0" />
            <span className="text-zinc-400">Neighbor</span>
          </div>
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded bg-amber-700 border border-amber-500 shrink-0" />
            <span className="text-zinc-400">Unresolved Ref</span>
          </div>
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded bg-zinc-800 border border-zinc-600 shrink-0" />
            <span className="text-zinc-400">External System</span>
          </div>
        </>
      )}
    </div>
    </Panel>
  )
}
