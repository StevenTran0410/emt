import React from 'react'
import { MarkerType, type Node, type Edge } from '@xyflow/react'
import type { ExportJson, BlastRadiusResponse } from './types'

/** "file.py::Class.method" -> "file.py" */
export function symbolFile(symbol: string): string {
  return symbol.includes('::') ? symbol.split('::')[0] : symbol
}

/** "file.py::Class.method" -> "Class.method" */
export function symbolName(symbol: string): string {
  return symbol.includes('::') ? symbol.split('::').slice(1).join('::') : symbol
}

export const COMMUNITY_COLORS = [
  '#6366f1', '#0ea5e9', '#10b981', '#f59e0b',
  '#ef4444', '#8b5cf6', '#ec4899', '#14b8a6',
  '#f97316', '#84cc16',
]

export const MAX_NODES_DISPLAY = 500

export function getNodeStyle(
  path: string,
  communityId: number,
  isSelected: boolean,
  isNeighbor: boolean,
  isCycle: boolean,
  selectedNode: string | null,
  impactData?: BlastRadiusResponse | null,
): React.CSSProperties {
  let background: string
  if (isSelected) {
    background = '#fbbf24'
  } else if (impactData) {
    // Impact mode: color by hop distance
    const hopColor = impactData.subgraph.hop_colors[path]
    background = hopColor ?? '#52525b'
  } else if (isCycle) {
    background = '#ef4444'
  } else {
    background = communityId >= 0
      ? COMMUNITY_COLORS[communityId % COMMUNITY_COLORS.length]
      : '#52525b'
  }

  const dimmed = selectedNode && !isSelected && !isNeighbor && !impactData

  return {
    background,
    color: '#fff',
    fontSize: 10,
    padding: '4px 8px',
    borderRadius: 6,
    border: isNeighbor ? '2px solid #fbbf24' : '1px solid rgba(255,255,255,0.2)',
    opacity: dimmed ? 0.35 : 1,
    maxWidth: 160,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
    cursor: 'pointer',
  }
}

export function buildFlowGraph(
  data: ExportJson,
  nodeIndex: Record<string, number>,
  selectedNode: string | null,
  neighborNodes: Set<string>,
  cycleNodes: Set<string>,
  impactData?: BlastRadiusResponse | null,
): { nodes: Node[]; edges: Edge[] } {
  const nodes: Node[] = data.nodes.map((path) => {
    const communityId = nodeIndex[path] ?? -1
    const isCycle = cycleNodes.has(path)
    const isSelected = path === selectedNode
    const isNeighbor = neighborNodes.has(path)

    const filename = path.split('/').pop() ?? path
    return {
      id: path,
      data: { label: filename, fullPath: path, communityId },
      position: { x: 0, y: 0 },
      style: getNodeStyle(path, communityId, isSelected, isNeighbor, isCycle, selectedNode, impactData),
    } as Node
  })

  const edges: Edge[] = data.edges
    .filter((e) => !e.external)
    .map((e) => ({
      id: `${e.src}->${e.dst}`,
      source: e.src,
      target: e.dst,
      style: { stroke: '#71717a', strokeWidth: 1.25, opacity: 0.7 },
      markerEnd: { type: MarkerType.ArrowClosed, color: '#71717a' },
    }))

  return { nodes, edges }
}
