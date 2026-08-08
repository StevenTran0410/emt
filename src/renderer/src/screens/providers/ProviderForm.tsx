import { useState, useEffect } from 'react'
import { Button, useToastStore } from '../../components/ui'
import { Loader2, ChevronDown, Wifi, Eye, EyeOff } from 'lucide-react'
import { useProviderStore } from '../../store/provider.store'
import { CLOUD_KINDS } from '../../types/constants'
import type { UpdateProviderRequest, CreateProviderRequest, OpenRouterEndpoint } from '../../types/electron'
import { CLOUD_MODEL_PRESETS, KIND_DEFAULTS } from './constants'
import { KindLabel, PrivacyBadge, TestStatus } from './StatusBadges'
import { ModelPicker } from './ModelPicker'
import type { ProviderFormProps } from './types'

export function ProviderForm({ kind, initial, onClose }: ProviderFormProps) {
  const { create, update, testConnection, fetchModels, saveManualModels, testing, testResults, modelLists, loadingModels, modelErrors } = useProviderStore()
  const toast = useToastStore()

  const isEdit = !!initial
  const defaults = KIND_DEFAULTS[kind]
  const isCloud = CLOUD_KINDS.has(kind)
  const [name, setName] = useState(initial?.display_name ?? defaults.display_name)
  const [url, setUrl] = useState(initial?.base_url ?? defaults.base_url)
  const [modelId, setModelId] = useState(initial?.model_id ?? defaults.model_id ?? '')
  const [apiKey, setApiKey] = useState('')
  const [showKey, setShowKey] = useState(false)
  const [saving, setSaving] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [showModelPicker, setShowModelPicker] = useState(false)
  const [modelSearch, setModelSearch] = useState('')
  const hasExistingKey = initial?.extra?.has_api_key === true

  const tempId = initial?.id ?? '__new__'
  const testResult = testResults[tempId]
  const isTesting = testing[tempId] ?? false
  const models = (modelLists[tempId] ?? []).map((m) => m.id)
  const isLoadingModels = loadingModels[tempId] ?? false
  const modelFetchError = modelErrors[tempId] ?? ''

  const [manualText, setManualText] = useState('')
  const [showManualUI, setShowManualUI] = useState(false)
  const [manualSaveError, setManualSaveError] = useState<string | null>(null)

  const [openrouterEndpoints, setOpenrouterEndpoints] = useState<OpenRouterEndpoint[]>([])
  const [loadingEndpoints, setLoadingEndpoints] = useState(false)
  const [openrouterProvider, setOpenrouterProvider] = useState<string>(
    (initial?.extra?.openrouter_provider as string) || ''
  )

  useEffect(() => {
    if (kind === 'openrouter' && isEdit && initial?.id) {
      setLoadingEndpoints(true)
      window.api.provider
        .endpoints(initial.id)
        .then((res) => {
          const sorted = (res.endpoints || []).slice().sort((a, b) => {
            const pa = parseFloat(a.prompt_price || '0')
            const pb = parseFloat(b.prompt_price || '0')
            return pa - pb
          })
          setOpenrouterEndpoints(sorted)
        })
        .catch((err) => {
          console.error('Failed to fetch OpenRouter endpoints:', err)
        })
        .finally(() => {
          setLoadingEndpoints(false)
        })
    }
  }, [kind, isEdit, initial?.id, modelId])

  useEffect(() => {
    if (modelFetchError) {
      setShowManualUI(true)
    }
  }, [modelFetchError])

  const handleSaveManualModels = async () => {
    setManualSaveError(null)
    if (!initial) return
    const parsed = manualText
      .split(/[,\n]/)
      .map((s) => s.trim())
      .filter(Boolean)
    const deduped = [...new Set(parsed)]
    if (deduped.length === 0) {
      setManualSaveError('Please enter at least one model ID')
      return
    }
    try {
      await saveManualModels(initial.id, deduped)
      setShowManualUI(false)
      setManualText('')
    } catch (err) {
      setManualSaveError(String(err))
    }
  }

  const handleSave = async () => {
    setFormError(null)
    if (!name.trim()) { setFormError('Display name is required'); return }
    if (!url.trim()) { setFormError('Base URL is required'); return }
    if (!modelId.trim()) { setFormError('Model ID is required'); return }
    if (isCloud && !isEdit && !apiKey.trim()) { setFormError('API key is required for cloud providers'); return }

    setSaving(true)
    try {
      if (isEdit && initial) {
        // Send only the delta we own. The backend MERGES extra (never deletes keys) and keeps
        // api_key/manual_models, so an empty openrouter_provider ('') is what unpins back to Auto —
        // deleting the key would leave the old pin in place. Spreading initial.extra would also
        // leak the masked has_api_key flag into stored extra.
        const req: UpdateProviderRequest = {
          display_name: name, base_url: url, model_id: modelId,
          ...(kind === 'openrouter' ? { extra: { openrouter_provider: openrouterProvider } } : {}),
          ...(apiKey.trim() ? { api_key: apiKey.trim() } : {})
        }
        await update(initial.id, req)
      } else {
        const extraPayload: Record<string, any> = {}
        if (kind === 'openrouter' && openrouterProvider) {
          extraPayload.openrouter_provider = openrouterProvider
        }
        const req: CreateProviderRequest = {
          kind, display_name: name, base_url: url, model_id: modelId,
          ...(Object.keys(extraPayload).length > 0 ? { extra: extraPayload } : {}),
          ...(apiKey.trim() ? { api_key: apiKey.trim() } : {})
        }
        await create(req)
      }
      toast.success('Provider saved')
      onClose()
    } catch (err) {
      setFormError(String(err))
    } finally {
      setSaving(false)
    }
  }

  const handleTest = async () => {
    if (!initial) return
    // Test connection must exercise the CURRENT form values, not whatever was last
    // persisted — save first so a not-yet-saved base URL/key/model is what gets tested.
    setFormError(null)
    if (!name.trim()) { setFormError('Display name is required'); return }
    if (!url.trim()) { setFormError('Base URL is required'); return }
    if (!modelId.trim()) { setFormError('Model ID is required'); return }

    setSaving(true)
    try {
      const req: UpdateProviderRequest = {
        display_name: name, base_url: url, model_id: modelId,
        ...(apiKey.trim() ? { api_key: apiKey.trim() } : {})
      }
      await update(initial.id, req)
    } catch (err) {
      setFormError(String(err))
      setSaving(false)
      return
    }
    setSaving(false)
    await testConnection(initial.id)
  }

  const handleFetchModels = async () => {
    setShowModelPicker(true)
    if (!initial) {
      // For cloud providers before save: show presets directly
      return
    }
    // Same as Test connection: browsing must query the CURRENT base URL/key, not
    // whatever was last saved — persist first (model_id intentionally omitted, the
    // user may be Browsing precisely to pick one).
    setFormError(null)
    if (!name.trim()) { setFormError('Display name is required'); return }
    if (!url.trim()) { setFormError('Base URL is required'); return }
    try {
      const req: UpdateProviderRequest = {
        display_name: name, base_url: url,
        ...(apiKey.trim() ? { api_key: apiKey.trim() } : {})
      }
      await update(initial.id, req)
    } catch (err) {
      setFormError(String(err))
      return
    }
    await fetchModels(initial.id)
  }

  // Cloud providers can show presets even before saving
  const cloudPresets = CLOUD_MODEL_PRESETS[kind] ?? []
  const displayModels = models.length > 0 ? models : (isCloud ? cloudPresets : [])
  const q = modelSearch.trim().toLowerCase()
  const filteredModels = q ? displayModels.filter((m) => m.toLowerCase().includes(q)) : displayModels

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 mb-1">
        <KindLabel kind={kind} />
        <PrivacyBadge kind={kind} />
      </div>

      <div>
        <label className="block text-xs font-medium text-zinc-400 mb-1">Display name</label>
        <input
          className="w-full bg-zinc-800 border border-zinc-700 rounded-md px-3 py-2 text-sm text-zinc-100 focus:outline-none focus:border-violet-500 transition-colors"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Ollama (local)"
        />
      </div>

      <div>
        <label className="block text-xs font-medium text-zinc-400 mb-1">Base URL</label>
        <input
          className="w-full bg-zinc-800 border border-zinc-700 rounded-md px-3 py-2 text-sm text-zinc-100 font-mono focus:outline-none focus:border-violet-500 transition-colors"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="http://localhost:11434"
        />
      </div>

      {isCloud && (
        <div>
          <label className="block text-xs font-medium text-zinc-400 mb-1">
            API Key {hasExistingKey && <span className="text-zinc-500 font-normal">(key saved — leave blank to keep)</span>}
          </label>
          <div className="relative">
            <input
              type={showKey ? 'text' : 'password'}
              className="w-full bg-zinc-800 border border-zinc-700 rounded-md px-3 py-2 pr-9 text-sm text-zinc-100 font-mono focus:outline-none focus:border-amber-500/60 transition-colors"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={hasExistingKey ? '••••••••••••••••' : 'sk-...'}
              autoComplete="off"
            />
            <button
              type="button"
              onClick={() => setShowKey((v) => !v)}
              className="absolute right-2.5 top-1/2 -translate-y-1/2 text-zinc-500 hover:text-zinc-300 transition-colors"
              tabIndex={-1}
            >
              {showKey ? <EyeOff size={14} /> : <Eye size={14} />}
            </button>
          </div>
          <p className="mt-1 text-xs text-zinc-600">Stored locally. Never sent anywhere except the provider's API.</p>
        </div>
      )}

      <div>
        <label className="block text-xs font-medium text-zinc-400 mb-1">Model ID</label>
        <div className="flex gap-2">
          <input
            className="flex-1 bg-zinc-800 border border-zinc-700 rounded-md px-3 py-2 text-sm text-zinc-100 font-mono focus:outline-none focus:border-violet-500 transition-colors"
            value={modelId}
            onChange={(e) => setModelId(e.target.value)}
            placeholder="e.g. llama3.2:latest"
          />
          {isEdit && (
            <button
              onClick={handleFetchModels}
              disabled={isLoadingModels}
              className="flex items-center gap-1.5 px-3 py-2 text-xs bg-zinc-700 hover:bg-zinc-600 text-zinc-300 rounded-md transition-colors disabled:opacity-50"
              title="Browse available models"
            >
              {isLoadingModels ? <Loader2 size={13} className="animate-spin" /> : <ChevronDown size={13} />}
              Browse
            </button>
          )}
        </div>
        {!isEdit && (
          <p className="mt-1 text-xs text-zinc-500">Save first, then browse available models via "Test & Browse".</p>
        )}
      </div>

      {kind === 'openrouter' && isEdit && (
        <div>
          <label className="block text-xs font-medium text-zinc-400 mb-1">
            Provider routing {loadingEndpoints && <span className="text-zinc-500 font-normal">(fetching upstreams…)</span>}
          </label>
          <select
            className="w-full bg-zinc-800 border border-zinc-700 rounded-md px-3 py-2 text-sm text-zinc-100 font-mono focus:outline-none focus:border-violet-500 transition-colors"
            value={openrouterProvider}
            onChange={(e) => setOpenrouterProvider(e.target.value)}
          >
            <option value="">Auto (OpenRouter decides)</option>
            {openrouterEndpoints.map((ep) => {
              const pricePer1M = (parseFloat(ep.prompt_price || '0') * 1e6).toFixed(3)
              return (
                <option key={ep.slug} value={ep.slug}>
                  {ep.provider_name} — ${pricePer1M}/1M in
                </option>
              )
            })}
          </select>
          <p className="mt-1 text-xs text-zinc-500">
            Pin requests to a specific upstream host (e.g. DeepSeek/DeepInfra) to lock pricing & maximize prompt caching.
          </p>
        </div>
      )}

      {showModelPicker && (
        <ModelPicker
          kind={kind}
          displayModels={displayModels}
          filteredModels={filteredModels}
          modelSearch={modelSearch}
          setModelSearch={setModelSearch}
          modelId={modelId}
          setModelId={setModelId}
          setShowModelPicker={setShowModelPicker}
          isLoadingModels={isLoadingModels}
          modelFetchError={modelFetchError}
          showManualUI={showManualUI}
          setShowManualUI={setShowManualUI}
          manualText={manualText}
          setManualText={setManualText}
          manualSaveError={manualSaveError}
          onSaveManualModels={handleSaveManualModels}
        />
      )}

      {/* Test connection only available in edit mode — new providers have no persisted ID
          to route the test call against until after the first save. */}
      {isEdit && (
        <div className="space-y-2">
          <button
            onClick={handleTest}
            disabled={isTesting || saving}
            className="flex items-center gap-2 px-3 py-2 text-xs bg-zinc-700 hover:bg-zinc-600 text-zinc-300 rounded-md transition-colors disabled:opacity-50"
          >
            {isTesting || saving ? <Loader2 size={13} className="animate-spin" /> : <Wifi size={13} />}
            Test connection
          </button>
          {testResult && <TestStatus ok={testResult.ok} message={testResult.message} />}
        </div>
      )}

      {formError && (
        <p className="text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded px-3 py-2">{formError}</p>
      )}

      <div className="flex gap-2 justify-end pt-2">
        <Button variant="secondary" onClick={onClose}>
          Cancel
        </Button>
        <Button
          variant="primary"
          onClick={handleSave}
          disabled={saving}
          loading={saving}
        >
          {isEdit ? 'Save changes' : 'Add provider'}
        </Button>
      </div>
    </div>
  )
}
