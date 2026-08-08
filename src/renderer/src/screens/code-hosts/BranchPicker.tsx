import { useEffect, useRef, useState } from 'react'
import { GitBranch, Loader2, ChevronDown, Search, RotateCcw } from 'lucide-react'
import { useLocalRepoStore } from '../../store/local-repo.store'
import { Spinner } from '../../components/ui'
import type { LocalRepo } from '../../types/electron'

export function BranchPicker({ repo }: { repo: LocalRepo }) {
  const { branchesMap, loadingBranchesId, loadBranches, setBranch } = useLocalRepoStore()
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const ref = useRef<HTMLDivElement>(null)

  const branches = branchesMap[repo.id]
  // Client-side filter only — no debounce needed (no network call per keystroke)
  const filtered = (branches ?? []).filter((b) => b.toLowerCase().includes(query.trim().toLowerCase()))
  const activeBranch = repo.selected_branch ?? repo.git_branch ?? 'HEAD'
  const isLoading = loadingBranchesId === repo.id

  const handleOpen = async () => {
    // Load once for fast UX; manual refresh button handles re-fetch.
    if (!branches) await loadBranches(repo.id, false)
    setOpen((v) => !v)
  }

  const handleRefresh = async () => {
    await loadBranches(repo.id, true)
  }

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    if (open) document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  return (
    <div ref={ref} className="relative">
      <button
        onClick={handleOpen}
        className={`inline-flex items-center gap-1.5 px-2 py-1 rounded-md text-xs font-medium border transition-colors
          ${repo.selected_branch
            ? 'bg-indigo-500/10 text-indigo-400 border-indigo-500/20 hover:bg-indigo-500/20'
            : 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20 hover:bg-emerald-500/20'
          }`}
        title="Click to change analysis branch"
      >
        {isLoading ? <Spinner size="sm" /> : <GitBranch size={10} />}
        {activeBranch}
        {repo.selected_branch && repo.selected_branch !== repo.git_branch && (
          <span className="text-indigo-500/70 text-xs">≠ HEAD</span>
        )}
        <ChevronDown size={10} />
      </button>

      {open && branches && (
        <div className="absolute left-0 top-full mt-1 z-30 w-52 bg-zinc-800 border border-zinc-700 rounded-lg shadow-xl overflow-hidden">
          <div className="px-3 py-2 text-xs text-zinc-500 font-medium border-b border-zinc-700">
            Select analysis branch
          </div>
          <div className="px-2 py-2 border-b border-zinc-700">
            <div className="flex items-center gap-1.5">
              <div className="relative flex-1">
                <Search size={11} className="absolute left-2 top-1/2 -translate-y-1/2 text-zinc-600" />
                <input
                  type="text"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search branch..."
                  className="w-full pl-7 pr-2 py-1.5 bg-zinc-900 border border-zinc-700 rounded text-xs text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-zinc-500"
                />
              </div>
              <button
                onClick={handleRefresh}
                className="p-1.5 border border-zinc-700 rounded text-zinc-500 hover:text-zinc-300 hover:border-zinc-600 transition-colors"
                title="Refresh branches from remote"
              >
                {isLoading ? <Loader2 size={11} className="animate-spin" /> : <RotateCcw size={11} />}
              </button>
            </div>
          </div>
          <div className="max-h-48 overflow-y-auto">
            {filtered.map((b) => (
              <button
                key={b}
                onClick={() => { setBranch(repo.id, b); setOpen(false) }}
                className={`w-full text-left px-3 py-2 text-xs flex items-center gap-2 hover:bg-zinc-700 transition-colors
                  ${b === activeBranch ? 'text-zinc-100 font-medium' : 'text-zinc-400'}`}
              >
                <GitBranch size={11} className={b === activeBranch ? 'text-emerald-400' : 'text-zinc-600'} />
                <span className="flex-1 truncate">{b}</span>
                {b === repo.git_branch && (
                  <span className="text-zinc-600 text-xs shrink-0">HEAD</span>
                )}
                {b === activeBranch && b !== repo.git_branch && (
                  <span className="text-indigo-400 text-xs shrink-0">selected</span>
                )}
              </button>
            ))}
          </div>
          {filtered.length === 0 && (
            <div className="px-3 py-4 text-xs text-zinc-500 text-center">
              {branches.length === 0 ? 'No branches found' : 'No branch matches your search'}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
