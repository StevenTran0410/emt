import { FolderOpen, GitCommit, Shield, Trash2, AlertTriangle, Link, RefreshCw } from 'lucide-react'
import type { LocalRepo } from '../../types/electron'
import { BranchPicker } from './BranchPicker'

export function LocalRepoCard({
  repo,
  onRevalidate,
  isRevalidating,
  onConfirmRemove,
}: {
  repo: LocalRepo
  onRevalidate: () => void
  isRevalidating: boolean
  onConfirmRemove: (id: string) => void
}) {
  return (
    <div className="bg-zinc-800/40 border border-zinc-700/70 rounded-xl p-4 hover:border-zinc-600 transition-colors">
      <div className="flex items-start gap-3">
        <div className="shrink-0 w-9 h-9 rounded-lg bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center mt-0.5">
          <FolderOpen size={16} className="text-emerald-400" />
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-semibold text-zinc-100">{repo.name}</span>

            {repo.is_git_repo ? (
              <BranchPicker repo={repo} />
            ) : (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs bg-amber-500/10 text-amber-400 border border-amber-500/20">
                <AlertTriangle size={10} />
                No git
              </span>
            )}

            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
              <Shield size={10} />
              Strict Local
            </span>
          </div>

          <p className="text-xs text-zinc-500 font-mono mt-1 truncate">{repo.path}</p>

          <div className="flex items-center gap-3 mt-2 flex-wrap">
            {repo.git_head_hash && (
              <span className="inline-flex items-center gap-1 text-xs text-zinc-500 font-mono">
                <GitCommit size={10} />
                {repo.git_head_hash}
              </span>
            )}
            {repo.git_remote_url && (
              <span className="inline-flex items-center gap-1 text-xs text-zinc-600 truncate max-w-xs">
                <Link size={10} />
                {repo.git_remote_url}
              </span>
            )}
            {repo.selected_branch && repo.selected_branch !== repo.git_branch && (
              <span className="text-xs text-indigo-400/70">
                Analysis: <span className="font-mono">{repo.selected_branch}</span>
              </span>
            )}
          </div>
        </div>

        <div className="flex items-center gap-1 shrink-0">
          <button
            onClick={onRevalidate}
            disabled={isRevalidating}
            title="Refresh git metadata"
            className="p-1.5 text-zinc-500 hover:text-zinc-300 hover:bg-zinc-700 rounded-md transition-colors disabled:opacity-40"
          >
            <RefreshCw size={13} className={isRevalidating ? 'animate-spin' : ''} />
          </button>
          <button
            onClick={() => onConfirmRemove(repo.id)}
            title="Remove folder"
            className="p-1.5 text-zinc-500 hover:text-red-400 hover:bg-red-500/10 rounded-md transition-colors"
          >
            <Trash2 size={13} />
          </button>
        </div>
      </div>
    </div>
  )
}
