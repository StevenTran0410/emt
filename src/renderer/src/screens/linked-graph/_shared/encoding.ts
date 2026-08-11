import type { CSSProperties } from 'react'

/** Verdict encoding tokens & color palette (Ticket 1 §6.3).
 * DOC_ONLY and CODE_ONLY MUST be different hues.
 * MATCH: Emerald (#10b981 / emerald-500)
 * DOC_ONLY: Amber (#f59e0b / amber-500)
 * CODE_ONLY: Sky/Cyan (#06b6d4 / sky-500)
 * UNKNOWN: Rose/Dashed (#f43f5e / rose-500 dashed)
 */

// Node-TYPE palette — kept identical to the doc-graph screen (DocGraphModal.getNodeStyle)
// so the same entity reads with the same colour in both views. Fill/border = type identity;
// the audit verdict is layered on top as a coloured ring (see linkedNodeStyle).
export const NODE_TYPE_HEX: Record<string, string> = {
  program: '#6366f1',
  dataset: '#10b981',
  step: '#0ea5e9',
  dd: '#3b82f6',
  copybook: '#8b5cf6',
  job: '#f59e0b',
  extroutine: '#f43f5e',
  br: '#2dd4bf',
  ddlimit: '#fb923c',
  tbd: '#facc15',
  capability: '#e879f9',
  doc: '#22d3ee',
  actor: '#94a3b8',
  field: '#71717a',
  code: '#06b6d4' // a resolved source file in the code lane
}

export function nodeTypeHex(nodeType: string): string {
  return NODE_TYPE_HEX[nodeType] ?? '#71717a'
}

export function hexToRgba(hex: string, alpha: number): string {
  const h = hex.replace('#', '')
  const r = parseInt(h.substring(0, 2), 16)
  const g = parseInt(h.substring(2, 4), 16)
  const b = parseInt(h.substring(4, 6), 16)
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

// Node entity_verdict -> ring colour. `matched` gets no ring (clean, border already = type
// colour); the misses get a loud ring so they pop against the matched majority.
export function entityVerdictRing(verdict?: string): string | null {
  switch (verdict) {
    case 'undocumented':
      return '#06b6d4' // code has it, doc doesn't (code-only hue)
    case 'missing':
      return '#f59e0b' // doc has it, code doesn't (doc-only hue)
    case 'unknown':
      return '#f43f5e'
    case 'out_of_scope':
      return '#a1a1aa'
    default:
      return null // matched
  }
}

export interface DocEdgeVisual {
  stroke: string
  strokeWidth: number
  strokeDasharray: string
  opacity: number
  animated: boolean
  isMiss: boolean
}

/** Edge visual: only ASSESSED relations (calls/copies/runs/binds_dd) carry a verdict colour;
 * documentation-internal edges (defines/rule_about/cites/references/accesses) render neutral. */
export function docEdgeVisual(
  e: { assessed: boolean; endpoint_verdict: string },
  showOnlyUnmatched: boolean
): DocEdgeVisual {
  const isMiss = e.assessed && e.endpoint_verdict !== 'MATCH'
  const dimmed = showOnlyUnmatched && !isMiss
  const stroke = !e.assessed
    ? '#3f3f46'
    : e.endpoint_verdict === 'MATCH'
      ? '#10b981'
      : e.endpoint_verdict === 'DOC_ONLY'
        ? '#f59e0b'
        : '#06b6d4'
  return {
    stroke,
    strokeWidth: isMiss ? 2.5 : 1,
    strokeDasharray: e.assessed && e.endpoint_verdict === 'UNKNOWN' ? '5,5' : 'none',
    opacity: dimmed ? 0.15 : e.assessed ? 1 : 0.45,
    animated: isMiss && e.endpoint_verdict === 'DOC_ONLY',
    isMiss
  }
}

export interface LinkedNodeStyleOpts {
  verdict?: string
  selected?: boolean
  dimmed?: boolean
  // Force the ring colour (e.g. membership in BD+DD view, or ambiguity). null = no ring.
  ringOverride?: string | null
}

/** Inline React Flow node style: fill/border by node TYPE (matches the doc graph), verdict as a ring. */
export function linkedNodeStyle(nodeType: string, opts: LinkedNodeStyleOpts = {}): CSSProperties {
  const typeHex = nodeTypeHex(nodeType)
  const ring =
    opts.ringOverride !== undefined ? opts.ringOverride : entityVerdictRing(opts.verdict)
  return {
    background: hexToRgba(typeHex, 0.13),
    borderColor: opts.selected ? '#38bdf8' : typeHex,
    borderWidth: opts.selected ? 2 : 1,
    borderStyle: 'solid',
    borderRadius: 8,
    color: '#f4f4f5',
    padding: '6px 10px',
    opacity: opts.dimmed ? 0.2 : 1,
    boxShadow: opts.selected
      ? '0 0 12px rgba(56, 189, 248, 0.4)'
      : ring
        ? `0 0 0 2px ${ring}, 0 0 8px ${hexToRgba(ring, 0.4)}`
        : 'none'
  }
}

export interface VerdictStyle {
  label: string
  ringColor: string
  badgeBg: string
  badgeText: string
  borderColor: string
  borderStyle: string
}

export const VERDICT_STYLES: Record<string, VerdictStyle> = {
  MATCH: {
    label: 'Matched',
    ringColor: 'ring-emerald-500',
    badgeBg: 'bg-emerald-500/10 dark:bg-emerald-500/20',
    badgeText: 'text-emerald-700 dark:text-emerald-400',
    borderColor: 'border-emerald-500',
    borderStyle: 'solid'
  },
  DOC_ONLY: {
    label: 'Doc Only',
    ringColor: 'ring-amber-500',
    badgeBg: 'bg-amber-500/10 dark:bg-amber-500/20',
    badgeText: 'text-amber-700 dark:text-amber-400',
    borderColor: 'border-amber-500',
    borderStyle: 'solid'
  },
  CODE_ONLY: {
    label: 'Code Only',
    ringColor: 'ring-sky-500',
    badgeBg: 'bg-sky-500/10 dark:bg-sky-500/20',
    badgeText: 'text-sky-700 dark:text-sky-400',
    borderColor: 'border-sky-500',
    borderStyle: 'solid'
  },
  UNKNOWN: {
    label: 'Unproven',
    ringColor: 'ring-rose-500/80',
    badgeBg: 'bg-rose-500/10 dark:bg-rose-500/20',
    badgeText: 'text-rose-700 dark:text-rose-400',
    borderColor: 'border-rose-500/80',
    borderStyle: 'dashed'
  }
}
