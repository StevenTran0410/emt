import { type Node, type Edge } from '@xyflow/react'
import dagre from '@dagrejs/dagre'
import * as d3force from 'd3-force'

export function applyDagreLayout(nodes: Node[], edges: Edge[]): Node[] {
  const g = new dagre.graphlib.Graph()
  g.setGraph({ rankdir: 'LR', nodesep: 40, ranksep: 80 })
  g.setDefaultEdgeLabel(() => ({}))

  nodes.forEach((n) => g.setNode(n.id, { width: 160, height: 36 }))
  edges.forEach((e) => g.setEdge(e.source, e.target))

  dagre.layout(g)

  return nodes.map((n) => {
    const pos = g.node(n.id)
    return { ...n, position: { x: pos.x - 80, y: pos.y - 18 } }
  })
}

interface ForceNode extends d3force.SimulationNodeDatum {
  id: string
  communityId: number
}

/** Force-directed layout with community clustering: one differential-collision rule gives tight same-cluster packing and wide gaps between clusters, instead of a separate repulsion force fighting for the same job. */
export function applyForceClusterLayout(nodes: Node[], edges: Edge[]): Node[] {
  const n = nodes.length
  if (n === 0) return nodes

  // Seed positions on a circle so the simulation doesn't start with every node at the origin.
  const seedRadius = Math.max(300, n * 4)
  const forceNodes: ForceNode[] = nodes.map((node, i) => {
    const angle = (2 * Math.PI * i) / n
    return {
      id: node.id,
      communityId: (node.data?.communityId as number | undefined) ?? -1,
      x: seedRadius * Math.cos(angle),
      y: seedRadius * Math.sin(angle),
    }
  })

  const nodeById = new Map(forceNodes.map((fn) => [fn.id, fn]))
  const forceLinks = edges
    .filter((e) => nodeById.has(e.source) && nodeById.has(e.target))
    .map((e) => ({ source: e.source, target: e.target }))

  // Padding sized for this app's ~160x36 node boxes: same-cluster pairs get just enough
  // room to avoid label overlap, different-cluster pairs get a much wider gap.
  const SAME_CLUSTER_PADDING = 100
  const DIFF_CLUSTER_PADDING = 230

  // Brute-force O(n^2) pairwise collision check — fine at this app's node-count scale;
  // a quadtree-accelerated version only pays off on much larger graphs.
  function differentialCollide(alpha: number): void {
    for (let i = 0; i < forceNodes.length; i++) {
      const a = forceNodes[i]
      for (let j = i + 1; j < forceNodes.length; j++) {
        const b = forceNodes[j]
        const dx = (b.x ?? 0) - (a.x ?? 0)
        const dy = (b.y ?? 0) - (a.y ?? 0)
        const dist = Math.sqrt(dx * dx + dy * dy) || 0.01
        const minDist = a.communityId === b.communityId ? SAME_CLUSTER_PADDING : DIFF_CLUSTER_PADDING
        if (dist >= minDist) continue
        const push = ((minDist - dist) / dist) * alpha * 0.5
        const ox = dx * push
        const oy = dy * push
        a.vx = (a.vx ?? 0) - ox
        a.vy = (a.vy ?? 0) - oy
        b.vx = (b.vx ?? 0) + ox
        b.vy = (b.vy ?? 0) + oy
      }
    }
  }

  function clusterCohesion(alpha: number): void {
    const centroids = new Map<number, { x: number; y: number; count: number }>()
    for (const fn of forceNodes) {
      const c = centroids.get(fn.communityId) ?? { x: 0, y: 0, count: 0 }
      c.x += fn.x ?? 0
      c.y += fn.y ?? 0
      c.count += 1
      centroids.set(fn.communityId, c)
    }
    for (const fn of forceNodes) {
      const c = centroids.get(fn.communityId)
      if (!c || c.count <= 2) continue // singleton/pair communities: nothing to cohere to
      fn.vx = (fn.vx ?? 0) + (c.x / c.count - (fn.x ?? 0)) * alpha * 0.1
      fn.vy = (fn.vy ?? 0) + (c.y / c.count - (fn.y ?? 0)) * alpha * 0.1
    }
  }

  const simulation = d3force
    .forceSimulation(forceNodes)
    // No generic repulsion — differential collision below already does all the separation work.
    .force(
      'link',
      d3force
        .forceLink<ForceNode, { source: string; target: string }>(forceLinks)
        .id((d) => d.id)
        .distance(70)
        .strength(0.3)
    )
    // Very weak per-node gravity — just a safety net against disconnected nodes drifting off.
    .force('gravityX', d3force.forceX(0).strength(0.004))
    .force('gravityY', d3force.forceY(0).strength(0.004))
    .velocityDecay(0.45) // dampens oscillation for a more stable settled layout
    .stop()

  // Run synchronously to a settled state — React Flow only needs final positions. Multiple
  // collision passes per tick are needed to fully resolve overlaps in a dense graph.
  for (let tick = 0; tick < 300; tick++) {
    simulation.tick()
    const alpha = simulation.alpha()
    clusterCohesion(alpha)
    for (let pass = 0; pass < 3; pass++) differentialCollide(alpha)
  }

  const positionById = new Map(forceNodes.map((fn) => [fn.id, { x: fn.x ?? 0, y: fn.y ?? 0 }]))
  return nodes.map((node) => ({ ...node, position: positionById.get(node.id) ?? { x: 0, y: 0 } }))
}

