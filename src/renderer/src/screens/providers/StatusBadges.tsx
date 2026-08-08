import { CheckCircle2, XCircle, AlertTriangle, Shield, Cloud } from 'lucide-react'
import { Badge } from '../../components/ui'
import type { TestResult } from '../../store/provider.store'
import { CLOUD_KINDS } from '../../types/constants'
import type { ProviderKind } from '../../types/electron'

export function LocalBadge() {
  return (
    <div className="inline-flex items-center gap-1">
      <Shield size={10} />
      <Badge variant="success">Strict Local</Badge>
    </div>
  )
}

export function CloudBadge() {
  return (
    <div className="inline-flex items-center gap-1">
      <Cloud size={10} />
      <Badge variant="warning">BYOK Cloud</Badge>
    </div>
  )
}

export function PrivacyBadge({ kind }: { kind: string }) {
  return CLOUD_KINDS.has(kind as ProviderKind) ? <CloudBadge /> : <LocalBadge />
}

export function KindLabel({ kind }: { kind: string }) {
  const labels: Record<string, string> = {
    ollama: 'Ollama',
    lmstudio: 'LM Studio',
    openai: 'OpenAI',
    anthropic: 'Anthropic',
    gemini: 'Gemini',
    deepseek: 'DeepSeek',
    openrouter: 'OpenRouter',
  }
  const colors: Record<string, string> = {
    ollama: 'text-violet-400 bg-violet-500/10 border-violet-500/20',
    lmstudio: 'text-sky-400 bg-sky-500/10 border-sky-500/20',
    openai: 'text-green-400 bg-green-500/10 border-green-500/20',
    anthropic: 'text-orange-400 bg-orange-500/10 border-orange-500/20',
    gemini: 'text-blue-400 bg-blue-500/10 border-blue-500/20',
    deepseek: 'text-indigo-400 bg-indigo-500/10 border-indigo-500/20',
    openrouter: 'text-cyan-400 bg-cyan-500/10 border-cyan-500/20',
  }
  const label = labels[kind] ?? kind
  const color = colors[kind] ?? 'text-zinc-400 bg-zinc-500/10 border-zinc-500/20'
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-mono border ${color}`}>
      {label}
    </span>
  )
}

export function TestStatus({ ok, message, warning }: TestResult) {
  if (ok && warning) {
    return (
      <div className="space-y-1">
        <div className="flex items-center gap-2 text-xs rounded px-3 py-2 bg-amber-500/10 text-amber-400">
          <AlertTriangle size={13} className="shrink-0" />
          <span>{message}</span>
        </div>
        <div className="flex items-start gap-2 text-xs rounded px-3 py-2 bg-zinc-800 text-zinc-400">
          <AlertTriangle size={12} className="shrink-0 mt-0.5 text-amber-500/60" />
          <span>{warning}</span>
        </div>
      </div>
    )
  }
  return (
    <div className={`flex items-center gap-2 text-xs rounded px-3 py-2 ${ok ? 'bg-emerald-500/10 text-emerald-400' : 'bg-red-500/10 text-red-400'}`}>
      {ok ? <CheckCircle2 size={13} /> : <XCircle size={13} />}
      <span>{message}</span>
    </div>
  )
}
