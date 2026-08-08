import { useState } from 'react'
import { Loader2 } from 'lucide-react'
import { ErrorBanner } from '../../components/ui/ErrorBanner'
import { Button, useToastStore } from '../../components/ui'
import { toErrorMessage } from '../../lib/errors'

export function CloneFromUrlPanel({ onClose, onCloned, workspaceId, mode = 'code_analysis' }: { onClose: () => void; onCloned: () => void; workspaceId?: string; mode?: string }) {
  const [url, setUrl] = useState('')
  const [cloning, setCloning] = useState(false)
  const [cloneError, setCloneError] = useState<string | null>(null)

  const repoName = url.trim()
    ? (url.trim().split('/').pop()?.replace(/\.git$/, '') ?? '')
    : ''

  const handleClone = async () => {
    const trimmed = url.trim()
    if (!trimmed) return
    setCloning(true)
    setCloneError(null)
    try {
      await window.api.folder.cloneFromUrl(trimmed, workspaceId, mode)
      useToastStore.getState().success('Repository cloned')
      onCloned()
      onClose()
    } catch (err) {
      setCloneError(toErrorMessage(err))
    } finally {
      setCloning(false)
    }
  }

  return (
    <div className="bg-zinc-800/60 border border-zinc-700 rounded-xl p-5 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-zinc-200">Clone from URL</h3>
        <button onClick={onClose} className="text-xs text-zinc-500 hover:text-zinc-300 transition-colors">
          Cancel
        </button>
      </div>

      <div className="space-y-2">
        <label className="text-xs text-zinc-500">Git URL (HTTPS or SSH)</label>
        <input
          type="text"
          value={url}
          onChange={(e) => { setUrl(e.target.value); setCloneError(null) }}
          onKeyDown={(e) => { if (e.key === 'Enter') handleClone() }}
          placeholder="https://github.com/user/repo.git  or  git@github.com:user/repo.git"
          autoFocus
          disabled={cloning}
          className="w-full px-3 py-2 bg-zinc-900 border border-zinc-700 rounded-lg text-sm text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-zinc-500 font-mono transition-colors disabled:opacity-50"
        />
      </div>

      {repoName && (
        <p className="text-xs text-zinc-500">
          Will clone into{' '}
          <code className="text-zinc-300 bg-zinc-800 px-1 py-0.5 rounded">
            ~/CodeSpectra/repos/{repoName}
          </code>
        </p>
      )}

      {cloneError && <ErrorBanner message={cloneError} onDismiss={() => setCloneError(null)} />}

      {cloning && (
        <div className="flex items-center gap-2 text-xs text-zinc-300 py-2">
          <Loader2 size={13} className="animate-spin" />
          Cloning… this may take a moment
        </div>
      )}

      <div className="flex items-center gap-2 pt-1">
        <Button
          variant="secondary"
          onClick={onClose}
          disabled={cloning}
          className="flex-1"
        >
          Cancel
        </Button>
        <Button
          variant="primary"
          onClick={handleClone}
          disabled={!url.trim() || cloning}
          loading={cloning}
          className="flex-1"
        >
          Clone
        </Button>
      </div>
    </div>
  )
}
