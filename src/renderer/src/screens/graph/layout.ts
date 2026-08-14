import { type Node, type Edge, MarkerType } from '@xyflow/react'
import dagre from '@dagrejs/dagre'
import * as d3force from 'd3-force'

export interface DagreLayoutOptions {
  rankdir?: 'TB' | 'LR' | 'BT' | 'RL'
  nodesep?: number
  ranksep?: number
  nodeWidth?: number
  nodeHeight?: number
}

export function applyDagreLayout(
  nodes: Node[],
  edges: Edge[],
  options: DagreLayoutOptions = {}
): Node[] {
  const rankdir = options.rankdir ?? 'LR'
  const nodesep = options.nodesep ?? 40
  const ranksep = options.ranksep ?? 80
  const nodeWidth = options.nodeWidth ?? 160
  const nodeHeight = options.nodeHeight ?? 36

  const g = new dagre.graphlib.Graph()
  g.setGraph({ rankdir, nodesep, ranksep })
  g.setDefaultEdgeLabel(() => ({}))

  nodes.forEach((n) => g.setNode(n.id, { width: nodeWidth, height: nodeHeight }))
  edges.forEach((e) => g.setEdge(e.source, e.target))

  dagre.layout(g)

  return nodes.map((n) => {
    const pos = g.node(n.id)
    return { ...n, position: { x: pos.x - nodeWidth / 2, y: pos.y - nodeHeight / 2 } }
  })
}

