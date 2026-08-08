import { XCircle } from 'lucide-react'

interface ModelPickerProps {
  kind: string
  displayModels: string[]
  filteredModels: string[]
  modelSearch: string
  setModelSearch: (v: string) => void
  modelId: string
  setModelId: (v: string) => void
  setShowModelPicker: (v: boolean) => void
  isLoadingModels: boolean
  modelFetchError: string
  showManualUI: boolean
  setShowManualUI: (v: boolean) => void
  manualText: string
  setManualText: (v: string) => void
  manualSaveError: string | null
  onSaveManualModels: () => void
}

// Renders the model Browse dropdown plus the manual-entry fallback UI. Assumes the caller
// already gates rendering on `showModelPicker` — mirrors the original inline conditionals.
export function ModelPicker({
  kind,
  displayModels,
  filteredModels,
  modelSearch,
  setModelSearch,
  modelId,
  setModelId,
  setShowModelPicker,
  isLoadingModels,
  modelFetchError,
  showManualUI,
  setShowManualUI,
  manualText,
  setManualText,
  manualSaveError,
  onSaveManualModels,
}: ModelPickerProps) {
  return (
    <>
      {displayModels.length > 0 && (
        <div className="border border-zinc-700 rounded-md bg-zinc-800 overflow-hidden">
          {displayModels.length > 8 && (
            <div className="p-1.5 border-b border-zinc-700 bg-zinc-800 sticky top-0">
              <input
                autoFocus
                value={modelSearch}
                onChange={(e) => setModelSearch(e.target.value)}
                placeholder={`Search ${displayModels.length} models…`}
                className="w-full bg-zinc-900 border border-zinc-700 rounded px-2.5 py-1.5 text-xs text-zinc-100 focus:outline-none focus:border-violet-500 transition-colors"
              />
            </div>
          )}
          <div className="divide-y divide-zinc-700 max-h-48 overflow-y-auto">
            {filteredModels.map((m) => {
              // LM Studio model IDs can be very long paths — show filename only as label
              const label = m.includes('/') ? m.split('/').pop()! : m
              const isCurrent = modelId === m
              return (
                <button
                  key={m}
                  onClick={() => { setModelId(m); setShowModelPicker(false); setModelSearch('') }}
                  className={`w-full text-left px-3 py-2 hover:bg-zinc-700 transition-colors ${isCurrent ? 'bg-zinc-700/50' : ''}`}
                >
                  <div className={`text-sm font-mono truncate ${isCurrent ? 'text-violet-400' : 'text-zinc-300'}`}>{label}</div>
                  {label !== m && <div className="text-[10px] text-zinc-600 truncate font-mono mt-0.5">{m}</div>}
                </button>
              )
            })}
            {filteredModels.length === 0 && (
              <div className="px-3 py-3 text-xs text-zinc-500">No model matches "{modelSearch}".</div>
            )}
          </div>
        </div>
      )}
      {modelFetchError && (
        <div className="space-y-2">
          <div className="flex items-start gap-2 border border-red-500/20 rounded-md bg-red-500/5 px-3 py-3 text-xs text-red-400">
            <XCircle size={13} className="shrink-0 mt-0.5" />
            <span>{modelFetchError}</span>
          </div>
          {showManualUI && (
            <div className="border border-zinc-700 rounded-md bg-zinc-800 p-3 space-y-2">
              <label className="block text-[11px] font-medium text-zinc-400">
                Or enter model list manually:
              </label>
              <textarea
                className="w-full bg-zinc-900 border border-zinc-700 rounded px-2 py-1.5 text-xs text-zinc-100 font-mono focus:outline-none focus:border-violet-500"
                rows={3}
                value={manualText}
                onChange={(e) => setManualText(e.target.value)}
                placeholder="GPT-5.4, GPT-5.4-mini&#10;or one per line"
              />
              {manualSaveError && (
                <p className="text-[10px] text-red-400">{manualSaveError}</p>
              )}
              <div className="flex gap-2 justify-end">
                <button
                  type="button"
                  onClick={() => setShowManualUI(false)}
                  className="px-2.5 py-1 text-[11px] bg-zinc-700 hover:bg-zinc-600 text-zinc-300 rounded transition-colors"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={onSaveManualModels}
                  className="px-2.5 py-1 text-[11px] bg-violet-600 hover:bg-violet-500 text-zinc-100 rounded font-medium transition-colors"
                >
                  Save models
                </button>
              </div>
            </div>
          )}
          {!showManualUI && (
            <button
              type="button"
              onClick={() => setShowManualUI(true)}
              className="text-[11px] text-violet-400 hover:text-violet-300 transition-colors font-medium underline"
            >
              Configure model list manually
            </button>
          )}
        </div>
      )}
      {displayModels.length === 0 && !isLoadingModels && !modelFetchError && (
        <div className="border border-zinc-700 rounded-md bg-zinc-800 px-3 py-3 text-xs text-zinc-500">
          No models found.{kind === 'ollama' ? ' Run `ollama pull <model>` to download one.' : ' Load a model in LM Studio first.'}
        </div>
      )}
    </>
  )
}
