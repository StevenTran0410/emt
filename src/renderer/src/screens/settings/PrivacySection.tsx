import React from 'react'

export function PrivacySection(): React.ReactElement {
  return (
    <section className="card p-4 space-y-3">
      <h2 className="text-sm font-medium text-gray-300">Privacy defaults</h2>
      <p className="text-xs text-gray-500">
        Default privacy mode and provider settings will be configurable here in RPA-011.
      </p>
      <div className="flex items-center justify-between py-2 border border-surface-border rounded-md px-3">
        <span className="text-sm text-gray-400">Default mode</span>
        <span className="text-xs text-green-400 bg-green-950 border border-green-800 px-2 py-0.5 rounded-full">
          Strict Local
        </span>
      </div>
    </section>
  )
}
