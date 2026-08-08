import React from 'react'
import type { LocalRepo } from './types'

interface RepoListPanelProps {
  repos: LocalRepo[]
  selectedRepoId: string | null
  onSelect: (repoId: string) => void
}

export function RepoListPanel({ repos, selectedRepoId, onSelect }: RepoListPanelProps): React.ReactElement {
  return (
    <div className="col-span-4 space-y-2">
      <h3 className="text-sm font-semibold text-zinc-300">Repositories</h3>
      {repos.map((repo) => (
        <button
          key={repo.id}
          onClick={() => onSelect(repo.id)}
          className={`w-full text-left rounded-lg border px-3 py-2 transition-colors ${
            selectedRepoId === repo.id
              ? 'border-blue-500/40 bg-blue-500/10'
              : 'border-zinc-700 hover:border-zinc-600 bg-zinc-800/40'
          }`}
        >
          <div className="text-sm text-zinc-100 font-medium truncate">{repo.name}</div>
          <div className="text-xs text-zinc-500 truncate">{repo.path}</div>
        </button>
      ))}
    </div>
  )
}
