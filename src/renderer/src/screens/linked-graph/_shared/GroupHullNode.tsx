import React from 'react'
import { NodeProps } from '@xyflow/react'

export interface GroupHullNodeData {
  label?: string
  sublabel?: string
  width?: number
  height?: number
  accentColor?: string
}

export function GroupHullNode({ data }: NodeProps): React.ReactElement {
  const nodeData = (data || {}) as GroupHullNodeData
  const width = nodeData.width ?? 320
  const height = nodeData.height ?? 200

  return (
    <div
      style={{ width, height }}
      className="relative rounded-xl border-2 border-dashed border-zinc-700/60 bg-zinc-900/30 p-3 backdrop-blur-xs transition-all pointer-events-none"
    >
      {nodeData.label && (
        <div className="pointer-events-auto absolute -top-3 left-4 flex cursor-pointer items-center gap-1.5 rounded-md border border-zinc-700 bg-zinc-900 px-2.5 py-0.5 text-xs font-semibold text-zinc-300 shadow-xs transition-colors hover:border-blue-500/60 hover:text-blue-200">
          <span
            className="h-2 w-2 rounded-full"
            style={{ backgroundColor: nodeData.accentColor ?? '#6366f1' }}
          />
          {nodeData.label}
          {nodeData.sublabel && (
            <span className="text-[10px] text-zinc-500 font-normal">({nodeData.sublabel})</span>
          )}
        </div>
      )}
    </div>
  )
}
