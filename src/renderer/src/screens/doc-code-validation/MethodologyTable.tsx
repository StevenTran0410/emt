import React from 'react'
import { CheckCircle2, HelpCircle, FileCode, Terminal, Wrench, ShieldCheck } from 'lucide-react'
import {
  METHODOLOGY_COMPLETENESS,
  METHODOLOGY_CORRECTNESS,
  type MethodologyDimension
} from './methodologyContent'

interface MethodologyTableProps {
  initialDimensionId?: string
}

export function MethodologyTable({ initialDimensionId }: MethodologyTableProps): React.ReactElement {
  return (
    <div className="space-y-6 text-xs text-zinc-300">
      {/* Dimension 1: Completeness */}
      <div className="space-y-3">
        <div className="flex items-center gap-2 text-indigo-400 font-bold text-sm border-b border-indigo-500/30 pb-2 uppercase tracking-wider">
          <CheckCircle2 className="w-4 h-4 text-indigo-400" />
          <span>Dimension 1: Entity Completeness (Panel A)</span>
        </div>

        <div className="space-y-4">
          {METHODOLOGY_COMPLETENESS.map((item) => (
            <DimensionBlock key={item.id} item={item} isHighlighted={item.id === initialDimensionId} />
          ))}
        </div>
      </div>

      {/* Dimension 2: Correctness */}
      <div className="space-y-3 pt-4">
        <div className="flex items-center gap-2 text-emerald-400 font-bold text-sm border-b border-emerald-500/30 pb-2 uppercase tracking-wider">
          <ShieldCheck className="w-4 h-4 text-emerald-400" />
          <span>Dimension 2: Structural Link Correctness (Panel B)</span>
        </div>

        <div className="space-y-4">
          {METHODOLOGY_CORRECTNESS.map((item) => (
            <DimensionBlock key={item.id} item={item} isHighlighted={item.id === initialDimensionId} />
          ))}
        </div>
      </div>
    </div>
  )
}

function DimensionBlock({
  item,
  isHighlighted
}: {
  item: MethodologyDimension
  isHighlighted?: boolean
}): React.ReactElement {
  return (
    <div
      id={item.id}
      className={`p-4 rounded-xl border transition-all ${
        isHighlighted
          ? 'bg-indigo-950/40 border-indigo-500/80 ring-2 ring-indigo-500/50'
          : 'bg-zinc-900/80 border-zinc-800 hover:border-zinc-700'
      }`}
    >
      <div className="font-bold text-zinc-100 text-sm mb-1">{item.dimension}</div>
      <div className="text-zinc-400 text-xs italic mb-3 flex items-start gap-1.5">
        <HelpCircle className="w-3.5 h-3.5 text-zinc-500 shrink-0 mt-0.5" />
        <span>{item.keyQuestion}</span>
      </div>

      <div className="space-y-2.5">
        <div>
          <span className="text-[11px] font-semibold text-zinc-400 uppercase tracking-wider block mb-0.5">
            How It Works
          </span>
          <p className="text-zinc-300 leading-relaxed">{item.howItWorks}</p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-2 bg-zinc-950/60 p-2.5 rounded-lg border border-zinc-800/80">
          <div>
            <span className="text-[10px] font-mono text-indigo-300 uppercase font-bold flex items-center gap-1 mb-1">
              <FileCode className="w-3 h-3 text-indigo-400" />
              COBOL Check Targets
            </span>
            <ul className="list-disc list-inside text-[11px] text-zinc-400 space-y-0.5">
              {item.cobolCheck.map((c, i) => (
                <li key={i}>{c}</li>
              ))}
            </ul>
          </div>
          <div>
            <span className="text-[10px] font-mono text-sky-300 uppercase font-bold flex items-center gap-1 mb-1">
              <Terminal className="w-3 h-3 text-sky-400" />
              JCL Check Targets
            </span>
            <ul className="list-disc list-inside text-[11px] text-zinc-400 space-y-0.5">
              {item.jclCheck.map((j, i) => (
                <li key={i}>{j}</li>
              ))}
            </ul>
          </div>
        </div>

        <div className="flex flex-col md:flex-row gap-3 pt-1">
          <div className="flex-1">
            <span className="text-[10px] font-mono text-amber-400 uppercase font-bold flex items-center gap-1 mb-0.5">
              <Wrench className="w-3 h-3 text-amber-400" />
              Remediation Solution
            </span>
            <p className="text-[11px] text-zinc-400">{item.solution}</p>
          </div>
          <div className="flex-1">
            <span className="text-[10px] font-mono text-emerald-400 uppercase font-bold flex items-center gap-1 mb-0.5">
              <ShieldCheck className="w-3 h-3 text-emerald-400" />
              Pass Criteria
            </span>
            <p className="text-[11px] text-zinc-400">{item.passCriteria}</p>
          </div>
        </div>
      </div>
    </div>
  )
}
