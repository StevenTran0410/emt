import React from 'react'
import { FileCode, Tag, ExternalLink } from 'lucide-react'

interface EvidenceChipProps {
  label: string
  kind?: 'entity' | 'relation' | 'file' | 'generic'
  targetId?: string
  className?: string
}

export function EvidenceChip({
  label,
  kind = 'generic',
  targetId,
  className = ''
}: EvidenceChipProps): React.ReactElement {
  const handleClick = (e: React.MouseEvent) => {
    e.stopPropagation()
    if (!targetId) return
    const targetEl = document.getElementById(targetId)
    if (targetEl) {
      targetEl.scrollIntoView({ behavior: 'smooth', block: 'center' })
      targetEl.classList.add('ring-2', 'ring-amber-400', 'bg-amber-500/10')
      setTimeout(() => {
        targetEl.classList.remove('ring-2', 'ring-amber-400', 'bg-amber-500/10')
      }, 2500)
    }
  }

  let icon = <Tag className="w-3 h-3" />
  let bgClasses = 'bg-zinc-800 text-zinc-300 border-zinc-700 hover:border-zinc-600'

  if (kind === 'file' || label.includes('.CBL') || label.includes('.JCL') || label.includes('.PRC') || label.includes(':')) {
    icon = <FileCode className="w-3 h-3 text-emerald-400" />
    bgClasses = 'bg-emerald-950/50 text-emerald-300 border-emerald-800/60 hover:bg-emerald-900/60'
  } else if (kind === 'entity') {
    icon = <Tag className="w-3 h-3 text-indigo-400" />
    bgClasses = 'bg-indigo-950/50 text-indigo-300 border-indigo-800/60 hover:bg-indigo-900/60'
  } else if (kind === 'relation') {
    icon = <ExternalLink className="w-3 h-3 text-amber-400" />
    bgClasses = 'bg-amber-950/50 text-amber-300 border-amber-800/60 hover:bg-amber-900/60'
  }

  return (
    <button
      onClick={handleClick}
      type="button"
      className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[11px] font-mono border transition-all truncate max-w-xs ${bgClasses} ${className}`}
      title={label}
    >
      {icon}
      <span className="truncate">{label}</span>
    </button>
  )
}
