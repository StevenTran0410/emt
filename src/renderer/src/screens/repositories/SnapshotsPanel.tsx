import React from 'react'
import { CheckCircle2, Loader2, Trash2 } from 'lucide-react'
import type { LocalRepo, RepoSnapshot } from './types'

interface SnapshotsPanelProps {
  selectedRepo: LocalRepo
  snapshots: RepoSnapshot[]
  selectedSnapshotId: string | null
  setSelectedSnapshotId: (id: string) => void
  loadingSnapshots: boolean
  selectedSnapshot: RepoSnapshot | null
  selectingSnapshot: boolean
  deletingSnapshot: boolean
  onSelectSnapshot: () => void
  onRequestDelete: () => void
}

export function SnapshotsPanel({
  selectedRepo,
  snapshots,
  selectedSnapshotId,
  setSelectedSnapshotId,
  loadingSnapshots,
  selectedSnapshot,
  selectingSnapshot,
  deletingSnapshot,
  onSelectSnapshot,
  onRequestDelete,
}: SnapshotsPanelProps): React.ReactElement {
  return (
    <div className="bg-zinc-800/40 border border-zinc-700 rounded-xl p-4">
      <div className="flex items-center justify-between mb-2">
        <h4 className="text-sm font-semibold text-zinc-200">Snapshots</h4>
        {loadingSnapshots && <Loader2 size={13} className="animate-spin text-zinc-500" />}
      </div>
      {snapshots.length === 0 ? (
        <p className="text-xs text-zinc-500">No snapshots yet.</p>
      ) : (
        <div className="space-y-2">
          {snapshots.map((s) => (
            <button
              key={s.id}
              onClick={() => setSelectedSnapshotId(s.id)}
              className={`w-full text-left flex items-center gap-2 text-xs border rounded-md px-3 py-2 ${
                selectedSnapshotId === s.id
                  ? 'border-blue-500/40 bg-blue-500/10'
                  : 'border-zinc-700 hover:border-zinc-600'
              }`}
            >
              <CheckCircle2 size={12} className={s.status === 'ready' ? 'text-emerald-400' : 'text-zinc-600'} />
              <span className="text-zinc-300">{s.branch ?? 'HEAD'}</span>
              <span className="text-zinc-600">·</span>
              <span className="font-mono text-zinc-300">{s.commit_hash ?? 'pending'}</span>
              <span className="text-zinc-600">·</span>
              <span className="text-zinc-500">{s.clone_policy}</span>
              {s.id === selectedRepo.active_snapshot_id && (
                <>
                  <span className="text-zinc-600">·</span>
                  <span className="text-emerald-400">active</span>
                </>
              )}
              <span className="ml-auto text-zinc-600">{new Date(s.synced_at).toLocaleString()}</span>
            </button>
          ))}
        </div>
      )}
      {selectedSnapshotId && (
        <div className="mt-3 flex items-center gap-2">
          <button
            onClick={onSelectSnapshot}
            disabled={selectingSnapshot || selectedSnapshot?.status !== 'ready'}
            className="px-3 py-2 text-xs bg-indigo-600 hover:bg-indigo-500 text-white rounded-md flex items-center gap-1.5 disabled:opacity-50"
          >
            {selectingSnapshot && <Loader2 size={12} className="animate-spin" />}
            {selectedSnapshot?.status === 'ready' ? 'Select' : 'Waiting for ready...'}
          </button>
          <button
            onClick={onRequestDelete}
            disabled={deletingSnapshot}
            className="px-3 py-2 text-xs border border-rose-700/60 text-rose-300 rounded-md hover:border-rose-500 disabled:opacity-50 inline-flex items-center gap-1.5"
          >
            {deletingSnapshot ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
            Delete snapshot
          </button>
        </div>
      )}
    </div>
  )
}
