import { FolderOpen, Loader2 } from 'lucide-react'
import { useLocalRepoStore } from '../../store/local-repo.store'
import { ErrorBanner } from '../../components/ui/ErrorBanner'
import { useToastStore } from '../../components/ui'
import { ValidationPreview } from './ValidationPreview'

export function AddFolderPanel({ onClose, workspaceId, mode = 'code_analysis' }: { onClose: () => void; workspaceId?: string; mode?: string }) {
  const { validate, clearValidation, validation, validating, add, adding, error } = useLocalRepoStore()

  const handlePick = async () => {
    clearValidation()
    const picked = await window.api.folder.pick()
    if (picked) await validate(picked)
  }

  const handleConfirm = async () => {
    if (!validation?.path) return
    const repo = await add(validation.path, workspaceId, mode)
    if (repo) {
      useToastStore.getState().success('Folder added')
      onClose()
    }
  }

  return (
    <div className="bg-zinc-800/60 border border-zinc-700 rounded-xl p-5 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-zinc-200">Open Local Folder</h3>
        <button onClick={onClose} className="text-xs text-zinc-500 hover:text-zinc-300 transition-colors">
          Cancel
        </button>
      </div>

      {!validation && (
        <button
          onClick={handlePick}
          disabled={validating}
          className="w-full flex items-center justify-center gap-2 py-8 border-2 border-dashed border-zinc-600 hover:border-emerald-500/40 hover:bg-emerald-500/5 rounded-xl text-sm text-zinc-400 hover:text-zinc-200 transition-all"
        >
          {validating ? (
            <><Loader2 size={16} className="animate-spin" /> Validating…</>
          ) : (
            <><FolderOpen size={16} /> Browse for folder</>
          )}
        </button>
      )}

      {error && !validation && <ErrorBanner message={error} />}

      {validation && (
        <ValidationPreview
          result={validation}
          onConfirm={handleConfirm}
          onCancel={() => { clearValidation(); onClose() }}
          adding={adding}
        />
      )}
    </div>
  )
}
