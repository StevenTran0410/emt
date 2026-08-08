import { useEffect, useMemo, useState, useRef } from 'react'
import { useNavigate, useSearchParams, useLocation } from 'react-router-dom'
import { toErrorMessage } from '../../lib/errors'
import type {
  ClonePolicy,
  EstimateFileCountResponse,
  LocalRepo,
  RepoSnapshot,
} from '../../types/electron'
import { useLocalRepoStore } from '../../store/local-repo.store'
import { useWorkspaceStore } from '../../store/workspace.store'
import { useToastStore } from '../../components/ui'
import type { ScreenMode, SyncMode } from './types'

export function useRepositoriesScreen() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const activeWorkspaceId = useWorkspaceStore((s) => s.activeWorkspaceId)
  const { repos, loading, error, load, clearError, loadBranches, branchesMap, setBranch } = useLocalRepoStore()
  const [selectedRepoId, setSelectedRepoId] = useState<string | null>(null)
  const [branch, setBranchLocal] = useState<string>('')
  const [syncMode, setSyncMode] = useState<SyncMode>('latest')
  const [pinnedRef, setPinnedRef] = useState('')
  const [detectSubmodules, setDetectSubmodules] = useState(true)
  const [includeTests, setIncludeTests] = useState(false)
  const [ignoreText, setIgnoreText] = useState('')
  const [clonePolicy, setClonePolicy] = useState<ClonePolicy>('full')
  const [saving, setSaving] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [syncProgress, setSyncProgress] = useState(0)
  const [snapshots, setSnapshots] = useState<RepoSnapshot[]>([])
  const [selectedSnapshotId, setSelectedSnapshotId] = useState<string | null>(null)
  const [loadingSnapshots, setLoadingSnapshots] = useState(false)
  const [selectingSnapshot, setSelectingSnapshot] = useState(false)
  const [deletingSnapshot, setDeletingSnapshot] = useState(false)
  const [confirmDeleteSnapshotId, setConfirmDeleteSnapshotId] = useState<string | null>(null)
  const [screenError, setScreenError] = useState<string | null>(null)
  const [estimate, setEstimate] = useState<EstimateFileCountResponse | null>(null)
  const [estimating, setEstimating] = useState(false)
  const runtimeHasSyncApi = Boolean((window as Window).api?.sync)
  const syncTimerRef = useRef<ReturnType<typeof setTimeout>>()

  const selectedRepo = useMemo(
    () => repos.find((r) => r.id === selectedRepoId) ?? null,
    [repos, selectedRepoId],
  )

  const location = useLocation()
  const mode: ScreenMode = location.pathname.startsWith('/aeh') ? 'aeh' : 'code_analysis'

  useEffect(() => { load(activeWorkspaceId ?? undefined, mode) }, [load, activeWorkspaceId, mode])

  // AEH mode only: repos already imported under Code Analysis but not yet registered for
  // AEH — "Use for AEH" reuses the on-disk clone instead of re-cloning via Code Hosts.
  const [caReposAvailable, setCaReposAvailable] = useState<LocalRepo[]>([])
  const [activatingPath, setActivatingPath] = useState<string | null>(null)

  useEffect(() => {
    if (mode !== 'aeh' || !activeWorkspaceId) {
      setCaReposAvailable([])
      return
    }
    window.api.folder.list(activeWorkspaceId, 'code_analysis')
      .then((caRepos) => {
        const aehPaths = new Set(repos.map((r) => r.path))
        setCaReposAvailable(caRepos.filter((r) => !aehPaths.has(r.path)))
      })
      .catch(() => setCaReposAvailable([]))
  }, [mode, activeWorkspaceId, repos])

  const handleUseForAEH = async (caRepo: LocalRepo) => {
    setActivatingPath(caRepo.path)
    try {
      await window.api.folder.add(caRepo.path, activeWorkspaceId ?? undefined, 'aeh')
      await load(activeWorkspaceId ?? undefined, 'aeh')
    } catch (err) {
      setScreenError(toErrorMessage(err))
    } finally {
      setActivatingPath(null)
    }
  }

  useEffect(() => {
    if (!selectedRepoId && repos.length > 0) setSelectedRepoId(repos[0].id)
  }, [repos, selectedRepoId])

  useEffect(() => {
    const repoIdFromQuery = searchParams.get('repoId')
    if (!repoIdFromQuery) return
    if (repos.some((r) => r.id === repoIdFromQuery)) {
      setSelectedRepoId(repoIdFromQuery)
    }
  }, [repos, searchParams])

  useEffect(() => {
    if (!selectedRepo) return
    setBranchLocal(selectedRepo.selected_branch ?? selectedRepo.git_branch ?? '')
    setSyncMode(selectedRepo.sync_mode)
    setPinnedRef(selectedRepo.pinned_ref ?? '')
    setDetectSubmodules(selectedRepo.detect_submodules)
    setIncludeTests(selectedRepo.include_tests ?? false)
    setIgnoreText((selectedRepo.ignore_overrides ?? []).join('\n'))
    setEstimate(null)
  }, [selectedRepo])

  useEffect(() => {
    const run = async () => {
      if (!selectedRepoId) return
      if (!window.api.sync?.listForRepo) {
        setScreenError('Sync API is unavailable. Restart the app so preload/main handlers are reloaded.')
        return
      }
      setLoadingSnapshots(true)
      try {
        const rows = await window.api.sync.listForRepo(selectedRepoId)
        setSnapshots(rows)
        const preferred = rows.find((x) => x.id === selectedRepo?.active_snapshot_id)?.id
        setSelectedSnapshotId(preferred ?? rows[0]?.id ?? null)
      } catch (err) {
        setScreenError(toErrorMessage(err))
      } finally {
        setLoadingSnapshots(false)
      }
    }
    run()
  }, [selectedRepoId, selectedRepo?.active_snapshot_id])

  const hasSnapshot = snapshots.length > 0
  const branchChanged = !!selectedRepo && branch && branch !== (selectedRepo.selected_branch ?? selectedRepo.git_branch ?? '')
  const selectedSnapshot = useMemo(
    () => snapshots.find((s) => s.id === selectedSnapshotId) ?? null,
    [snapshots, selectedSnapshotId],
  )

  useEffect(() => () => clearTimeout(syncTimerRef.current), [])

  const handleSaveSettings = async () => {
    if (!selectedRepo) return
    setSaving(true)
    setScreenError(null)
    try {
      if (branch) await setBranch(selectedRepo.id, branch)
      const ignore_overrides = ignoreText
        .split('\n')
        .map((x) => x.trim())
        .filter(Boolean)
      await window.api.folder.updateSettings(selectedRepo.id, {
        sync_mode: syncMode,
        pinned_ref: syncMode === 'pinned' ? (pinnedRef.trim() || null) : null,
        ignore_overrides,
        detect_submodules: detectSubmodules,
        include_tests: includeTests,
      })
      await load(activeWorkspaceId ?? undefined, mode)
    } catch (err) {
      setScreenError(toErrorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  const handleEstimate = async () => {
    if (!selectedRepo) return
    setEstimating(true)
    setScreenError(null)
    try {
      const v = await window.api.folder.estimateFileCount(selectedRepo.id)
      setEstimate(v)
    } catch (err) {
      setScreenError(toErrorMessage(err))
    } finally {
      setEstimating(false)
    }
  }

  const handlePrepareSnapshot = async () => {
    if (!selectedRepo) return
    if (!window.api.sync?.prepare || !window.api.sync?.listForRepo) {
      setScreenError('Sync API is unavailable. Restart the app and try again.')
      return
    }
    setSyncing(true)
    setSyncProgress(8)
    setScreenError(null)
    const progressTimer = setInterval(() => {
      setSyncProgress((p) => (p >= 92 ? p : p + 4))
    }, 450)
    try {
      const created = await window.api.sync.prepare({
        local_repo_id: selectedRepo.id,
        branch: syncMode === 'pinned' ? (pinnedRef.trim() || branch || null) : (branch || null),
        clone_policy: clonePolicy,
      })
      setSelectedSnapshotId(created.id)

      // Poll snapshot until it reaches final status to avoid "frozen spinner" UX.
      for (let i = 0; i < 300; i += 1) {
        const rows = await window.api.sync.listForRepo(selectedRepo.id)
        setSnapshots(rows)
        const current = rows.find((s) => s.id === created.id)
        if (!current) break
        if (current.status === 'pending') setSyncProgress((p) => Math.max(p, 25))
        if (current.status === 'syncing') setSyncProgress((p) => Math.max(p, 70))
        if (current.status === 'ready') break
        if (current.status === 'failed') {
          throw new Error(current.error ?? 'Snapshot prepare failed')
        }
        await new Promise((resolve) => setTimeout(resolve, 700))
      }
      setSyncProgress(100)
    } catch (err) {
      setScreenError(toErrorMessage(err))
    } finally {
      clearInterval(progressTimer)
      syncTimerRef.current = setTimeout(() => setSyncProgress(0), 350)
      setSyncing(false)
    }
  }

  const handleSelectSnapshot = async () => {
    if (!selectedRepo || !selectedSnapshotId) return
    if (selectedSnapshot?.status !== 'ready') return
    setSelectingSnapshot(true)
    setScreenError(null)
    try {
      navigate(`/snapshot-viewer?repoId=${encodeURIComponent(selectedRepo.id)}&snapshotId=${encodeURIComponent(selectedSnapshotId)}`)
    } catch (err) {
      setScreenError(toErrorMessage(err))
    } finally {
      setSelectingSnapshot(false)
    }
  }

  const requestDeleteSnapshot = () => {
    if (!selectedRepo || !selectedSnapshotId) return
    setConfirmDeleteSnapshotId(selectedSnapshotId)
  }

  const handleConfirmDeleteSnapshot = async () => {
    if (!selectedRepo || !confirmDeleteSnapshotId) return
    setDeletingSnapshot(true)
    setScreenError(null)
    try {
      await window.api.sync.deleteSnapshot(confirmDeleteSnapshotId)
      useToastStore.getState().success('Snapshot deleted')
      const rows = await window.api.sync.listForRepo(selectedRepo.id)
      setSnapshots(rows)
      setSelectedSnapshotId(rows[0]?.id ?? null)
      await load(activeWorkspaceId ?? undefined, mode)
      setConfirmDeleteSnapshotId(null)
    } catch (err) {
      setScreenError(toErrorMessage(err))
    } finally {
      setDeletingSnapshot(false)
    }
  }

  return {
    // workspace / mode
    activeWorkspaceId,
    mode,
    runtimeHasSyncApi,

    // repos
    repos,
    loading,
    error,
    clearError,
    selectedRepoId,
    setSelectedRepoId,
    selectedRepo,
    branchesMap,
    loadBranches,

    // ca -> aeh reuse
    caReposAvailable,
    activatingPath,
    handleUseForAEH,

    // repo setup form
    branch,
    setBranchLocal,
    syncMode,
    setSyncMode,
    pinnedRef,
    setPinnedRef,
    detectSubmodules,
    setDetectSubmodules,
    includeTests,
    setIncludeTests,
    ignoreText,
    setIgnoreText,
    clonePolicy,
    setClonePolicy,
    hasSnapshot,
    branchChanged,
    estimate,
    estimating,
    saving,
    syncing,
    syncProgress,
    handleSaveSettings,
    handleEstimate,
    handlePrepareSnapshot,

    // snapshots
    snapshots,
    selectedSnapshotId,
    setSelectedSnapshotId,
    loadingSnapshots,
    selectedSnapshot,
    selectingSnapshot,
    deletingSnapshot,
    confirmDeleteSnapshotId,
    setConfirmDeleteSnapshotId,
    handleSelectSnapshot,
    requestDeleteSnapshot,
    handleConfirmDeleteSnapshot,

    // screen-level error
    screenError,
    setScreenError,
  }
}
