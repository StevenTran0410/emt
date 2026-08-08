import React from 'react'
import { FolderOpen } from 'lucide-react'

export function StorageSection(): React.ReactElement {
  return (
    <section className="card p-4 space-y-3">
      <h2 className="text-sm font-medium text-gray-300 flex items-center gap-2">
        <FolderOpen className="w-4 h-4" />
        Storage
      </h2>
      <p className="text-xs text-gray-500">
        Cache path, max cache size, and auto-cleanup settings will be configurable here in RPA-011.
      </p>
    </section>
  )
}
