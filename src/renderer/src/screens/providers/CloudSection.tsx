import { Plus } from 'lucide-react'
import { Button } from '../../components/ui'
import { ProviderForm } from './ProviderForm'
import { ProviderCard } from './ProviderCard'
import type { CloudSectionProps } from './types'

export function CloudSection({ kind, title, description, providers: list, adding, onAdd, onCloseAdd }: CloudSectionProps) {
  const accentColor = {
    openai: 'text-green-400 bg-green-500/10 border-green-500/20',
    anthropic: 'text-orange-400 bg-orange-500/10 border-orange-500/20',
    gemini: 'text-blue-400 bg-blue-500/10 border-blue-500/20',
    deepseek: 'text-indigo-400 bg-indigo-500/10 border-indigo-500/20',
    openrouter: 'text-cyan-400 bg-cyan-500/10 border-cyan-500/20',
  }[kind]

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <div>
          <h3 className="text-sm font-semibold text-zinc-300">{title}</h3>
          <p className="text-xs text-zinc-500 mt-0.5">{description}</p>
        </div>
        {adding !== kind && (
          <Button variant="secondary" size="sm" onClick={() => onAdd(kind)}>
            <Plus size={13} />
            Add {title}
          </Button>
        )}
      </div>
      <div className="space-y-3">
        {list.map((p) => <ProviderCard key={p.id} config={p} />)}
        {adding === kind && (
          <div className={`bg-zinc-800/60 border rounded-xl p-5 ${accentColor.split(' ')[0] === 'text-green-400' ? 'border-green-500/20' : accentColor.split(' ')[0] === 'text-orange-400' ? 'border-orange-500/20' : accentColor.split(' ')[0] === 'text-blue-400' ? 'border-blue-500/20' : 'border-indigo-500/20'}`}>
            <ProviderForm kind={kind} onClose={onCloseAdd} />
          </div>
        )}
        {list.length === 0 && adding !== kind && (
          <div className="border border-dashed border-zinc-700 rounded-xl p-5 text-center">
            <p className="text-sm text-zinc-500">No {title} providers configured.</p>
            <button onClick={() => onAdd(kind)} className="mt-2 text-xs text-amber-400 hover:text-amber-300 underline">
              Add {title}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
