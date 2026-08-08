import React from 'react'
import { FolderOpen } from 'lucide-react'
import { EmptyState } from '../../components/ui/EmptyState'
import { ErrorBanner } from '../../components/ui/ErrorBanner'
import { LoadingRow } from '../../components/ui/LoadingRow'
import { CaAvailableSection } from './CaAvailableSection'
import { RepoListPanel } from './RepoListPanel'
import { RepoSetupPanel } from './RepoSetupPanel'
import { SnapshotsPanel } from './SnapshotsPanel'
import { DeleteSnapshotDialog } from './DeleteSnapshotDialog'
import { useRepositoriesScreen } from './useRepositoriesScreen'

export default function RepositoriesScreen(): React.ReactElement {
  const s = useRepositoriesScreen()

  const caAvailableSection = s.mode === 'aeh' && s.caReposAvailable.length > 0 && (
    <CaAvailableSection
      caReposAvailable={s.caReposAvailable}
      activatingPath={s.activatingPath}
      onUseForAEH={s.handleUseForAEH}
    />
  )

  return (
    <div className="flex flex-col h-full">
      <div className="screen-header">
        <h1 className="screen-title">Repositories</h1>
        <p className="screen-subtitle">Manage repository connections and snapshots</p>
      </div>
      <div className="flex-1 overflow-y-auto p-6 space-y-4">
        {s.error && <ErrorBanner message={s.error} onDismiss={s.clearError} />}
        {s.screenError && <ErrorBanner message={s.screenError} onDismiss={() => s.setScreenError(null)} />}
        {!s.runtimeHasSyncApi && (
          <ErrorBanner message="This screen needs the new sync preload bridge. Please restart CodeSpectra (or restart `npm run dev`)." />
        )}

        {!s.activeWorkspaceId ? (
          <EmptyState
            icon={<FolderOpen className="w-7 h-7" />}
            title="No workspace selected"
            description="Select or create a workspace first, then add repository connections."
          />
        ) : s.loading ? (
          <LoadingRow message="Loading repositories..." />
        ) : s.repos.length === 0 ? (
          <>
            {caAvailableSection}
            <EmptyState
              icon={<FolderOpen className="w-7 h-7" />}
              title="No repositories in this workspace"
              description={
                s.mode === 'aeh'
                  ? 'Use a repo already imported in Code Analysis above, or add a new one from Code Hosts.'
                  : 'Add repositories from Code Hosts first, then configure snapshot settings here.'
              }
            />
          </>
        ) : (
          <>
            {caAvailableSection}
            <div className="grid grid-cols-12 gap-4">
              <RepoListPanel
                repos={s.repos}
                selectedRepoId={s.selectedRepoId}
                onSelect={s.setSelectedRepoId}
              />

              <div className="col-span-8 space-y-4">
                {s.selectedRepo && (
                  <>
                    <RepoSetupPanel
                      selectedRepo={s.selectedRepo}
                      branch={s.branch}
                      setBranchLocal={s.setBranchLocal}
                      branchesMap={s.branchesMap}
                      loadBranches={s.loadBranches}
                      clonePolicy={s.clonePolicy}
                      setClonePolicy={s.setClonePolicy}
                      syncMode={s.syncMode}
                      setSyncMode={s.setSyncMode}
                      pinnedRef={s.pinnedRef}
                      setPinnedRef={s.setPinnedRef}
                      ignoreText={s.ignoreText}
                      setIgnoreText={s.setIgnoreText}
                      detectSubmodules={s.detectSubmodules}
                      setDetectSubmodules={s.setDetectSubmodules}
                      includeTests={s.includeTests}
                      setIncludeTests={s.setIncludeTests}
                      hasSnapshot={s.hasSnapshot}
                      branchChanged={s.branchChanged}
                      estimate={s.estimate}
                      saving={s.saving}
                      estimating={s.estimating}
                      syncing={s.syncing}
                      syncProgress={s.syncProgress}
                      onSave={s.handleSaveSettings}
                      onEstimate={s.handleEstimate}
                      onPrepareSnapshot={s.handlePrepareSnapshot}
                    />

                    <SnapshotsPanel
                      selectedRepo={s.selectedRepo}
                      snapshots={s.snapshots}
                      selectedSnapshotId={s.selectedSnapshotId}
                      setSelectedSnapshotId={s.setSelectedSnapshotId}
                      loadingSnapshots={s.loadingSnapshots}
                      selectedSnapshot={s.selectedSnapshot}
                      selectingSnapshot={s.selectingSnapshot}
                      deletingSnapshot={s.deletingSnapshot}
                      onSelectSnapshot={s.handleSelectSnapshot}
                      onRequestDelete={s.requestDeleteSnapshot}
                    />
                  </>
                )}
              </div>
            </div>
          </>
        )}
      </div>
      <DeleteSnapshotDialog
        open={s.confirmDeleteSnapshotId !== null}
        onClose={() => s.setConfirmDeleteSnapshotId(null)}
        onConfirm={s.handleConfirmDeleteSnapshot}
        loading={s.deletingSnapshot}
      />
    </div>
  )
}
