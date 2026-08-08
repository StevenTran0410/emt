import { useState, useEffect } from 'react'
import { ErrorBanner } from '../../components/ui/ErrorBanner'
import { Plus, Loader2, Cloud } from 'lucide-react'
import { Button } from '../../components/ui'
import { ConsentBanner } from '../../components/providers/ConsentBanner'
import { useProviderStore } from '../../store/provider.store'
import { LMStudioSetupGuide } from './LMStudioSetupGuide'
import { ProviderForm } from './ProviderForm'
import { ProviderCard } from './ProviderCard'
import { CloudSection } from './CloudSection'
import { useConsentGate } from './useConsentGate'
import type { AddKind } from './types'

export default function ProvidersScreen() {
  const { providers, loading, error, load, clearError } = useProviderStore()
  const [adding, setAdding] = useState<AddKind>(null)
  const { showConsent, handleAddCloud, handleConsentAccept, dismissConsent } = useConsentGate(setAdding)

  useEffect(() => { load() }, [load])

  const ollamaProviders = providers.filter((p) => p.kind === 'ollama')
  const lmStudioProviders = providers.filter((p) => p.kind === 'lmstudio')
  const openaiProviders = providers.filter((p) => p.kind === 'openai')
  const anthropicProviders = providers.filter((p) => p.kind === 'anthropic')
  const geminiProviders = providers.filter((p) => p.kind === 'gemini')
  const deepseekProviders = providers.filter((p) => p.kind === 'deepseek')
  const openrouterProviders = providers.filter((p) => p.kind === 'openrouter')

  return (
    <div className="flex flex-col h-full">
      {showConsent && (
        <ConsentBanner
          onAccept={handleConsentAccept}
          onDismiss={dismissConsent}
        />
      )}
      <div className="shrink-0 px-8 pt-8 pb-4 border-b border-zinc-800">
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-xl font-semibold text-zinc-100">Model Providers</h1>
            <p className="mt-1 text-sm text-zinc-500">
              Connect local AI models to power analysis. All local providers keep your code on-device.
            </p>
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-8 py-6 space-y-8">
        {error && <ErrorBanner message={error} onDismiss={clearError} />}

        {loading && (
          <div className="flex items-center gap-2 text-zinc-500 text-sm">
            <Loader2 size={16} className="animate-spin" />
            Loading providers...
          </div>
        )}

        <section>
          <div className="flex items-center justify-between mb-3">
            <div>
              <h2 className="text-sm font-semibold text-zinc-300">Ollama</h2>
              <p className="text-xs text-zinc-500 mt-0.5">Run open-source models locally via Ollama's REST API</p>
            </div>
            {adding !== 'ollama' && (
              <Button variant="secondary" size="sm" onClick={() => setAdding('ollama')}>
                <Plus size={13} />
                Add Ollama
              </Button>
            )}
          </div>

          <div className="space-y-3">
            {ollamaProviders.map((p) => <ProviderCard key={p.id} config={p} />)}

            {adding === 'ollama' && (
              <div className="bg-zinc-800/60 border border-violet-500/30 rounded-xl p-5">
                <ProviderForm kind="ollama" onClose={() => setAdding(null)} />
              </div>
            )}

            {ollamaProviders.length === 0 && adding !== 'ollama' && (
              <div className="border border-dashed border-zinc-700 rounded-xl p-6 text-center">
                <p className="text-sm text-zinc-500">No Ollama providers configured yet.</p>
                <button
                  onClick={() => setAdding('ollama')}
                  className="mt-3 text-xs text-violet-400 hover:text-violet-300 underline transition-colors"
                >
                  Add your first Ollama provider
                </button>
              </div>
            )}
          </div>
        </section>

        <section>
          <div className="flex items-center justify-between mb-3">
            <div>
              <h2 className="text-sm font-semibold text-zinc-300">LM Studio</h2>
              <p className="text-xs text-zinc-500 mt-0.5">OpenAI-compatible local server via LM Studio</p>
            </div>
            {adding !== 'lmstudio' && (
              <Button variant="secondary" size="sm" onClick={() => setAdding('lmstudio')}>
                <Plus size={13} />
                Add LM Studio
              </Button>
            )}
          </div>

          <div className="space-y-3">
            <LMStudioSetupGuide />
            {lmStudioProviders.map((p) => <ProviderCard key={p.id} config={p} />)}

            {adding === 'lmstudio' && (
              <div className="bg-zinc-800/60 border border-sky-500/30 rounded-xl p-5">
                <ProviderForm kind="lmstudio" onClose={() => setAdding(null)} />
              </div>
            )}

            {lmStudioProviders.length === 0 && adding !== 'lmstudio' && (
              <div className="border border-dashed border-zinc-700 rounded-xl p-6 text-center">
                <p className="text-sm text-zinc-500">No LM Studio providers configured yet.</p>
                <button
                  onClick={() => setAdding('lmstudio')}
                  className="mt-3 text-xs text-sky-400 hover:text-sky-300 underline transition-colors"
                >
                  Add your first LM Studio provider
                </button>
              </div>
            )}
          </div>
        </section>

        <div className="flex items-center gap-3 pt-2">
          <div className="flex-1 h-px bg-zinc-800" />
          <div className="flex items-center gap-1.5 text-xs text-zinc-500 font-medium">
            <Cloud size={11} />
            Cloud Providers (BYOK)
          </div>
          <div className="flex-1 h-px bg-zinc-800" />
        </div>
        <p className="text-xs text-zinc-600">
          Bring Your Own Key — code may be sent to external servers. One-time consent required.
        </p>

        <CloudSection
          kind="openai" title="OpenAI" description="GPT-4o, o3-mini, and other OpenAI models"
          providers={openaiProviders} adding={adding} onAdd={handleAddCloud} onCloseAdd={() => setAdding(null)}
        />
        <CloudSection
          kind="anthropic" title="Anthropic" description="Claude Opus, Sonnet, Haiku models"
          providers={anthropicProviders} adding={adding} onAdd={handleAddCloud} onCloseAdd={() => setAdding(null)}
        />
        <CloudSection
          kind="gemini" title="Google Gemini" description="Gemini 2.0 Flash, 1.5 Pro, and others"
          providers={geminiProviders} adding={adding} onAdd={handleAddCloud} onCloseAdd={() => setAdding(null)}
        />
        <CloudSection
          kind="deepseek" title="DeepSeek" description="DeepSeek Chat and DeepSeek Reasoner"
          providers={deepseekProviders} adding={adding} onAdd={handleAddCloud} onCloseAdd={() => setAdding(null)}
        />
        <CloudSection
          kind="openrouter" title="OpenRouter" description="Any model via OpenRouter — GPT, Claude, Gemini, DeepSeek, Qwen…"
          providers={openrouterProviders} adding={adding} onAdd={handleAddCloud} onCloseAdd={() => setAdding(null)}
        />
      </div>
    </div>
  )
}
