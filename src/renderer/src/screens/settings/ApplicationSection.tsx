import React from 'react'
import { Info } from 'lucide-react'

export function ApplicationSection({
  version,
  userDataPath,
}: {
  version: string
  userDataPath: string
}): React.ReactElement {
  return (
    <section className="card p-4 space-y-3">
      <h2 className="text-sm font-medium text-gray-300 flex items-center gap-2">
        <Info className="w-4 h-4" />
        Application
      </h2>
      <div className="space-y-2 text-sm">
        <div className="flex justify-between py-1.5 border-b border-surface-border">
          <span className="text-gray-400">Version</span>
          <span className="text-gray-200 font-mono">{version || '—'}</span>
        </div>
        <div className="flex justify-between items-start py-1.5 border-b border-surface-border gap-4">
          <span className="text-gray-400 shrink-0">User data path</span>
          <span className="text-gray-200 font-mono text-xs text-right break-all">
            {userDataPath || '—'}
          </span>
        </div>
      </div>
    </section>
  )
}
