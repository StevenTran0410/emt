import { useState } from 'react'
import { Trash2, RefreshCw, Loader2, Pencil } from 'lucide-react'
import { ConfirmDialog, useToastStore } from '../../components/ui'
import { useProviderStore } from '../../store/provider.store'
import type { ProviderConfig } from '../../types/electron'
import { KindLabel, PrivacyBadge, TestStatus } from './StatusBadges'
import { ProviderForm } from './ProviderForm'

export function ProviderCard({ config }: { config: ProviderConfig }) {
  const { remove, testConnection, testing, testResults } = useProviderStore()
  const toast = useToastStore()
  const [editing, setEditing] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)

  const testResult = testResults[config.id]
  const isTesting = testing[config.id] ?? false

  if (editing) {
    return (
      <div className="bg-zinc-800/60 border border-zinc-700 rounded-xl p-5">
        <ProviderForm kind={config.kind} initial={config} onClose={() => setEditing(false)} />
      </div>
    )
  }

  return (
    <div className="bg-zinc-800/60 border border-zinc-700 rounded-xl p-5 space-y-3 hover:border-zinc-600 transition-colors">
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-semibold text-zinc-100 truncate">{config.display_name}</span>
            <KindLabel kind={config.kind} />
            <PrivacyBadge kind={config.kind} />
          </div>
          <p className="mt-1 text-xs text-zinc-500 font-mono truncate">{config.base_url}</p>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <button
            onClick={() => setEditing(true)}
            className="p-1.5 text-zinc-500 hover:text-zinc-300 hover:bg-zinc-700 rounded transition-colors"
            title="Edit"
          >
            <Pencil size={14} />
          </button>
          <button
            onClick={() => setConfirmDelete(true)}
            className="p-1.5 text-zinc-500 hover:text-red-400 hover:bg-zinc-700 rounded transition-colors"
            title="Delete"
          >
            <Trash2 size={14} />
          </button>
        </div>
      </div>

      <div className="flex items-center gap-3 flex-wrap">
        <span className="text-xs text-zinc-400">
          Model: <span className="font-mono text-zinc-300">{config.model_id || <em className="text-zinc-600">not set</em>}</span>
        </span>
        <span className="text-xs text-zinc-600">·</span>
        <span className="text-xs text-zinc-400">
          Context: <span className="text-zinc-300">{config.capabilities.max_context_tokens.toLocaleString()} tokens</span>
            </span>
      </div>

      <div className="flex items-center gap-2">
        <button
          onClick={() => testConnection(config.id)}
          disabled={isTesting}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-zinc-700 hover:bg-zinc-600 text-zinc-300 rounded-md transition-colors disabled:opacity-50"
        >
          {isTesting ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
          Test connection
        </button>
        {testResult && <TestStatus ok={testResult.ok} message={testResult.message} />}
      </div>

      <ConfirmDialog
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        onConfirm={async () => {
          await remove(config.id)
          toast.success('Provider removed')
          setConfirmDelete(false)
        }}
        title="Remove provider?"
        description={`Remove ${config.display_name}? This cannot be undone.`}
        confirmLabel="Remove"
        confirmVariant="danger"
      />
    </div>
  )
}
