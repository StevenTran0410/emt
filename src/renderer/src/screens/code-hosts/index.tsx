import React, { useEffect, useState } from 'react'
import { FolderOpen, Plus, ArrowDownToLine } from 'lucide-react'
import { useLocalRepoStore } from '../../store/local-repo.store'
import { useWorkspaceStore } from '../../store/workspace.store'
import { ErrorBanner } from '../../components/ui/ErrorBanner'
import { LoadingRow } from '../../components/ui/LoadingRow'
import { Button, ConfirmDialog, useToastStore } from '../../components/ui'
import { AddFolderPanel } from './AddFolderPanel'
import { CloneFromUrlPanel } from './CloneFromUrlPanel'
import { LocalRepoCard } from './LocalRepoCard'
import { SshKeySettings } from './SshKeySettings'

export default function CodeHostsSetup(): React.ReactElement {
  const { repos, loading, error, load, remove, revalidate, revalidatingId, clearError } = useLocalRepoStore()
  const activeWorkspaceId = useWorkspaceStore((s) => s.activeWorkspaceId)
  const toast = useToastStore()
  const [panel, setPanel] = useState<'none' | 'folder' | 'clone'>('none')
  const [confirmRemoveId, setConfirmRemoveId] = useState<string | null>(null)
  // Code Hosts is a shared screen with no route-based mode (unlike Repositories/Ask) — the
  // user must pick CA vs AEH lineage explicitly here, or AEH repos could never be created.
  const [hostMode, setHostMode] = useState<'code_analysis' | 'aeh'>('code_analysis')

  useEffect(() => { load(activeWorkspaceId ?? undefined, hostMode) }, [load, activeWorkspaceId, hostMode])

  return (
    <>
      <div className="screen-header flex items-center justify-between">
        <div>
        <h1 className="screen-title">Code Hosts</h1>
          <p className="screen-subtitle">
            {repos.length > 0
              ? `${repos.length} repositor${repos.length !== 1 ? 'ies' : 'y'}`
              : 'Open a local folder or clone any git repository'}
          </p>
        </div>
        {panel === 'none' && (
          <div className="flex items-center gap-2">
            <Button variant="secondary" size="sm" onClick={() => setPanel('clone')}>
              <ArrowDownToLine className="w-3.5 h-3.5" />
              Clone URL
            </Button>
            <Button variant="primary" onClick={() => setPanel('folder')}>
              <Plus className="w-4 h-4" />
              Open Folder
            </Button>
          </div>
        )}
      </div>

      <div className="px-6 pt-4">
        <div className="inline-flex items-center rounded-lg border border-zinc-800 bg-zinc-900/60 p-0.5 text-xs">
          <button
            onClick={() => setHostMode('code_analysis')}
            className={`px-3 py-1.5 rounded-md font-medium transition-colors ${
              hostMode === 'code_analysis'
                ? 'bg-zinc-800 text-gray-100'
                : 'text-gray-400 hover:text-gray-200'
            }`}
          >
            Code Analysis
          </button>
          <button
            onClick={() => setHostMode('aeh')}
            className={`px-3 py-1.5 rounded-md font-medium transition-colors ${
              hostMode === 'aeh'
                ? 'bg-indigo-500/15 text-indigo-300 border border-indigo-500/30'
                : 'text-gray-400 hover:text-gray-200'
            }`}
          >
            Agentic Eval Harness
          </button>
        </div>
        <p className="text-[11px] text-gray-500 mt-1.5">
          {hostMode === 'aeh'
            ? 'Repos added here are indexed independently for AEH (test files included) — separate from any Code Analysis import of the same folder.'
            : 'Repos added here are indexed for Code Analysis (test files excluded by default).'}
        </p>
      </div>

      <div className="p-6 space-y-4 overflow-y-auto">
        {error && <ErrorBanner message={error} onDismiss={clearError} />}

        {panel === 'folder' && (
          <AddFolderPanel onClose={() => setPanel('none')} workspaceId={activeWorkspaceId ?? undefined} mode={hostMode} />
        )}

        {panel === 'clone' && (
          <CloneFromUrlPanel
            onClose={() => setPanel('none')}
            onCloned={() => { load(activeWorkspaceId ?? undefined, hostMode); setPanel('none') }}
            workspaceId={activeWorkspaceId ?? undefined}
            mode={hostMode}
          />
        )}

        {loading && <LoadingRow message="Loading repositories…" />}

        {repos.map((repo) => (
          <LocalRepoCard
            key={repo.id}
            repo={repo}
            onRevalidate={() => revalidate(repo.id)}
            isRevalidating={revalidatingId === repo.id}
            onConfirmRemove={setConfirmRemoveId}
          />
        ))}

        <ConfirmDialog
          open={confirmRemoveId !== null}
          onClose={() => setConfirmRemoveId(null)}
          onConfirm={async () => {
            if (confirmRemoveId) {
              await remove(confirmRemoveId)
              toast.success('Repository removed')
              setConfirmRemoveId(null)
            }
          }}
          title="Remove repository"
          description="This will remove the repository from CodeSpectra. Your local files will not be deleted."
          confirmLabel="Remove"
          confirmVariant="danger"
        />

        {!loading && repos.length === 0 && panel === 'none' && (
          <div className="border border-dashed border-zinc-700 rounded-xl p-10 text-center">
            <div className="w-12 h-12 rounded-xl bg-zinc-800 border border-zinc-700 flex items-center justify-center mx-auto mb-4">
              <FolderOpen size={22} className="text-zinc-500" />
            </div>
            <p className="text-sm font-medium text-zinc-300">No repositories added yet</p>
            <p className="text-xs text-zinc-600 mt-1 max-w-xs mx-auto">
              Open any local folder or clone from a git URL. Authentication uses your existing SSH keys or credential manager — no tokens needed.
            </p>
            <div className="flex items-center justify-center gap-2 mt-4">
              <Button variant="secondary" size="sm" onClick={() => setPanel('clone')}>
                <ArrowDownToLine size={13} />
                Clone URL
              </Button>
              <Button variant="primary" onClick={() => setPanel('folder')}>
                <FolderOpen size={14} />
                Open Folder
              </Button>
            </div>
          </div>
        )}

        <SshKeySettings />
      </div>
    </>
  )
}
