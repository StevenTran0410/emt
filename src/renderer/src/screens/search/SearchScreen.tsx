import React, { useEffect, useState } from 'react'
import { Search, Loader2, FileCode, Sliders, Hash, Cpu, Sparkles, Copy, Check } from 'lucide-react'
import type { LocalRepo, RepoSnapshot, RrfFusionDebugBundle, FusedRankEntry } from '../../types/electron'
import { useWorkspaceStore } from '../../store/workspace.store'
import { ErrorBanner } from '../../components/ui/ErrorBanner'
import { toErrorMessage } from '../../lib/errors'

export function SearchScreen() {
  const { workspaces, activeWorkspaceId } = useWorkspaceStore()
  const currentWorkspace = workspaces.find((w) => w.id === activeWorkspaceId) ?? null
  const [repos, setRepos] = useState<LocalRepo[]>([])
  const [selectedRepoId, setSelectedRepoId] = useState<string>('')
  const [snapshots, setSnapshots] = useState<RepoSnapshot[]>([])
  const [selectedSnapshotId, setSelectedSnapshotId] = useState<string>('')
  
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  
  const [results, setResults] = useState<RrfFusionDebugBundle | null>(null)
  const [budget, setBudget] = useState<number>(4000)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [copiedId, setCopiedId] = useState<string | null>(null)

  // Load repositories for active workspace
  useEffect(() => {
    async function loadRepos() {
      if (!currentWorkspace) return
      try {
        const repoList = await window.api.folder.list(currentWorkspace.id)
        setRepos(repoList)
        if (repoList.length > 0 && !selectedRepoId) {
          setSelectedRepoId(repoList[0].id)
        }
      } catch (err) {
        setError(toErrorMessage(err))
      }
    }
    loadRepos()
  }, [activeWorkspaceId])

  // Load snapshots for selected repo
  useEffect(() => {
    async function loadSnapshots() {
      if (!selectedRepoId) {
        setSnapshots([])
        setSelectedSnapshotId('')
        return
      }
      try {
        const snapList = await window.api.sync.listForRepo(selectedRepoId)
        setSnapshots(snapList)
        const readySnap = snapList.find((s) => s.status === 'ready') || snapList[0]
        if (readySnap) {
          setSelectedSnapshotId(readySnap.id)
        } else {
          setSelectedSnapshotId('')
        }
      } catch (err) {
        setError(toErrorMessage(err))
      }
    }
    loadSnapshots()
  }, [selectedRepoId])

  const handleSearch = async (e?: React.FormEvent) => {
    if (e) e.preventDefault()
    if (!selectedSnapshotId) {
      setError('Please select an active repository snapshot to search.')
      return
    }
    if (!query.trim()) return

    setLoading(true)
    setError(null)
    try {
      const bundle = await window.api.retrieval.retrieveRrfFusion({
        snapshot_id: selectedSnapshotId,
        query: query.trim(),
        section: 'overview' as any,
        budget: budget
      })
      setResults(bundle)
    } catch (err) {
      setError(toErrorMessage(err))
      setResults(null)
    } finally {
      setLoading(false)
    }
  }

  const copyToClipboard = (text: string, id: string) => {
    navigator.clipboard.writeText(text)
    setCopiedId(id)
    setTimeout(() => setCopiedId(null), 2000)
  }

  const finalResults: FusedRankEntry[] = results?.final || results?.fused || []

  return (
    <div className="flex flex-col h-full bg-slate-950 text-slate-100 p-6 overflow-y-auto">
      {/* Header */}
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-50 flex items-center gap-2">
            <Search className="w-6 h-6 text-indigo-400" />
            Code Retrieval Search
          </h1>
          <p className="text-sm text-slate-400 mt-1">
            Perform high-accuracy hybrid BM25 + structural graph + vector retrieval on indexed repositories.
          </p>
        </div>
      </div>

      {error && <ErrorBanner message={error} onDismiss={() => setError(null)} className="mb-4" />}

      {/* Target Repository & Snapshot Selector */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6 bg-slate-900/60 p-4 rounded-xl border border-slate-800 backdrop-blur-md">
        <div>
          <label className="block text-xs font-semibold text-slate-400 mb-1.5">Target Repository</label>
          <select
            value={selectedRepoId}
            onChange={(e) => setSelectedRepoId(e.target.value)}
            className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:ring-2 focus:ring-indigo-500"
          >
            {repos.length === 0 ? (
              <option value="">No repositories found</option>
            ) : (
              repos.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name} ({r.path})
                </option>
              ))
            )}
          </select>
        </div>

        <div>
          <label className="block text-xs font-semibold text-slate-400 mb-1.5">Snapshot Index</label>
          <select
            value={selectedSnapshotId}
            onChange={(e) => setSelectedSnapshotId(e.target.value)}
            className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            disabled={snapshots.length === 0}
          >
            {snapshots.length === 0 ? (
              <option value="">No snapshots available</option>
            ) : (
              snapshots.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.branch ? `Branch: ${s.branch}` : 'Snapshot'} ({s.status}) - {new Date(s.created_at).toLocaleString()}
                </option>
              ))
            )}
          </select>
        </div>
      </div>

      {/* Search Input Bar */}
      <form onSubmit={handleSearch} className="mb-6">
        <div className="relative flex items-center">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Type code query, symbol name, or architectural keyword (e.g. 'router handler authentication')..."
            className="w-full bg-slate-900 border border-slate-700 focus:border-indigo-500 rounded-xl pl-12 pr-32 py-3.5 text-slate-100 text-sm shadow-inner focus:outline-none focus:ring-2 focus:ring-indigo-500/20 transition-all"
          />
          <Search className="absolute left-4 w-5 h-5 text-slate-400" />
          
          <div className="absolute right-2 flex items-center gap-2">
            <button
              type="button"
              onClick={() => setShowAdvanced(!showAdvanced)}
              className={`p-2 rounded-lg text-slate-400 hover:text-slate-200 hover:bg-slate-800 transition-colors ${showAdvanced ? 'bg-slate-800 text-indigo-400' : ''}`}
              title="Advanced Settings"
            >
              <Sliders className="w-4 h-4" />
            </button>
            <button
              type="submit"
              disabled={loading || !query.trim() || !selectedSnapshotId}
              className="bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white text-xs font-semibold px-4 py-2 rounded-lg flex items-center gap-1.5 transition-all shadow-md hover:shadow-indigo-500/20"
            >
              {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
              Search
            </button>
          </div>
        </div>

        {/* Advanced Settings */}
        {showAdvanced && (
          <div className="mt-3 p-4 bg-slate-900/80 rounded-xl border border-slate-800 flex items-center gap-6 text-xs text-slate-300">
            <div>
              <label className="block text-slate-400 mb-1">Token Budget Limit</label>
              <input
                type="number"
                value={budget}
                onChange={(e) => setBudget(Number(e.target.value))}
                step={500}
                min={1000}
                max={16000}
                className="bg-slate-800 border border-slate-700 rounded px-2.5 py-1 text-slate-200 w-28 text-center"
              />
            </div>
            <div className="text-slate-400">
              Retrieval Strategy: <span className="text-indigo-400 font-semibold">RRF Multi-Signal Fusion + Structural Graph</span>
            </div>
          </div>
        )}
      </form>

      {/* Results Header */}
      {results && (
        <div className="mb-4 flex items-center justify-between text-xs text-slate-400 bg-slate-900/40 px-4 py-2.5 rounded-lg border border-slate-800">
          <div className="flex items-center gap-3">
            <span>Found <strong className="text-slate-200">{finalResults.length}</strong> ranked code chunks</span>
            {results.reranker_status && (
              <span className="flex items-center gap-1 bg-slate-800 text-slate-300 px-2 py-0.5 rounded border border-slate-700">
                <Cpu className="w-3.5 h-3.5 text-indigo-400" /> Reranker: {results.reranker_status}
              </span>
            )}
          </div>
        </div>
      )}

      {/* Results List */}
      <div className="space-y-4 flex-1">
        {loading && (
          <div className="flex flex-col items-center justify-center py-20 text-slate-400">
            <Loader2 className="w-8 h-8 animate-spin text-indigo-500 mb-3" />
            <p className="text-sm">Scoring candidates across BM25, vector embeddings, and structural graph signals...</p>
          </div>
        )}

        {!loading && results && finalResults.length === 0 && (
          <div className="text-center py-16 text-slate-500 border border-dashed border-slate-800 rounded-xl">
            <FileCode className="w-10 h-10 mx-auto mb-2 opacity-50" />
            <p className="text-sm font-medium">No code chunks matched your query.</p>
            <p className="text-xs text-slate-600 mt-1">Try broadening your search terms or choosing another snapshot.</p>
          </div>
        )}

        {!loading &&
          finalResults.map((chunk, idx) => (
            <div
              key={chunk.chunk_id || idx}
              className="bg-slate-900/80 border border-slate-800 hover:border-indigo-500/50 rounded-xl p-4 transition-all shadow-sm hover:shadow-md"
            >
              {/* Chunk Top Bar */}
              <div className="flex items-center justify-between mb-3 pb-2 border-b border-slate-800/80">
                <div className="flex items-center gap-2 overflow-hidden">
                  <span className="bg-indigo-950 text-indigo-400 border border-indigo-800/60 font-mono text-xs px-2 py-0.5 rounded font-semibold shrink-0">
                    #{idx + 1}
                  </span>
                  <span className="font-mono text-xs font-semibold text-slate-200 truncate flex items-center gap-1.5">
                    <FileCode className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                    {chunk.rel_path}
                  </span>
                </div>

                <div className="flex items-center gap-3 shrink-0 text-xs">
                  <span className="bg-slate-800 text-slate-300 border border-slate-700 px-2 py-0.5 rounded font-mono">
                    Score: {chunk.fused_score.toFixed(4)}
                  </span>
                  <button
                    onClick={() => copyToClipboard(chunk.rel_path, `path-${idx}`)}
                    className="p-1 text-slate-400 hover:text-slate-200 transition-colors"
                    title="Copy File Path"
                  >
                    {copiedId === `path-${idx}` ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                  </button>
                </div>
              </div>

              {/* Per-Signal Ranks Breakdown */}
              {chunk.per_signal_ranks && (
                <div className="flex items-center gap-2 mb-3 text-[11px] text-slate-400">
                  <span className="text-slate-500">Signal Ranks:</span>
                  {Object.entries(chunk.per_signal_ranks).map(([signal, r]) => (
                    <span key={signal} className="bg-slate-800/60 px-2 py-0.5 rounded text-slate-300 font-mono">
                      {signal}: #{r}
                    </span>
                  ))}
                </div>
              )}

              {/* Code Snippet Excerpt */}
              <div className="relative bg-slate-950 rounded-lg p-3 border border-slate-800/80 font-mono text-xs text-slate-300 overflow-x-auto">
                <pre className="whitespace-pre-wrap break-words">{chunk.excerpt}</pre>
                <button
                  onClick={() => copyToClipboard(chunk.excerpt, `snippet-${idx}`)}
                  className="absolute top-2 right-2 p-1 bg-slate-900 hover:bg-slate-800 text-slate-400 hover:text-slate-200 rounded border border-slate-700 transition-colors"
                  title="Copy Snippet"
                >
                  {copiedId === `snippet-${idx}` ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                </button>
              </div>
            </div>
          ))}
      </div>
    </div>
  )
}