export function applyComponentBandedLayout(
  nodes: Node[],
  edges: Edge[],
  options: DagreLayoutOptions & { componentGapY?: number } = {}
): Node[] {
  if (nodes.length === 0) return []

  const rankdir = options.rankdir ?? 'LR'
  const nodesep = options.nodesep ?? 70
  const ranksep = options.ranksep ?? 120
  const componentGapY = options.componentGapY ?? 80
  const nodeHeight = options.nodeHeight ?? 36

  const adj = new Map<string, Set<string>>()
  nodes.forEach((n) => adj.set(n.id, new Set()))
  edges.forEach((e) => {
    if (adj.has(e.source) && adj.has(e.target)) {
      adj.get(e.source)!.add(e.target)
      adj.get(e.target)!.add(e.source)
    }
  })

  const visited = new Set<string>()
  const components: Node[][] = []
  const nodeMap = new Map(nodes.map((n) => [n.id, n]))

  nodes.forEach((node) => {
    if (visited.has(node.id)) return
    const compNodes: Node[] = []
    const queue = [node.id]
    visited.add(node.id)

    while (queue.length > 0) {
      const curr = queue.shift()!
      const nObj = nodeMap.get(curr)
      if (nObj) compNodes.push(nObj)

      adj.get(curr)?.forEach((neighbor) => {
        if (!visited.has(neighbor)) {
          visited.add(neighbor)
          queue.push(neighbor)
        }
      })
    }
    components.push(compNodes)
  })

  components.sort((a, b) => b.length - a.length)

  const resultNodes: Node[] = []
  let currentYOffset = 0

  components.forEach((compNodes) => {
    const compNodeIds = new Set(compNodes.map((n) => n.id))
    const compEdges = edges.filter((e) => compNodeIds.has(e.source) && compNodeIds.has(e.target))

    const laidOut = applyDagreLayout(compNodes, compEdges, {
      ...options,
      rankdir,
      nodesep,
      ranksep
    })

    if (laidOut.length === 0) return

    let minY = Infinity
    let maxY = -Infinity
    laidOut.forEach((n) => {
      if (n.position.y < minY) minY = n.position.y
      if (n.position.y > maxY) maxY = n.position.y
    })

    const compHeight = maxY - minY + nodeHeight

    const shifted = laidOut.map((n) => ({
      ...n,
      position: {
        x: n.position.x,
        y: n.position.y - minY + currentYOffset
      }
    }))

    resultNodes.push(...shifted)
    currentYOffset += compHeight + componentGapY
  })

  return resultNodes
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

export interface BusinessFlowSkeletonOptions {
  maxCols?: number
  paddingX?: number
  paddingY?: number
  titleOffset?: number
}

/**
 * Pure/deterministic Business Flow Skeleton layout engine (TICKET P4-1-UI).
 * Lays out step nodes + branch edges per flow using Dagre TB, stacks flows into a grid,
 * and emitsGroupHullNode background containers.
 */
export function projectBusinessFlowSkeleton(
  flows: any[],
  options: BusinessFlowSkeletonOptions = {}
): { nodes: Node[]; edges: Edge[] } {
  if (!flows || flows.length === 0) {
    return { nodes: [], edges: [] }
  }

  const paddingX = options.paddingX ?? 24
  const paddingY = options.paddingY ?? 24
  const titleOffset = options.titleOffset ?? 36

  // Sort flows by (ordinal, id)
  const sortedFlows = [...flows].sort((a, b) => {
    if (a.ordinal !== b.ordinal) return a.ordinal - b.ordinal
    return String(a.id).localeCompare(String(b.id))
  })

  const maxCols = options.maxCols ?? (sortedFlows.length > 6 ? 2 : 1)

  const allNodes: Node[] = []
  const allEdges: Edge[] = []

  const colWidths: number[] = new Array(maxCols).fill(0)
  const rowHeights: number[] = []

  const flowLayouts: {
    flow: any
    nodes: Node[]
    edges: Edge[]
    bbox: { minX: number; minY: number; maxX: number; maxY: number; width: number; height: number }
    col: number
    row: number
  }[] = []

  sortedFlows.forEach((flow, index) => {
    const col = index % maxCols
    const row = Math.floor(index / maxCols)

    const stepNodes: Node[] = (flow.steps || []).map((step: any) => ({
      id: step.id,
      type: 'businessStep',
      position: { x: 0, y: 0 },
      width: 220,
      height: 80,
      style: { width: 220, height: 80 },
      data: {
        step,
        flow,
        flowId: flow.id,
        name: step.name,
        functionality: step.functionality,
        origin: flow.origin
      }
    }))

    const stepIdSet = new Set(stepNodes.map((n) => n.id))
    const endNodes: Node[] = []
    const branchEdges: Edge[] = []

    ;(flow.branches || []).forEach((branch: any) => {
      const hasTarget = branch.target_step_id && stepIdSet.has(branch.target_step_id)
      const targetId = hasTarget ? branch.target_step_id! : `${branch.id}:end`

      if (!hasTarget) {
        endNodes.push({
          id: `${branch.id}:end`,
          type: 'branchEnd',
          position: { x: 0, y: 0 },
          width: 100,
          height: 32,
          style: { width: 100, height: 32 },
          data: {
            kind: branch.branch_kind,
            branch,
            flowId: flow.id
          }
        })
      }

      let strokeColor = '#6b7280' // OTHER
      if (branch.branch_kind === 'SUCCESS') strokeColor = '#10b981'
      else if (branch.branch_kind === 'FAILURE') strokeColor = '#ef4444'
      else if (branch.branch_kind === 'ERROR') strokeColor = '#f59e0b'

      const g = branch.guard_description || ''
      const short = g.length > 22 ? g.slice(0, 22) + '…' : g

      branchEdges.push({
        id: branch.id,
        source: branch.source_step_id,
        target: targetId,
        type: 'businessBranch',
        style: { stroke: strokeColor, strokeWidth: 1.5 },
        markerEnd: { type: MarkerType.ArrowClosed, width: 18, height: 18, color: strokeColor },
        data: {
          label: short,
          guard: branch.guard_description,
          strokeColor,
          branch_kind: branch.branch_kind,
          branch,
          labelOffset: { dx: 0, dy: 0 }
        }
      })
    })

    const flowNodes = [...stepNodes, ...endNodes]
    if (flowNodes.length === 0) return

    // Run Dagre TB layout for this single flow
    const laidOutFlowNodes = applyDagreLayout(flowNodes, branchEdges, {
      rankdir: 'TB',
      nodeWidth: 220,
      nodeHeight: 80,
      nodesep: 90,
      ranksep: 110
    })

    let minX = Infinity
    let minY = Infinity
    let maxX = -Infinity
    let maxY = -Infinity

    laidOutFlowNodes.forEach((n) => {
      const w = n.width || 220
      const h = n.height || 80
      if (n.position.x < minX) minX = n.position.x
      if (n.position.y < minY) minY = n.position.y
      if (n.position.x + w > maxX) maxX = n.position.x + w
      if (n.position.y + h > maxY) maxY = n.position.y + h
    })

    if (!isFinite(minX) || !isFinite(maxX)) {
      minX = 0
      minY = 0
      maxX = 220
      maxY = 80
    }

    const bboxWidth = Math.max(260, maxX - minX + paddingX * 2)
    const bboxHeight = Math.max(120, maxY - minY + paddingY * 2 + titleOffset)

    if (bboxWidth > colWidths[col]) colWidths[col] = bboxWidth
    if (rowHeights.length <= row) rowHeights[row] = bboxHeight
    else if (bboxHeight > rowHeights[row]) rowHeights[row] = bboxHeight

    flowLayouts.push({
      flow,
      nodes: laidOutFlowNodes,
      edges: branchEdges,
      bbox: { minX, minY, maxX, maxY, width: bboxWidth, height: bboxHeight },
      col,
      row
    })
  })

  // Calculate cumulative X offsets for columns and Y offsets for rows
  const colXOffset: number[] = [40]
  for (let c = 1; c < maxCols; c++) {
    colXOffset[c] = colXOffset[c - 1] + colWidths[c - 1] + 80
  }

  const rowYOffset: number[] = [40]
  for (let r = 1; r < rowHeights.length; r++) {
    rowYOffset[r] = rowYOffset[r - 1] + rowHeights[r - 1] + 60
  }

  // Shift nodes/edges into final grid position and emit GroupHullNode
  flowLayouts.forEach(({ flow, nodes, edges, bbox, col, row }) => {
    const gridX = colXOffset[col]
    const gridY = rowYOffset[row]

    const hullNode: Node = {
      id: `group:${flow.id}`,
      type: 'group',
      position: { x: gridX, y: gridY },
      style: { width: bbox.width, height: bbox.height },
      data: {
        label: flow.name,
        sublabel: `${flow.block_key} · ${flow.origin}`,
        width: bbox.width,
        height: bbox.height,
        accentColor: flow.origin === 'llm' ? '#6366f1' : '#f59e0b',
        flowId: flow.id
      },
      zIndex: -1,
      selectable: false,
      draggable: false
    }

    allNodes.push(hullNode)

    const shiftedNodes = nodes.map((n) => ({
      ...n,
      position: {
        x: n.position.x - bbox.minX + gridX + paddingX,
        y: n.position.y - bbox.minY + gridY + paddingY + titleOffset
      }
    }))

    allNodes.push(...shiftedNodes)
    allEdges.push(...edges)
  })

  return { nodes: allNodes, edges: allEdges }
}
