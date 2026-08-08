import React from 'react'
import type { NativeFunction } from './types'

export function NativeModulePopup({
  functions,
  onClose,
}: {
  functions: NativeFunction[]
  onClose: () => void
}): React.ReactElement {
  const available = functions.filter((f) => f.available).length
  const total = functions.length

  // Group by module
  const byModule: Record<string, NativeFunction[]> = {}
  for (const fn of functions) {
    const mod = fn.module || 'unknown'
    if (!byModule[mod]) byModule[mod] = []
    byModule[mod].push(fn)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div
        className="bg-surface-overlay border border-surface-border rounded-xl shadow-2xl w-[480px] max-h-[70vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="px-5 py-4 border-b border-surface-border flex items-center justify-between shrink-0">
          <div>
            <h3 className="text-sm font-semibold text-gray-100">Native C++ Modules</h3>
            <p className="text-xs text-gray-500 mt-0.5">
              {available}/{total} functions accelerated
            </p>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-300 transition-colors text-lg leading-none">&times;</button>
        </div>

        {/* Scrollable body */}
        <div className="overflow-y-auto px-5 py-3 space-y-4">
          {Object.entries(byModule).map(([mod, fns]) => {
            const modAvail = fns.filter((f) => f.available).length
            return (
              <div key={mod}>
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-xs font-mono text-gray-400">{mod}</span>
                  <span className={`text-[10px] px-1.5 py-0.5 rounded-full border ${
                    modAvail === fns.length
                      ? 'text-green-400 bg-green-950 border-green-800'
                      : modAvail > 0
                      ? 'text-yellow-400 bg-yellow-950 border-yellow-800'
                      : 'text-red-400 bg-red-950 border-red-800'
                  }`}>
                    {modAvail}/{fns.length}
                  </span>
                </div>
                <div className="space-y-1">
                  {fns.map((fn) => (
                    <div key={fn.name} className="flex items-start gap-2 py-1.5 border-b border-surface-border/50 last:border-0">
                      <span className={`mt-0.5 shrink-0 text-[10px] font-mono px-1.5 py-0.5 rounded border ${
                        fn.available
                          ? 'text-green-400 bg-green-950 border-green-800'
                          : 'text-yellow-400 bg-yellow-950 border-yellow-800'
                      }`}>
                        {fn.available ? 'C++' : 'PY'}
                      </span>
                      <div className="min-w-0">
                        <div className="text-xs font-mono text-gray-200">{fn.name}</div>
                        <div className="text-[11px] text-gray-500 leading-4">{fn.description}</div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )
          })}
        </div>

        {/* Footer hint */}
        {functions.some((f) => !f.available) && (
          <div className="px-5 py-3 border-t border-surface-border shrink-0">
            <p className="text-[11px] text-yellow-500">
              Run <span className="font-mono bg-zinc-800 px-1 rounded">python scripts/build_native_graph.py</span> (builds graph + BM25) and <span className="font-mono bg-zinc-800 px-1 rounded">python scripts/build_native_chunker.py</span> inside <span className="font-mono">backend/</span> to enable C++ acceleration.
            </p>
          </div>
        )}
      </div>
    </div>
  )
}
