import React from 'react'
import { AlertTriangle, GitBranch, Loader2, RefreshCw } from 'lucide-react'
import type { EstimateFileCountResponse } from '../../types/electron'
import type { ClonePolicy, LocalRepo, SyncMode } from './types'

interface RepoSetupPanelProps {
  selectedRepo: LocalRepo
  branch: string
  setBranchLocal: (branch: string) => void
  branchesMap: Record<string, string[]>
  loadBranches: (repoId: string) => void
  clonePolicy: ClonePolicy
  setClonePolicy: (policy: ClonePolicy) => void
  syncMode: SyncMode
  setSyncMode: (mode: SyncMode) => void
  pinnedRef: string
  setPinnedRef: (ref: string) => void
  ignoreText: string
  setIgnoreText: (text: string) => void
  detectSubmodules: boolean
  setDetectSubmodules: (value: boolean) => void
  includeTests: boolean
  setIncludeTests: (value: boolean) => void
  hasSnapshot: boolean
  branchChanged: string | boolean
  estimate: EstimateFileCountResponse | null
  saving: boolean
  estimating: boolean
  syncing: boolean
  syncProgress: number
  onSave: () => void
  onEstimate: () => void
  onPrepareSnapshot: () => void
}

export function RepoSetupPanel({
  selectedRepo,
  branch,
  setBranchLocal,
  branchesMap,
  loadBranches,
  clonePolicy,
  setClonePolicy,
  syncMode,
  setSyncMode,
  pinnedRef,
  setPinnedRef,
  ignoreText,
  setIgnoreText,
  detectSubmodules,
  setDetectSubmodules,
  includeTests,
  setIncludeTests,
  hasSnapshot,
  branchChanged,
  estimate,
  saving,
  estimating,
  syncing,
  syncProgress,
  onSave,
  onEstimate,
  onPrepareSnapshot,
}: RepoSetupPanelProps): React.ReactElement {
  return (
    <div className="bg-zinc-800/50 border border-zinc-700 rounded-xl p-4 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-zinc-200">Repository Setup</h3>
        <span className="text-xs text-zinc-500">{selectedRepo.name}</span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div>
          <label className="text-xs text-zinc-300 mb-1 block">Branch / ref</label>
          <div className="flex gap-2">
            <select
              className="min-w-0 flex-1 bg-zinc-900 border border-zinc-700 rounded-md px-2 py-2 text-sm text-zinc-100"
              value={branch}
              onChange={(e) => setBranchLocal(e.target.value)}
            >
              {(branchesMap[selectedRepo.id] ?? [selectedRepo.git_branch ?? '']).filter(Boolean).map((b) => (
                <option key={b} value={b}>{b}</option>
              ))}
            </select>
            <button
              onClick={() => loadBranches(selectedRepo.id)}
              className="px-2.5 py-2 text-xs border border-zinc-700 rounded-md text-zinc-400 hover:text-zinc-200 hover:border-zinc-600"
            >
              <GitBranch size={13} />
            </button>
          </div>
        </div>

        <div>
          <label className="text-xs text-zinc-300 mb-1 block">Clone policy</label>
          <select
            className="min-w-0 w-full bg-zinc-900 border border-zinc-700 rounded-md px-2 py-2 text-sm text-zinc-100"
            value={clonePolicy}
            onChange={(e) => setClonePolicy(e.target.value as ClonePolicy)}
          >
            <option value="full">Full clone</option>
            <option value="shallow">Shallow (--depth=1)</option>
            <option value="partial">Partial (--filter=blob:none)</option>
          </select>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div>
          <label className="text-xs text-zinc-300 mb-1 block">Sync mode</label>
          <select
            className="w-full bg-zinc-900 border border-zinc-700 rounded-md px-2 py-2 text-sm text-zinc-100"
            value={syncMode}
            onChange={(e) => setSyncMode(e.target.value as SyncMode)}
          >
            <option value="latest">Always pull latest</option>
            <option value="pinned">Pin to specific ref</option>
          </select>
        </div>

        <div>
          <label className="text-xs text-zinc-300 mb-1 block">Pinned ref / commit SHA</label>
          <input
            className="w-full bg-zinc-900 border border-zinc-700 rounded-md px-2 py-2 text-sm text-zinc-100 font-mono disabled:opacity-50"
            value={pinnedRef}
            onChange={(e) => setPinnedRef(e.target.value)}
            disabled={syncMode !== 'pinned'}
            placeholder="main or a1b2c3d"
          />
        </div>
      </div>

      <div>
        <label className="text-xs text-zinc-300 mb-1 block">Repo-specific ignore overrides (one per line)</label>
        <textarea
          rows={4}
          className="w-full bg-zinc-900 border border-zinc-700 rounded-md px-2 py-2 text-xs text-zinc-200 font-mono"
          value={ignoreText}
          onChange={(e) => setIgnoreText(e.target.value)}
          placeholder="**/*.gen.ts&#10;vendor/**"
        />
      </div>

      <label className="inline-flex items-center gap-2 text-xs text-zinc-300">
        <input
          type="checkbox"
          checked={detectSubmodules}
          onChange={(e) => setDetectSubmodules(e.target.checked)}
        />
        Detect and note submodules (no deep indexing in v1)
      </label>

      <label className="inline-flex items-center gap-2 text-xs text-zinc-300">
        <input
          type="checkbox"
          checked={includeTests}
          onChange={(e) => setIncludeTests(e.target.checked)}
        />
        Include test files and folders (default: excluded — any path containing "test" is skipped)
      </label>

      {hasSnapshot && branchChanged && (
        <div className="flex items-start gap-2 text-xs text-amber-300 bg-amber-500/10 border border-amber-500/20 rounded-md px-3 py-2">
          <AlertTriangle size={13} className="shrink-0 mt-0.5" />
          Changing branch after an existing snapshot will create a new snapshot and invalidate cached indexes.
        </div>
      )}

      {estimate && (
        <div className="text-xs text-zinc-300 space-y-1">
          <div>Estimated files after ignore rules: <span className="text-zinc-200 font-semibold">{estimate.estimated_file_count.toLocaleString()}</span></div>
          <div className="text-zinc-500">Effective ignores: {estimate.effective_ignores.join(', ') || '(none)'}</div>
        </div>
      )}

      <div className="flex items-center gap-2 flex-wrap">
        <button
          onClick={onSave}
          disabled={saving}
          className="px-3 py-2 text-xs bg-blue-600 hover:bg-blue-500 text-white rounded-md flex items-center gap-1.5 disabled:opacity-50"
        >
          {saving && <Loader2 size={12} className="animate-spin" />}
          Save settings
        </button>
        <button
          onClick={onEstimate}
          disabled={estimating}
          className="px-3 py-2 text-xs border border-zinc-700 text-zinc-300 rounded-md hover:border-zinc-600 disabled:opacity-50"
        >
          {estimating ? 'Estimating...' : 'Estimate file count'}
        </button>
        <button
          onClick={onPrepareSnapshot}
          disabled={syncing}
          className="px-3 py-2 text-xs bg-emerald-600 hover:bg-emerald-500 text-white rounded-md flex items-center gap-1.5 disabled:opacity-50"
        >
          {syncing ? <RefreshCw size={12} /> : <RefreshCw size={12} />}
          {syncing ? 'Preparing...' : 'Prepare snapshot'}
        </button>
        <div className="w-48 h-2 bg-zinc-800 border border-zinc-700 rounded overflow-hidden">
          <div
            className="h-full bg-emerald-500 transition-all duration-300"
            style={{ width: `${syncProgress}%` }}
          />
        </div>
      </div>
    </div>
  )
}
