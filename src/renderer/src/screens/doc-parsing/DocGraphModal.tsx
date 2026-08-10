import React, { useState, useMemo, useCallback } from 'react'
import {
  X,
  Layers,
  Filter,
  Info,
  ArrowRight,
  ArrowLeft,
  Link,
  Database,
  FileText
} from 'lucide-react'
import {
  ReactFlow,
  Background,
  Controls,
  Panel,
  type Node,
  type Edge,
  type NodeProps,
  Handle,
  Position
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'

import { applyDagreLayout } from '../graph/layout'
import type { DocGraphExport, DocNode, DocEdge, DocGraphSummary } from '../../types/electron'

function getNodeStyle(nodeType: string): { bg: string; border: string; text: string; dot: string; hex: string } {
  switch (nodeType) {
    case 'program':
      return { bg: 'bg-indigo-950/90', border: 'border-indigo-500/80', text: 'text-indigo-200', dot: 'bg-indigo-400', hex: '#6366f1' }
    case 'dataset':
      return { bg: 'bg-emerald-950/90', border: 'border-emerald-500/80', text: 'text-emerald-200', dot: 'bg-emerald-400', hex: '#10b981' }
    case 'step':
      return { bg: 'bg-sky-950/90', border: 'border-sky-500/80', text: 'text-sky-200', dot: 'bg-sky-400', hex: '#0ea5e9' }
    case 'dd':
      return { bg: 'bg-blue-950/90', border: 'border-blue-500/80', text: 'text-blue-200', dot: 'bg-blue-400', hex: '#3b82f6' }
    case 'copybook':
      return { bg: 'bg-violet-950/90', border: 'border-violet-500/80', text: 'text-violet-200', dot: 'bg-violet-400', hex: '#8b5cf6' }
    case 'job':
      return { bg: 'bg-amber-950/90', border: 'border-amber-500/80', text: 'text-amber-200', dot: 'bg-amber-400', hex: '#f59e0b' }
    case 'extroutine':
      return { bg: 'bg-rose-950/90', border: 'border-rose-500/80', text: 'text-rose-200', dot: 'bg-rose-400', hex: '#f43f5e' }
    case 'br':
      return { bg: 'bg-teal-950/90', border: 'border-teal-500/80', text: 'text-teal-200', dot: 'bg-teal-400', hex: '#2dd4bf' }
    case 'ddlimit':
      return { bg: 'bg-orange-950/90', border: 'border-orange-500/80', text: 'text-orange-200', dot: 'bg-orange-400', hex: '#fb923c' }
    case 'tbd':
      return { bg: 'bg-yellow-950/90', border: 'border-yellow-500/80', text: 'text-yellow-200', dot: 'bg-yellow-400', hex: '#facc15' }
    case 'capability':
      return { bg: 'bg-fuchsia-950/90', border: 'border-fuchsia-500/80', text: 'text-fuchsia-200', dot: 'bg-fuchsia-400', hex: '#e879f9' }
    case 'doc':
      return { bg: 'bg-cyan-950/90', border: 'border-cyan-500/80', text: 'text-cyan-200', dot: 'bg-cyan-400', hex: '#22d3ee' }
    case 'actor':
      return { bg: 'bg-slate-800/90', border: 'border-slate-500/80', text: 'text-slate-200', dot: 'bg-slate-400', hex: '#94a3b8' }
    default:
      return { bg: 'bg-zinc-900/90', border: 'border-zinc-700/80', text: 'text-zinc-200', dot: 'bg-zinc-400', hex: '#71717a' }
  }
}

function CustomDocNodeModal({ data }: NodeProps) {
  const { label, nodeType, isSelected, isNeighbor, isDimmed } = data as {
    label: string
    nodeType: string
    rawNode: DocNode
    isSelected?: boolean
    isNeighbor?: boolean
    isDimmed?: boolean
  }
  const style = getNodeStyle(nodeType)

  let borderClasses = style.border
  let ringClasses = ''

  if (isSelected) {
    borderClasses = 'border-amber-400'
    ringClasses = 'ring-2 ring-amber-400 shadow-lg shadow-amber-500/20'
  } else if (isNeighbor) {
    borderClasses = 'border-cyan-400'
    ringClasses = 'ring-1 ring-cyan-400/60 shadow-md shadow-cyan-500/10'
  }

  return (
    <div
      className={`px-3 py-2 rounded-lg border text-xs font-mono transition-all duration-200 ${style.bg} ${borderClasses} ${ringClasses} ${
        isDimmed ? 'opacity-20' : 'opacity-100'
      }`}
    >
      <Handle type="target" position={Position.Top} className="w-2 h-2 !bg-zinc-400" />
      <div className="flex items-center gap-2">
        <span className={`w-2 h-2 rounded-full ${style.dot} shrink-0`} />
        <span className={`font-semibold ${style.text}`}>{label}</span>
      </div>
      <div className="text-[10px] text-zinc-400 mt-0.5 uppercase tracking-wider font-semibold">
        {nodeType}
      </div>
      <Handle type="source" position={Position.Bottom} className="w-2 h-2 !bg-zinc-400" />
    </div>
  )
}

const nodeTypes = {
  customDocNodeModal: CustomDocNodeModal
}

interface DocGraphModalProps {
  exportData: DocGraphExport
  summary: DocGraphSummary | null
  clusterId?: string
  showAllNodes: boolean
  setShowAllNodes: (val: boolean) => void
  onClose: () => void
}

export function DocGraphModal({
  exportData,
  summary,
  clusterId,
  showAllNodes,
  setShowAllNodes,
  onClose
}: DocGraphModalProps): React.ReactElement {
  const [selectedNode, setSelectedNode] = useState<DocNode | null>(null)

  // Compute connected vs isolated nodes
  const { connectedNodeIds, totalIsolatedCount } = useMemo(() => {
    if (!exportData || !exportData.nodes) {
      return { connectedNodeIds: new Set<string>(), isolatedNodesGrouped: {}, totalIsolatedCount: 0 }
    }

    const connectedIds = new Set<string>()
    for (const e of exportData.edges || []) {
      connectedIds.add(e.src_node_id)
      connectedIds.add(e.dst_node_id)
    }

    let isolatedCount = 0
    for (const n of exportData.nodes) {
      if (!connectedIds.has(n.id)) {
        isolatedCount++
      }
    }

    return {
      connectedNodeIds: connectedIds,
      totalIsolatedCount: isolatedCount
    }
  }, [exportData])

  // Compute 1-hop neighbors and connection details for selectedNode
  const { neighborSet, connectedEdgeList } = useMemo(() => {
    if (!selectedNode || !exportData?.edges) {
      return { neighborSet: new Set<string>(), connectedEdgeList: [] }
    }

    const neighbors = new Set<string>([selectedNode.id])
    const connEdges: Array<{
      edge: DocEdge
      otherNode: DocNode | null
      direction: 'outgoing' | 'incoming'
    }> = []

    const nodeMap = new Map<string, DocNode>()
    for (const n of exportData.nodes || []) {
      nodeMap.set(n.id, n)
    }

    for (const e of exportData.edges) {
      if (e.src_node_id === selectedNode.id) {
        neighbors.add(e.dst_node_id)
        connEdges.push({
          edge: e,
          otherNode: nodeMap.get(e.dst_node_id) || null,
          direction: 'outgoing'
        })
      } else if (e.dst_node_id === selectedNode.id) {
        neighbors.add(e.src_node_id)
        connEdges.push({
          edge: e,
          otherNode: nodeMap.get(e.src_node_id) || null,
          direction: 'incoming'
        })
      }
    }

    return { neighborSet: neighbors, connectedEdgeList: connEdges }
  }, [selectedNode, exportData])

  // Build flow graph nodes & edges
  const { nodes: flowNodes, edges: flowEdges, presentNodeTypes } = useMemo(() => {
    if (!exportData || !exportData.nodes) return { nodes: [], edges: [], presentNodeTypes: [] }

    const filteredNodes = showAllNodes
      ? exportData.nodes
      : exportData.nodes.filter((n) => connectedNodeIds.has(n.id) && n.node_type !== 'field')

    const rawNodes = filteredNodes.slice(0, 500)
    const validNodeIds = new Set(rawNodes.map((n) => n.id))

    const isAnySelected = selectedNode !== null

    const initialNodes: Node[] = rawNodes.map((n) => {
      const isSelected = selectedNode?.id === n.id
      const isNeighbor = isAnySelected && !isSelected && neighborSet.has(n.id)
      const isDimmed = isAnySelected && !isSelected && !isNeighbor

      return {
        id: n.id,
        type: 'customDocNodeModal',
        data: {
          label: n.display_name,
          nodeType: n.node_type,
          rawNode: n,
          isSelected,
          isNeighbor,
          isDimmed
        },
        position: { x: 0, y: 0 }
      }
    })

    const initialEdges: Edge[] = (exportData.edges || [])
      .filter((e) => validNodeIds.has(e.src_node_id) && validNodeIds.has(e.dst_node_id))
      .map((e) => {
        const isConnectedToSelected =
          isAnySelected && (e.src_node_id === selectedNode.id || e.dst_node_id === selectedNode.id)
        const isEdgeDimmed = isAnySelected && !isConnectedToSelected

        const stroke = isConnectedToSelected ? '#f59e0b' : '#64748b'
        const strokeWidth = isConnectedToSelected ? 2.5 : 1.5
        const opacity = isEdgeDimmed ? 0.15 : isConnectedToSelected ? 1.0 : 0.7
        const labelOpacity = isEdgeDimmed ? 0.15 : 1

        return {
          id: `${e.src_node_id}->${e.dst_node_id}:${e.edge_type}`,
          source: e.src_node_id,
          target: e.dst_node_id,
          label: e.edge_type,
          style: { stroke, strokeWidth, opacity },
          animated: isConnectedToSelected,
          // Dark, pill-shaped label instead of React Flow's default opaque white box.
          labelStyle: {
            fill: isConnectedToSelected ? '#fcd34d' : '#a1a1aa',
            fontSize: 9,
            fontFamily: 'ui-monospace, SFMono-Regular, monospace',
            fontWeight: 600,
            opacity: labelOpacity
          },
          labelShowBg: true,
          labelBgStyle: {
            fill: '#0a0a0a',
            fillOpacity: isEdgeDimmed ? 0.25 : 0.82,
            stroke: isConnectedToSelected ? '#f59e0b' : '#3f3f46',
            strokeWidth: 0.5
          },
          labelBgPadding: [5, 2] as [number, number],
          labelBgBorderRadius: 5
        }
      })

    const layoutedNodes = applyDagreLayout(initialNodes, initialEdges)
    const presentTypes = Array.from(new Set(rawNodes.map((n) => n.node_type))).sort()

    return { nodes: layoutedNodes, edges: initialEdges, presentNodeTypes: presentTypes }
  }, [exportData, showAllNodes, connectedNodeIds, selectedNode, neighborSet])

  const handleNodeClick = useCallback((_: React.MouseEvent, node: Node) => {
    const rawNode = (node.data?.rawNode as DocNode) || null
    setSelectedNode(rawNode)
  }, [])

  const handlePaneClick = useCallback(() => {
    setSelectedNode(null)
  }, [])

  return (
    <div className="fixed inset-0 z-50 bg-zinc-950 flex flex-col animate-in fade-in duration-200">
      {/* Top Header */}
      <div className="px-5 py-3 border-b border-zinc-800 bg-zinc-900/90 backdrop-blur flex items-center justify-between shrink-0">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <Layers className="w-5 h-5 text-indigo-400" />
            <span className="font-semibold text-zinc-100 text-sm">
              Doc Graph — {clusterId || summary?.cluster_id || 'Full View'}
            </span>
          </div>
          <div className="h-4 w-px bg-zinc-700" />
          <div className="text-xs text-zinc-400 flex items-center gap-3 font-mono">
            <span>{flowNodes.length} nodes shown</span>
            <span>{flowEdges.length} edges</span>
            {totalIsolatedCount > 0 && (
              <span className="text-amber-400">({totalIsolatedCount} isolated hidden)</span>
            )}
          </div>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={() => setShowAllNodes(!showAllNodes)}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors flex items-center gap-2 ${
              showAllNodes
                ? 'bg-amber-500/10 border-amber-500/40 text-amber-300 hover:bg-amber-500/20'
                : 'bg-zinc-800 border-zinc-700 text-zinc-300 hover:bg-zinc-700'
            }`}
          >
            <Filter className="w-3.5 h-3.5" />
            <span>{showAllNodes ? 'Show Connected Only' : 'Show All Nodes (Inc. Isolated)'}</span>
          </button>

          <button
            onClick={onClose}
            className="p-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-400 hover:text-zinc-100 border border-zinc-700 transition-colors"
            title="Exit full view"
          >
            <X className="w-5 h-5" />
          </button>
        </div>
      </div>

      {/* Main Body */}
      <div className="flex-1 flex overflow-hidden relative">
        {/* Left Side Info Panel */}
        <div className="w-96 border-r border-zinc-800 bg-zinc-900/70 backdrop-blur flex flex-col shrink-0 overflow-y-auto">
          {selectedNode ? (
            <div className="p-4 space-y-4 text-xs">
              <div className="flex items-start justify-between border-b border-zinc-800 pb-3">
                <div>
                  <span className="text-[10px] font-mono uppercase font-bold text-amber-400 tracking-wider">
                    Selected Node
                  </span>
                  <h3 className="text-sm font-bold font-mono text-zinc-100 mt-0.5">
                    {selectedNode.display_name}
                  </h3>
                </div>
                <button
                  onClick={() => setSelectedNode(null)}
                  className="text-zinc-500 hover:text-zinc-300 p-1"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>

              {/* Node Type & Style Badge */}
              <div className="bg-zinc-950/70 p-2.5 rounded-lg border border-zinc-800/80 flex items-center justify-between font-mono">
                <span className="text-zinc-400">Node Type:</span>
                <span className={`px-2 py-0.5 rounded text-[11px] font-bold uppercase ${getNodeStyle(selectedNode.node_type).bg} ${getNodeStyle(selectedNode.node_type).text}`}>
                  {selectedNode.node_type}
                </span>
              </div>

              {/* Attributes */}
              {Object.keys(selectedNode.attributes || {}).length > 0 && (
                <div className="space-y-1.5">
                  <span className="text-zinc-400 font-semibold flex items-center gap-1.5">
                    <Info className="w-3.5 h-3.5 text-indigo-400" />
                    Attributes
                  </span>
                  <pre className="bg-zinc-950 p-2.5 rounded-lg text-[11px] text-zinc-300 overflow-x-auto max-h-40 font-mono border border-zinc-800">
                    {JSON.stringify(selectedNode.attributes, null, 2)}
                  </pre>
                </div>
              )}

              {/* Provenance */}
              {selectedNode.provenance && selectedNode.provenance.length > 0 && (
                <div className="space-y-1.5">
                  <span className="text-zinc-400 font-semibold flex items-center gap-1.5">
                    <FileText className="w-3.5 h-3.5 text-emerald-400" />
                    Provenance
                  </span>
                  <div className="space-y-1.5 max-h-32 overflow-y-auto pr-1">
                    {selectedNode.provenance.map((p, idx) => (
                      <div key={idx} className="bg-zinc-950/80 p-2 rounded-lg border border-zinc-800 text-[11px] space-y-0.5">
                        <div className="truncate text-zinc-300"><strong className="text-zinc-400">Doc:</strong> {p.doc_id}</div>
                        {p.doc_span && (
                          <div className="text-zinc-400">
                            Section: <span className="text-zinc-200">{p.doc_span.section_id}</span> (L{p.doc_span.line_start})
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Connections Section */}
              <div className="space-y-2 pt-2 border-t border-zinc-800">
                <span className="text-zinc-300 font-semibold flex items-center gap-1.5">
                  <Link className="w-3.5 h-3.5 text-amber-400" />
                  Connections ({connectedEdgeList.length})
                </span>

                {connectedEdgeList.length === 0 ? (
                  <div className="p-3 bg-zinc-950/50 rounded-lg text-zinc-500 italic text-[11px] text-center">
                    No direct connections found.
                  </div>
                ) : (
                  <div className="space-y-1.5 max-h-56 overflow-y-auto pr-1">
                    {connectedEdgeList.map(({ edge, otherNode, direction }, idx) => {
                      const otherType = otherNode?.node_type || 'other'
                      const style = getNodeStyle(otherType)
                      return (
                        <button
                          key={idx}
                          onClick={() => otherNode && setSelectedNode(otherNode)}
                          className="w-full text-left bg-zinc-950/80 hover:bg-zinc-800 p-2 rounded-lg border border-zinc-800 transition-colors flex items-center justify-between group"
                        >
                          <div className="space-y-0.5 min-w-0 flex-1 pr-2">
                            <div className="flex items-center gap-1.5">
                              <span className="px-1.5 py-0.2 rounded text-[9px] bg-zinc-800 text-amber-300 font-mono font-bold uppercase border border-amber-500/30">
                                {edge.edge_type}
                              </span>
                              <span className="text-[10px] text-zinc-400 flex items-center gap-1">
                                {direction === 'outgoing' ? (
                                  <>
                                    <span>out</span>
                                    <ArrowRight className="w-3 h-3 text-emerald-400" />
                                  </>
                                ) : (
                                  <>
                                    <ArrowLeft className="w-3 h-3 text-sky-400" />
                                    <span>in</span>
                                  </>
                                )}
                              </span>
                            </div>
                            <div className="font-mono text-zinc-200 text-[11px] font-semibold truncate group-hover:text-amber-300">
                              {otherNode?.display_name || (direction === 'outgoing' ? edge.dst_node_id : edge.src_node_id)}
                            </div>
                          </div>
                          <span className={`px-1.5 py-0.5 rounded text-[9px] font-mono uppercase font-semibold shrink-0 ${style.bg} ${style.text}`}>
                            {otherType}
                          </span>
                        </button>
                      )
                    })}
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div className="p-6 text-center space-y-3 text-zinc-500 my-auto">
              <Database className="w-8 h-8 text-zinc-600 mx-auto" />
              <div className="text-xs">Select any node on the canvas to inspect its details, attributes, provenance, and 1-hop connections.</div>
            </div>
          )}
        </div>

        {/* ReactFlow Canvas Area */}
        <div className="flex-1 relative">
          <ReactFlow
            nodes={flowNodes}
            edges={flowEdges}
            nodeTypes={nodeTypes}
            onNodeClick={handleNodeClick}
            onPaneClick={handlePaneClick}
            fitView
            className="bg-zinc-950"
          >
            <Background color="#27272a" gap={20} />
            <Controls className="!bg-zinc-800 !border-zinc-700 !text-zinc-200" />

            {/* Legend Panel */}
            <Panel position="top-right">
              <div className="bg-zinc-900/90 border border-zinc-800 rounded-lg p-3 text-xs space-y-1.5 shadow-xl backdrop-blur">
                <div className="font-semibold text-zinc-200 text-[11px] uppercase tracking-wider mb-2">
                  Node Types & Legend
                </div>
                {presentNodeTypes.map((type) => {
                  const s = getNodeStyle(type)
                  return (
                    <div key={type} className="flex items-center gap-2 text-zinc-300">
                      <div className={`w-2.5 h-2.5 rounded-full ${s.dot} shrink-0`} />
                      <span className="capitalize text-[11px] font-mono">{type}</span>
                    </div>
                  )
                })}

                <div className="border-t border-zinc-800 pt-2 mt-2 space-y-1">
                  <div className="flex items-center gap-2">
                    <div className="w-2.5 h-2.5 rounded-full bg-amber-400 ring-2 ring-amber-400 shrink-0" />
                    <span className="text-zinc-400 text-[11px]">Selected Node</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="w-2.5 h-2.5 rounded-full bg-cyan-400 ring-1 ring-cyan-400 shrink-0" />
                    <span className="text-zinc-400 text-[11px]">1-Hop Neighbor</span>
                  </div>
                </div>
              </div>
            </Panel>
          </ReactFlow>
        </div>
      </div>
    </div>
  )
}