export interface LaneAlignedLayoutOptions {
  nodeWidth?: number
  nodeHeight?: number
  verticalGap?: number
}

/**
 * Lane-aligned multi-graph layout engine (Ticket 1 §6.2).
 * Lays out anchor layer with rankdir LR Dagre once, then positions secondary layer
 * aligned horizontally across from matched anchor nodes at laneX, reserving slack for unmatched nodes.
 */
export function applyLaneAlignedLayout(
  anchorNodes: Node[],
  anchorEdges: Edge[],
  secondaryNodes: Node[],
  matchFn: (secNode: Node) => string | null,
  laneX: number,
  opts: LaneAlignedLayoutOptions = {}
): { anchorNodes: Node[]; secondaryNodes: Node[] } {
  const nodeHeight = opts.nodeHeight ?? 36
  const verticalGap = opts.verticalGap ?? 20

  const laidOutAnchor = applyDagreLayout(anchorNodes, anchorEdges)
  const anchorPosMap = new Map<string, { x: number; y: number }>()
  laidOutAnchor.forEach((n) => anchorPosMap.set(n.id, n.position))

  const unmatchedSecondary: Node[] = []
  const laidOutSecondary: Node[] = secondaryNodes.map((secNode) => {
    const anchorId = matchFn(secNode)
    if (anchorId && anchorPosMap.has(anchorId)) {
      const anchorPos = anchorPosMap.get(anchorId)!
      return { ...secNode, position: { x: laneX, y: anchorPos.y } }
    }
    unmatchedSecondary.push(secNode)
    return { ...secNode, position: { x: laneX, y: 0 } }
  })

  // Position unmatched secondary nodes in vertical gaps
  if (unmatchedSecondary.length > 0) {
    let maxY = 0
    anchorPosMap.forEach((pos) => {
      if (pos.y > maxY) maxY = pos.y
    })
    let curY = maxY + nodeHeight + verticalGap
    const unmatchedIds = new Set(unmatchedSecondary.map((n) => n.id))

    return {
      anchorNodes: laidOutAnchor,
      secondaryNodes: laidOutSecondary.map((secNode) => {
        if (unmatchedIds.has(secNode.id)) {
          const pos = { x: laneX, y: curY }
          curY += nodeHeight + verticalGap
          return { ...secNode, position: pos }
        }
        return secNode
      })
    }
  }

  return { anchorNodes: laidOutAnchor, secondaryNodes: laidOutSecondary }
}
