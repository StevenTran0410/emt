import { FolderOpen, GitBranch, GitCommit, Globe, Shield, AlertTriangle, XCircle } from 'lucide-react'
import { Button } from '../../components/ui'
import type { ValidateFolderResponse } from '../../types/electron'

export function ValidationPreview({
  result,
  onConfirm,
  onCancel,
  adding,
}: {
  result: ValidateFolderResponse
  onConfirm: () => void
  onCancel: () => void
  adding: boolean
}) {
  const ok = result.exists && result.is_directory

  return (
    <div className="bg-zinc-800/60 border border-zinc-700 rounded-xl p-5 space-y-4">
      <div className="flex items-start gap-3">
        <div className={`shrink-0 mt-0.5 w-8 h-8 rounded-lg flex items-center justify-center ${ok ? 'bg-emerald-500/10 border border-emerald-500/20' : 'bg-red-500/10 border border-red-500/20'}`}>
          {ok ? <FolderOpen size={15} className="text-emerald-400" /> : <XCircle size={15} className="text-red-400" />}
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-zinc-100 truncate">{result.name}</p>
          <p className="text-xs text-zinc-500 truncate font-mono mt-0.5">{result.path}</p>
        </div>
      </div>

      {!ok && (
        <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2 text-sm text-red-400">
          <XCircle size={13} />
          {!result.exists ? 'Path does not exist' : 'Path is not a directory'}
        </div>
      )}

      {ok && (
        <div className="space-y-2">
          {result.is_git_repo ? (
            <div className="flex flex-wrap items-center gap-2">
              <span className="inline-flex items-center gap-1.5 px-2 py-1 rounded-md text-xs font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                <GitBranch size={11} />
                {result.git_branch ?? 'detached HEAD'}
              </span>
              {result.git_head_hash && (
                <span className="inline-flex items-center gap-1.5 px-2 py-1 rounded-md text-xs font-mono bg-zinc-700/60 text-zinc-300 border border-zinc-600">
                  <GitCommit size={11} />
                  {result.git_head_hash}
                </span>
              )}
              {result.git_remote_url && (
                <span className="inline-flex items-center gap-1.5 px-2 py-1 rounded-md text-xs bg-zinc-700/60 text-zinc-500 border border-zinc-600 max-w-xs truncate">
                  <Globe size={11} />
                  {result.git_remote_url}
                </span>
              )}
            </div>
          ) : (
            <div className="flex items-center gap-2 bg-amber-500/10 border border-amber-500/20 rounded-lg px-3 py-2 text-xs text-amber-400">
              <AlertTriangle size={13} />
              No .git folder found — folder will be indexed without commit history
            </div>
          )}

          {result.has_size_warning && (
            <div className="flex items-start gap-2 bg-amber-500/5 border border-amber-500/15 rounded-lg px-3 py-2 text-xs text-amber-400/80">
              <AlertTriangle size={12} className="shrink-0 mt-0.5" />
              <span><strong>Large folder:</strong> {result.size_warning_reason}. Initial scan may take longer.</span>
            </div>
          )}

          <div className="flex items-center gap-1.5 text-xs text-emerald-400">
            <Shield size={12} />
            Strict Local — no data leaves this device
          </div>
        </div>
      )}

      <div className="flex items-center gap-2 pt-1">
        <Button
          variant="secondary"
          onClick={onCancel}
          className="flex-1"
        >
          Cancel
        </Button>
        <Button
          variant="primary"
          onClick={onConfirm}
          disabled={!ok || adding}
          loading={adding}
          className="flex-1"
        >
          {ok ? 'Add folder' : 'Add folder'}
        </Button>
      </div>
    </div>
  )
}
