import React, { useState } from 'react'
import { Network, Layers, GitCompare, ArrowRight, ShieldCheck } from 'lucide-react'
import { Button } from '../../components/ui/Button'
import { StepByStepScreen } from './StepByStepScreen'
import { ComparisonScreen } from './ComparisonScreen'

export function LinkedGraphScreen(): React.ReactElement {
  const [activeTab, setActiveTab] = useState<'landing' | 'step' | 'compare'>('landing')

  if (activeTab === 'step') {
    return (
      <div className="flex h-full w-full flex-col bg-zinc-950 text-zinc-100 overflow-hidden">
        <div className="flex items-center justify-between border-b border-zinc-800 bg-zinc-950 px-5 py-2">
          <div className="flex items-center gap-2">
            <Network className="h-4 w-4 text-cyan-400" />
            <span className="font-semibold text-xs text-zinc-200">Linked Multi-Graph</span>
            <span className="text-zinc-600">/</span>
            <span className="text-xs text-amber-400 font-medium">Step-by-Step View</span>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setActiveTab('landing')}
              className="rounded px-2.5 py-1 text-xs text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200"
            >
              Overview
            </button>
            <button
              onClick={() => setActiveTab('compare')}
              className="rounded px-2.5 py-1 text-xs text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200"
            >
              Comparison View
            </button>
          </div>
        </div>
        <div className="flex-1 overflow-hidden">
          <StepByStepScreen />
        </div>
      </div>
    )
  }

  if (activeTab === 'compare') {
    return (
      <div className="flex h-full w-full flex-col bg-zinc-950 text-zinc-100 overflow-hidden">
        <div className="flex items-center justify-between border-b border-zinc-800 bg-zinc-950 px-5 py-2">
          <div className="flex items-center gap-2">
            <Network className="h-4 w-4 text-cyan-400" />
            <span className="font-semibold text-xs text-zinc-200">Linked Multi-Graph</span>
            <span className="text-zinc-600">/</span>
            <span className="text-xs text-cyan-400 font-medium">Comparison View</span>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setActiveTab('landing')}
              className="rounded px-2.5 py-1 text-xs text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200"
            >
              Overview
            </button>
            <button
              onClick={() => setActiveTab('step')}
              className="rounded px-2.5 py-1 text-xs text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200"
            >
              Step-by-Step View
            </button>
          </div>
        </div>
        <div className="flex-1 overflow-hidden">
          <ComparisonScreen />
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full w-full flex-col bg-zinc-950 text-zinc-100 p-6 overflow-y-auto">
      {/* Top Header */}
      <div className="flex items-center justify-between border-b border-zinc-800 pb-4 mb-6">
        <div>
          <div className="flex items-center gap-2.5">
            <Network className="h-6 w-6 text-cyan-400" />
            <h1 className="text-xl font-bold tracking-tight text-zinc-100">Linked Multi-Graph Audit</h1>
          </div>
          <p className="mt-1 text-xs text-zinc-400">
            Unified visual substrate for BD domain specs, DD technical specs, and COBOL/JCL source code graph.
          </p>
        </div>

        {/* View Switcher Header Buttons */}
        <div className="flex items-center gap-2 rounded-lg border border-zinc-800 bg-zinc-900/60 p-1">
          <button
            onClick={() => setActiveTab('landing')}
            className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
              (activeTab as string) === 'landing'
                ? 'bg-zinc-800 text-zinc-100 shadow-xs'
                : 'text-zinc-400 hover:text-zinc-200'
            }`}
          >
            Overview
          </button>
          <button
            onClick={() => setActiveTab('step')}
            className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
              (activeTab as string) === 'step'
                ? 'bg-zinc-800 text-zinc-100 shadow-xs'
                : 'text-zinc-400 hover:text-zinc-200'
            }`}
          >
            <Layers className="h-3.5 w-3.5 text-amber-400" />
            Step-by-Step View
          </button>
          <button
            onClick={() => setActiveTab('compare')}
            className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
              (activeTab as string) === 'compare'
                ? 'bg-zinc-800 text-zinc-100 shadow-xs'
                : 'text-zinc-400 hover:text-zinc-200'
            }`}
          >
            <GitCompare className="h-3.5 w-3.5 text-cyan-400" />
            Comparison View
          </button>
        </div>
      </div>

      {/* Main Content Area */}
      {activeTab === 'landing' && (
        <div className="max-w-4xl space-y-6">
          {/* Substrate Card */}
          <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-6 shadow-sm">
            <div className="flex items-center gap-2 text-emerald-400 text-xs font-semibold uppercase tracking-wider">
              <ShieldCheck className="h-4 w-4" /> Foundation Substrate Ready (Ticket 1/3)
            </div>
            <h2 className="mt-2 text-lg font-bold text-zinc-100">
              Aggregated Gap Catalog & Cross-Layer Bridge Engine
            </h2>
            <p className="mt-1 text-xs text-zinc-400 leading-relaxed">
              The backend aggregation endpoint (<code className="text-cyan-300 font-mono">GET /api/doc-code/linked-graph</code>) is fully operational. It surfaces non-authoritative parse eligibility, doc/code count deltas, name-fallback warnings, unassessed coverage statistics, and exact BD/DD layer memberships without mutating stored assertions.
            </p>

            {/* Two Main Screen Entry Points */}
            <div className="mt-6 grid grid-cols-1 md:grid-cols-2 gap-4">
              {/* Option 1: Step by step */}
              <div className="flex flex-col justify-between rounded-lg border border-zinc-800 bg-zinc-950 p-5 hover:border-amber-500/50 transition-all">
                <div className="space-y-2">
                  <div className="flex items-center gap-2">
                    <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-amber-500/10 text-amber-400 border border-amber-500/20">
                      <Layers className="h-4 w-4" />
                    </div>
                    <h3 className="text-sm font-semibold text-zinc-100">Step-by-Step View</h3>
                  </div>
                  <p className="text-xs text-zinc-400">
                    Step-driven linear audit path tracing BD hull envelopes wrapping DD technical entities into source code files.
                  </p>
                </div>
                <div className="mt-5">
                  <Button
                    variant="secondary"
                    className="w-full justify-between text-amber-400 border-amber-500/30 hover:bg-amber-500/10"
                    onClick={() => setActiveTab('step')}
                  >
                    <span>Launch Step-by-Step View</span>
                    <ArrowRight className="h-4 w-4" />
                  </Button>
                </div>
              </div>

              {/* Option 2: Comparison */}
              <div className="flex flex-col justify-between rounded-lg border border-zinc-800 bg-zinc-950 p-5 hover:border-cyan-500/50 transition-all">
                <div className="space-y-2">
                  <div className="flex items-center gap-2">
                    <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-cyan-500/10 text-cyan-400 border border-cyan-500/20">
                      <GitCompare className="h-4 w-4" />
                    </div>
                    <h3 className="text-sm font-semibold text-zinc-100">Comparison View</h3>
                  </div>
                  <p className="text-xs text-zinc-400">
                    Side-by-side multi-layer graph comparison with 2-of-3 layer toggles and split canvas view.
                  </p>
                </div>
                <div className="mt-5">
                  <Button
                    variant="secondary"
                    className="w-full justify-between text-cyan-400 border-cyan-500/30 hover:bg-cyan-500/10"
                    onClick={() => setActiveTab('compare')}
                  >
                    <span>Launch Comparison View</span>
                    <ArrowRight className="h-4 w-4" />
                  </Button>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
