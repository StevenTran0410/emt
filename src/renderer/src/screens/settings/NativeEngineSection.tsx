import React, { useState } from 'react'
import { Cpu } from 'lucide-react'
import type { Diagnostics } from './types'
import { NativeModulePopup } from './NativeModulePopup'

export function NativeEngineSection({
  diagnostics,
  diagError,
}: {
  diagnostics: Diagnostics | null
  diagError: string | null
}): React.ReactElement {
  const [showNativePopup, setShowNativePopup] = useState(false)

  return (
    <section className="card p-4 space-y-3">
      <h2 className="text-sm font-medium text-gray-300 flex items-center gap-2">
        <Cpu className="w-4 h-4" />
        Native Engine (C++)
      </h2>

      {diagError && (
        <p className="text-xs text-red-400">Backend not reachable: {diagError}</p>
      )}

      {!diagnostics && !diagError && (
        <p className="text-xs text-gray-500 animate-pulse">Loading…</p>
      )}

      {diagnostics && (() => {
        const avail = diagnostics.native_functions.filter((f) => f.available).length
        const total = diagnostics.native_functions.length
        const allLoaded = avail === total
        return (
          <div className="space-y-2 text-sm">
            <div className="flex justify-between py-1.5 border-b border-surface-border">
              <span className="text-gray-400">Python version</span>
              <span className="text-gray-200 font-mono text-xs">{diagnostics.python_version}</span>
            </div>
            <div className="flex justify-between items-center py-1.5">
              <span className="text-gray-400">C++ acceleration</span>
              <button
                onClick={() => setShowNativePopup(true)}
                className={`text-xs px-2.5 py-1 rounded-full border cursor-pointer hover:brightness-125 transition-all ${
                  allLoaded
                    ? 'text-green-400 bg-green-950 border-green-800'
                    : avail > 0
                    ? 'text-yellow-400 bg-yellow-950 border-yellow-800'
                    : 'text-red-400 bg-red-950 border-red-800'
                }`}
              >
                {avail}/{total} modules
              </button>
            </div>
          </div>
        )
      })()}

      {showNativePopup && diagnostics && (
        <NativeModulePopup
          functions={diagnostics.native_functions}
          onClose={() => setShowNativePopup(false)}
        />
      )}
    </section>
  )
}
