import React from 'react'
import { Loader2 } from 'lucide-react'
import type {
  ExportJson,
  CommunitiesResponse,
  NeighborResult,
  ImpactState,
  FileSymbolEdgesResponse,
} from './types'
import { COMMUNITY_COLORS, symbolFile, symbolName } from './utils'
import { ImpactPanel } from './ImpactPanel'

interface LeftPanelProps {
  selectedNode: string | null
  graphData: ExportJson | null
  communityData: CommunitiesResponse | null
  neighborData: NeighborResult | null
  neighborLoading: boolean
  impactState: ImpactState
  impactLoading: boolean
  symbolEdgesData: FileSymbolEdgesResponse | null
  symbolEdgesLoading: boolean
}

export function LeftPanel({
  selectedNode,
  graphData,
  communityData,
  neighborData,
  neighborLoading,
  impactState,
  impactLoading,
  symbolEdgesData,
  symbolEdgesLoading,
}: LeftPanelProps): React.ReactElement {
  const showImpactPanel = impactState.active && (impactState.result !== null || impactState.seedFiles.length > 0 || impactLoading)

  if (!selectedNode || !graphData || !communityData) {
    if (showImpactPanel) {
      return (
        <ImpactPanel impactState={impactState} impactLoading={impactLoading} />
      )
    }
    return (
      <div className="w-80 border-r border-zinc-700 bg-zinc-900 p-4 text-zinc-400 text-sm flex items-center justify-center">
        Click a node to see details
      </div>
    )
  }

  const communityId = communityData.node_index[selectedNode] ?? -1
  const community = communityData.communities.find((c) => c.community_id === communityId)

  // "Files that import this" = incoming edges (dst === selectedNode)
  const incomingEdges = graphData.edges.filter((e) => e.dst === selectedNode && !e.external)
  const incomingFiles = incomingEdges.map((e) => e.src).slice(0, 10)
  const incomingMore = incomingEdges.length > 10 ? incomingEdges.length - 10 : 0

  // "This imports" = outgoing edges (src === selectedNode)
  const outgoingEdges = graphData.edges.filter((e) => e.src === selectedNode && !e.external)
  const outgoingFiles = outgoingEdges.map((e) => e.dst).slice(0, 10)
  const outgoingMore = outgoingEdges.length > 10 ? outgoingEdges.length - 10 : 0

  const blastRadiusFiles = neighborData?.nodes.filter((n) => n !== selectedNode) ?? []

  // Centrality score, computed client-side using the same formula as the backend's
  // _compute_scores_python: indegree*3 + outdegree.
  const indegree = incomingEdges.length
  const outdegree = outgoingEdges.length
  const centralityScore = indegree * 3 + outdegree

  // Cross-file function calls only — same-file self-references are noise here.
  const outgoingCalls = (symbolEdgesData?.outgoing ?? []).filter(
    (e) => symbolFile(e.dst_symbol) !== selectedNode
  )
  const incomingCalls = (symbolEdgesData?.incoming ?? []).filter(
    (e) => symbolFile(e.src_symbol) !== selectedNode
  )

  return (
    <div className="w-80 border-r border-zinc-700 bg-zinc-900 p-4 overflow-y-auto text-xs space-y-4">
      {/* Node info */}
      <div>
        <div className="text-zinc-300 font-mono text-[10px] break-words">{selectedNode}</div>
      </div>

      {/* Centrality score breakdown */}
      <div>
        <div className="text-zinc-400 font-semibold mb-2">Centrality score</div>
        <div className="text-zinc-200 text-base font-semibold">{centralityScore}</div>
        <div className="text-[10px] text-zinc-500 mt-1">
          = indegree ({indegree}) × 3 + outdegree ({outdegree})
        </div>
        <div className="text-[10px] text-zinc-500">
          Higher indegree (more files depend on this) weighs 3× more than outdegree
          (this file depending on others).
        </div>
      </div>

      {/* Community */}
      {community && (
        <div>
          <div className="text-zinc-400 font-semibold mb-2">Community</div>
          <div className="flex items-center gap-2 text-zinc-300">
            <div
              className="w-3 h-3 rounded-full"
              style={{
                backgroundColor:
                  COMMUNITY_COLORS[community.community_id % COMMUNITY_COLORS.length],
              }}
            />
            <span>Community {community.community_id}</span>
          </div>
          {community.hub_paths.length > 0 && (
            <div className="mt-2 text-zinc-400">
              <div className="text-[10px] mb-1">Hub paths:</div>
              <div className="space-y-1">
                {community.hub_paths.slice(0, 5).map((hp) => (
                  <div key={hp} className="text-[10px] text-zinc-500 truncate">
                    {hp.split('/').pop()}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Files that import this */}
      {incomingFiles.length > 0 && (
        <div>
          <div className="text-zinc-400 font-semibold mb-2">Files that import this</div>
          <div className="space-y-1">
            {incomingFiles.map((f) => (
              <div key={f} className="text-[10px] text-zinc-400 truncate">
                {f.split('/').pop()}
              </div>
            ))}
            {incomingMore > 0 && (
              <div className="text-[10px] text-zinc-500 italic">and {incomingMore} more...</div>
            )}
          </div>
        </div>
      )}

      {/* This imports */}
      {outgoingFiles.length > 0 && (
        <div>
          <div className="text-zinc-400 font-semibold mb-2">This imports</div>
          <div className="space-y-1">
            {outgoingFiles.map((f) => (
              <div key={f} className="text-[10px] text-zinc-400 truncate">
                {f.split('/').pop()}
              </div>
            ))}
            {outgoingMore > 0 && (
              <div className="text-[10px] text-zinc-500 italic">and {outgoingMore} more...</div>
            )}
          </div>
        </div>
      )}

      {/* Blast radius */}
      {neighborData && !neighborLoading && (
        <div>
          <div className="text-zinc-400 font-semibold mb-2">Blast radius (2-hop)</div>
          <div className="space-y-1">
            {blastRadiusFiles.slice(0, 10).map((f) => (
              <div key={f} className="text-[10px] text-zinc-400 truncate">
                {f.split('/').pop()}
              </div>
            ))}
            {blastRadiusFiles.length > 10 && (
              <div className="text-[10px] text-zinc-500 italic">
                and {blastRadiusFiles.length - 10} more...
              </div>
            )}
          </div>
        </div>
      )}

      {/* Function-level drill-down, backed by symbol_graph_edges */}
      {symbolEdgesData && !symbolEdgesLoading && (
        <div>
          <div className="text-zinc-400 font-semibold mb-2">
            Functions in this file ({symbolEdgesData.defined_symbols.length})
          </div>
          {symbolEdgesData.defined_symbols.length === 0 ? (
            <div className="text-[10px] text-zinc-500 italic">
              No function-level call data yet — only Python/TypeScript files are
              analyzed at this level, and the graph may need rebuilding.
            </div>
          ) : (
            <div className="space-y-1 mb-3">
              {symbolEdgesData.defined_symbols.slice(0, 10).map((s) => (
                <div key={s} className="text-[10px] text-zinc-400 font-mono truncate">
                  {s}
                </div>
              ))}
              {symbolEdgesData.defined_symbols.length > 10 && (
                <div className="text-[10px] text-zinc-500 italic">
                  and {symbolEdgesData.defined_symbols.length - 10} more...
                </div>
              )}
            </div>
          )}

          {outgoingCalls.length > 0 && (
            <div className="mb-3">
              <div className="text-[10px] text-zinc-500 mb-1">Calls out to other files:</div>
              <div className="space-y-1">
                {outgoingCalls.slice(0, 8).map((e, i) => (
                  <div key={i} className="text-[10px] text-zinc-400">
                    <span className="font-mono">{symbolName(e.src_symbol)}</span>
                    {' → '}
                    <span className="font-mono text-zinc-300">
                      {symbolFile(e.dst_symbol).split('/').pop()}::{symbolName(e.dst_symbol)}
                    </span>
                    <span
                      className={
                        'ml-1 ' + (e.confidence_score >= 0.7 ? 'text-emerald-500' : 'text-amber-500')
                      }
                    >
                      ({e.resolution_method}, {e.confidence_score.toFixed(2)})
                    </span>
                  </div>
                ))}
                {outgoingCalls.length > 8 && (
                  <div className="text-[10px] text-zinc-500 italic">
                    and {outgoingCalls.length - 8} more...
                  </div>
                )}
              </div>
            </div>
          )}

          {incomingCalls.length > 0 && (
            <div>
              <div className="text-[10px] text-zinc-500 mb-1">Called from other files:</div>
              <div className="space-y-1">
                {incomingCalls.slice(0, 8).map((e, i) => (
                  <div key={i} className="text-[10px] text-zinc-400">
                    <span className="font-mono text-zinc-300">
                      {symbolFile(e.src_symbol).split('/').pop()}::{symbolName(e.src_symbol)}
                    </span>
                    {' → '}
                    <span className="font-mono">{symbolName(e.dst_symbol)}</span>
                    <span
                      className={
                        'ml-1 ' + (e.confidence_score >= 0.7 ? 'text-emerald-500' : 'text-amber-500')
                      }
                    >
                      ({e.resolution_method}, {e.confidence_score.toFixed(2)})
                    </span>
                  </div>
                ))}
                {incomingCalls.length > 8 && (
                  <div className="text-[10px] text-zinc-500 italic">
                    and {incomingCalls.length - 8} more...
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      )}

      {symbolEdgesLoading && (
        <div className="flex items-center gap-2 text-zinc-500">
          <Loader2 size={12} className="animate-spin" />
          <span>Loading function-level calls...</span>
        </div>
      )}

      {neighborLoading && (
        <div className="flex items-center gap-2 text-zinc-500">
          <Loader2 size={12} className="animate-spin" />
          <span>Loading neighbors...</span>
        </div>
      )}

      {/* Impact analysis results (shown when impact mode is active) */}
      {showImpactPanel && (
        <ImpactPanel impactState={impactState} impactLoading={impactLoading} inline />
      )}
    </div>
  )
}
