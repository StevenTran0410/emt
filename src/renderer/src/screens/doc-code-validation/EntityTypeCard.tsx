import React, { useState } from 'react'
import { ChevronDown, ChevronRight, CheckCircle, AlertTriangle, AlertCircle, HelpCircle, FileText } from 'lucide-react'
import { EvidenceChip } from './EvidenceChip'
import type { TypeComparisonResult } from '../../types/electron'

interface EntityTypeCardProps {
  typeResult: TypeComparisonResult
}

export function EntityTypeCard({ typeResult }: EntityTypeCardProps): React.ReactElement {
  const [isExpanded, setIsExpanded] = useState<boolean>(false)
  const [activeTab, setActiveTab] = useState<'undocumented' | 'missing' | 'unknown'>('undocumented')

  const totalComparable = typeResult.matched + typeResult.undocumented.length + typeResult.missing.length
  const matchPct = Math.round((typeResult.matched / Math.max(1, totalComparable)) * 100)

  const activeItems =
    activeTab === 'undocumented'
      ? typeResult.undocumented
      : activeTab === 'missing'
      ? typeResult.missing
      : typeResult.unknown

  return (
    <div className="bg-zinc-900/80 border border-zinc-800 rounded-xl overflow-hidden shadow-sm">
      {/* Header Bar */}
      <div
        onClick={() => setIsExpanded(!isExpanded)}
        className="px-4 py-3 bg-zinc-900 flex items-center justify-between cursor-pointer hover:bg-zinc-800/80 transition-colors"
      >
        <div className="flex items-center gap-3">
          <button type="button" className="text-zinc-400 hover:text-zinc-200">
            {isExpanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
          </button>
          <div>
            <span className="font-bold text-zinc-100 font-mono text-sm uppercase">{typeResult.type}</span>
            <span className="text-xs text-zinc-400 ml-2">
              (Doc: <strong className="text-zinc-200">{typeResult.doc_count}</strong> · Code:{' '}
              <strong className="text-zinc-200">{typeResult.code_count}</strong>)
            </span>
          </div>
        </div>

        <div className="flex items-center gap-3">
          {/* Progress bar */}
          <div className="hidden sm:flex items-center gap-2 w-36">
            <div className="flex-1 bg-zinc-950 rounded-full h-2 overflow-hidden border border-zinc-800">
              <div
                className={`h-full ${
                  matchPct >= 90 ? 'bg-emerald-400' : matchPct >= 70 ? 'bg-amber-400' : 'bg-rose-400'
                }`}
                style={{ width: `${matchPct}%` }}
              />
            </div>
            <span className="text-xs font-mono font-bold text-zinc-300 w-9 text-right">{matchPct}%</span>
          </div>

          {/* Counts Badges */}
          <div className="flex items-center gap-1.5 text-xs font-mono">
            <span className="px-2 py-0.5 rounded bg-emerald-500/10 border border-emerald-500/30 text-emerald-300 font-bold">
              {typeResult.matched} Match
            </span>
            {typeResult.undocumented.length > 0 && (
              <span className="px-2 py-0.5 rounded bg-amber-500/10 border border-amber-500/30 text-amber-300 font-bold">
                {typeResult.undocumented.length} Undoc
              </span>
            )}
            {typeResult.missing.length > 0 && (
              <span className="px-2 py-0.5 rounded bg-rose-500/10 border border-rose-500/30 text-rose-300 font-bold">
                {typeResult.missing.length} Missing
              </span>
            )}
            {typeResult.unknown.length > 0 && (
              <span className="px-2 py-0.5 rounded bg-zinc-500/10 border border-zinc-500/30 text-zinc-400 font-bold">
                {typeResult.unknown.length} Unk
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Expanded Tab View */}
      {isExpanded && (
        <div className="border-t border-zinc-800 p-4 bg-zinc-950/50 space-y-3">
          {/* Tabs */}
          <div className="flex items-center gap-2 border-b border-zinc-800 pb-2">
            <button
              onClick={() => setActiveTab('undocumented')}
              className={`px-3 py-1 rounded text-xs font-medium transition-colors flex items-center gap-1.5 ${
                activeTab === 'undocumented'
                  ? 'bg-amber-500/20 text-amber-300 border border-amber-500/40'
                  : 'text-zinc-400 hover:text-zinc-200'
              }`}
            >
              <AlertTriangle className="w-3.5 h-3.5" />
              <span>Undocumented ({typeResult.undocumented.length})</span>
            </button>

            <button
              onClick={() => setActiveTab('missing')}
              className={`px-3 py-1 rounded text-xs font-medium transition-colors flex items-center gap-1.5 ${
                activeTab === 'missing'
                  ? 'bg-rose-500/20 text-rose-300 border border-rose-500/40'
                  : 'text-zinc-400 hover:text-zinc-200'
              }`}
            >
              <AlertCircle className="w-3.5 h-3.5" />
              <span>Missing ({typeResult.missing.length})</span>
            </button>

            <button
              onClick={() => setActiveTab('unknown')}
              className={`px-3 py-1 rounded text-xs font-medium transition-colors flex items-center gap-1.5 ${
                activeTab === 'unknown'
                  ? 'bg-zinc-800 text-zinc-200 border border-zinc-700'
                  : 'text-zinc-400 hover:text-zinc-200'
              }`}
            >
              <HelpCircle className="w-3.5 h-3.5" />
              <span>Unknown ({typeResult.unknown.length})</span>
            </button>
          </div>

          {/* Items list */}
          {activeItems.length === 0 ? (
            <div className="py-6 text-center text-zinc-500 italic text-xs flex items-center justify-center gap-2">
              <CheckCircle className="w-4 h-4 text-emerald-500/60" />
              <span>No {activeTab} entities in this category.</span>
            </div>
          ) : (
            <div className="space-y-2 max-h-64 overflow-y-auto pr-1">
              {activeItems.map((item: any, idx) => {
                const key = item.key || item.semantic_key || item.display_name || item.name || `item-${idx}`
                const domId = `entity:${typeResult.type}:${key}`
                const relPath = item.rel_path
                const line = item.line_start
                const provStr =
                  typeof item.provenance === 'string'
                    ? item.provenance
                    : item.provenance
                    ? JSON.stringify(item.provenance)
                    : null

                return (
                  <div
                    id={domId}
                    key={idx}
                    className="p-2.5 rounded-lg bg-zinc-900 border border-zinc-800 text-xs flex flex-col md:flex-row md:items-center justify-between gap-2 transition-colors hover:border-zinc-700"
                  >
                    <div className="space-y-1 min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="font-mono font-bold text-zinc-200 truncate">{key}</span>
                        {item.node_type && (
                          <span className="px-1.5 py-0.2 rounded text-[10px] bg-zinc-800 text-zinc-400 font-mono uppercase">
                            {item.node_type}
                          </span>
                        )}
                      </div>

                      {provStr && (
                        <div className="text-[11px] text-zinc-400 flex items-center gap-1 truncate">
                          <FileText className="w-3 h-3 text-zinc-500 shrink-0" />
                          <span className="truncate">{provStr}</span>
                        </div>
                      )}
                    </div>

                    <div className="shrink-0">
                      {relPath ? (
                        <EvidenceChip
                          kind="file"
                          label={`${relPath}${line ? `:${line}` : ''}`}
                          targetId={domId}
                        />
                      ) : (
                        <EvidenceChip kind="entity" label={key} targetId={domId} />
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
