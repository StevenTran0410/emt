export type ExportJson = {
  nodes: string[]
  edges: Array<{ src: string; dst: string; external: boolean }>
  communities: Record<string, number>       // node_path -> community_id
  community_groups: Record<string, string[]> // community_id -> [node_paths]
  cycles: string[][]
  test_files: string[]
  generated_at: string
}

export type CommunitiesResponse = {
  snapshot_id: string
  total_communities: number
  communities: Array<{
    community_id: number
    member_count: number
    hub_paths: string[]
    modularity_contribution: number
    neighbor_community_ids: number[]
    is_singleton: boolean
    llm_summary: string | null
    generated_at: string
  }>
  node_index: Record<string, number>
}

export type NeighborResult = {
  snapshot_id: string
  seed_path: string
  hops: number
  nodes: string[]
  edges: Array<{
    src_path: string
    dst_path: string
    edge_type: string
    is_external: boolean
  }>
}

export type BlastRadiusResponse = {
  changed_files: string[]
  blast_radius: {
    total_affected: number
    by_hop: Record<number, number>
    high_risk_files: string[]
    affected_communities: Array<{
      community_id: number
      member_count: number
      hub_paths: string[]
    }>
    call_chains: unknown[]
  }
  subgraph: {
    nodes: string[]
    edges: unknown[]
    seed_files: string[]
    hop_colors: Record<string, string>
  }
  context_chunks: unknown[]
}

export type ImpactState = {
  active: boolean
  seedFiles: string[]
  result: BlastRadiusResponse | null
}

export type SymbolEdgeInfo = {
  src_symbol: string
  dst_symbol: string
  edge_type: string
  confidence_score: number
  resolution_method: string
}

export type FileSymbolEdgesResponse = {
  snapshot_id: string
  file_path: string
  defined_symbols: string[]
  outgoing: SymbolEdgeInfo[]
  incoming: SymbolEdgeInfo[]
}
