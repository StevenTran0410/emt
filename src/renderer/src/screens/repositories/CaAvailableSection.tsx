import React from 'react'
import { Button } from '../../components/ui'
import type { LocalRepo } from './types'

interface CaAvailableSectionProps {
  caReposAvailable: LocalRepo[]
  activatingPath: string | null
  onUseForAEH: (repo: LocalRepo) => void
}

export function CaAvailableSection({ caReposAvailable, activatingPath, onUseForAEH }: CaAvailableSectionProps): React.ReactElement {
  return (
    <div className="border border-indigo-800/40 bg-indigo-950/20 rounded-lg p-4 space-y-2.5">
      <h3 className="text-sm font-semibold text-indigo-300">Available from Code Analysis</h3>
      <p className="text-xs text-indigo-300/70">
        Already cloned/imported for Code Analysis — reuse it for AEH without cloning again
        (AEH indexes it independently, test files included).
      </p>
      <div className="space-y-1.5">
        {caReposAvailable.map((r) => (
          <div key={r.id} className="flex items-center justify-between gap-3 bg-zinc-900/60 border border-zinc-800 rounded-md px-3 py-2">
            <div className="min-w-0">
              <p className="text-sm text-zinc-200 truncate">{r.name}</p>
              <p className="text-xs text-zinc-500 truncate">{r.path}</p>
            </div>
            <Button
              variant="secondary"
              className="shrink-0 text-xs px-2.5 py-1"
              disabled={activatingPath === r.path}
              onClick={() => onUseForAEH(r)}
            >
              {activatingPath === r.path ? 'Activating…' : 'Use for AEH'}
            </Button>
          </div>
        ))}
      </div>
    </div>
  )
}
