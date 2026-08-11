import React from 'react'
import { X, AlertTriangle, CheckCircle2, XCircle, FileText, Code2, Link2, Info, Table2 } from 'lucide-react'
import type { LinkedGraphNode, LinkedGraphEdge, CrossLinkItem } from '../../../types/electron'
import { VERDICT_STYLES } from './encoding'

export interface BdGroupDetail {
  label: string
  section_id?: string | null
  members: { id: string; display_name: string; node_type: string }[]
  evidence: any[]
}

export interface ExplainPanelProps {
  selectedNode?: LinkedGraphNode | null
  selectedEdge?: LinkedGraphEdge | null
  crossLink?: CrossLinkItem | null
  // Fields grouped under the selected node (hidden from the graph, shown here as a table).
  fields?: LinkedGraphNode[]
  // A clicked BD hull: what business-design section it is and which DD flows it wraps.
  bdGroup?: BdGroupDetail | null
  onClose: () => void
}

export function ExplainPanel({
  selectedNode,
  selectedEdge,
  crossLink,
  fields,
  bdGroup,
  onClose
}: ExplainPanelProps): React.ReactElement | null {
  if (!selectedNode && !selectedEdge && !bdGroup) return null

  return (
    <div className="fixed inset-y-0 right-0 z-50 flex w-96 flex-col border-l border-zinc-800 bg-zinc-950 p-5 shadow-2xl text-zinc-100 animate-in slide-in-from-right duration-200">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-zinc-800 pb-3">
        <div className="flex items-center gap-2">
          <Info className="h-5 w-5 text-cyan-400" />
          <h3 className="text-base font-semibold text-zinc-100">
            {selectedNode
              ? 'Entity Alignment Audit'
              : selectedEdge
                ? 'Relation Alignment Audit'
                : 'BD Section'}
          </h3>
        </div>
        <button
          onClick={onClose}
          className="rounded-lg p-1 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="mt-4 flex-1 overflow-y-auto space-y-5 text-xs pr-1">
        {/* Clicked BD hull: what this business-design section is and what it wraps */}
        {bdGroup && (
          <>
            <div className="rounded-lg border border-blue-500/30 bg-blue-950/20 p-3.5 space-y-1">
              <div className="flex items-center gap-1.5 text-blue-300 font-semibold">
                <FileText className="h-4 w-4" /> {bdGroup.label}
              </div>
              <div className="text-[11px] text-zinc-400">
                Business-design section{bdGroup.section_id ? ` §${bdGroup.section_id}` : ''} — it
                groups the DD flows below (derived from the BD document&apos;s section structure).
              </div>
            </div>

            <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-3.5 space-y-2">
              <div className="flex items-center justify-between text-zinc-300 font-medium">
                <span className="flex items-center gap-1.5">
                  <Table2 className="h-3.5 w-3.5 text-sky-400" /> Covers (DD flows)
                </span>
                <span className="text-[10px] text-zinc-500 font-mono">{bdGroup.members.length}</span>
              </div>
              <div className="space-y-1 max-h-72 overflow-y-auto">
                {bdGroup.members.map((m) => (
                  <div
                    key={m.id}
                    className="flex items-center justify-between rounded bg-zinc-950 px-2 py-1 border border-zinc-800 text-[11px]"
                  >
                    <span className="font-mono text-zinc-200 truncate">{m.display_name}</span>
                    <span className="text-[9px] uppercase text-zinc-500 font-mono shrink-0 ml-2">
                      {m.node_type}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            {bdGroup.evidence && bdGroup.evidence.length > 0 && (
              <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-3.5 space-y-1.5">
                <div className="flex items-center gap-1.5 text-zinc-300 font-medium">
                  <FileText className="h-3.5 w-3.5 text-purple-400" /> Evidence
                </div>
                <div className="space-y-1 max-h-28 overflow-y-auto">
                  {bdGroup.evidence.slice(0, 20).map((ev, i) => (
                    <div
                      key={i}
                      className="rounded bg-zinc-950 p-1.5 font-mono text-[10px] text-zinc-400 border border-zinc-800 truncate"
                    >
                      {ev.doc_id || 'BD'}
                      {ev.doc_span?.section_id ? ` · §${ev.doc_span.section_id}` : ''}
                      {ev.doc_span?.line_start ? ` · L${ev.doc_span.line_start}` : ''}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}

        {/* Selected Node Details */}
        {selectedNode && (
          <>
            {/* Identity & Verdict */}
            <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-3.5 space-y-2">
              <div className="flex items-center justify-between">
                <span className="font-mono text-xs font-semibold text-zinc-200">
                  {selectedNode.display_name}
                </span>
                <span
                  className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${
                    selectedNode.entity_verdict === 'matched'
                      ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/40'
                      : selectedNode.entity_verdict === 'missing'
                      ? 'bg-rose-500/20 text-rose-400 border border-rose-500/40'
                      : 'bg-amber-500/20 text-amber-400 border border-amber-500/40'
                  }`}
                >
                  {selectedNode.entity_verdict.toUpperCase()}
                </span>
              </div>
              <div className="text-[11px] text-zinc-400 font-mono">ID: {selectedNode.id}</div>
              <div className="flex items-center gap-2 text-[11px]">
                <span className="text-zinc-400">Layer presence:</span>
                {selectedNode.in_bd && <span className="px-1.5 py-0.5 rounded bg-blue-500/20 text-blue-300 font-mono">BD</span>}
                {selectedNode.in_dd && <span className="px-1.5 py-0.5 rounded bg-purple-500/20 text-purple-300 font-mono">DD</span>}
                {selectedNode.in_code && <span className="px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-300 font-mono">CODE</span>}
              </div>
            </div>

            {/* Fields folded into this node (hidden from the graph) — shown here as a table. */}
            {fields && fields.length > 0 && (
              <div className="rounded-lg border border-sky-500/30 bg-sky-950/20 p-3.5 space-y-2">
                <div className="flex items-center justify-between text-sky-200 font-medium">
                  <span className="flex items-center gap-1.5">
                    <Table2 className="h-3.5 w-3.5 text-sky-400" /> Fields
                  </span>
                  <span className="text-[10px] text-zinc-400 font-mono">{fields.length}</span>
                </div>
                <div className="max-h-64 overflow-y-auto rounded border border-zinc-800">
                  <table className="w-full text-[11px] font-mono">
                    <thead className="sticky top-0 bg-zinc-950 text-zinc-500">
                      <tr>
                        <th className="text-left px-2 py-1 font-medium">Field</th>
                        <th className="text-left px-2 py-1 font-medium">Datatype (PIC)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {fields.map((f) => (
                        <tr key={f.id} className="border-t border-zinc-800/60">
                          <td className="px-2 py-1 text-zinc-200 truncate max-w-[150px]">
                            {f.display_name.includes('.')
                              ? f.display_name.split('.').slice(1).join('.')
                              : f.display_name}
                          </td>
                          <td className="px-2 py-1 text-sky-300">{f.attributes?.pic || '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {/* Cross-Link Join Status */}
            {crossLink && (
              <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-3.5 space-y-2">
                <div className="flex items-center justify-between text-zinc-300 font-medium">
                  <span className="flex items-center gap-1.5">
                    <Link2 className="h-3.5 w-3.5 text-cyan-400" /> Cross-Link Resolution
                  </span>
                  <span className="font-mono text-[10px] uppercase text-zinc-400">
                    {crossLink.match_method}
                  </span>
                </div>

                {crossLink.match_method === 'name_fallback' && (
                  <div className="flex items-start gap-2 rounded-md bg-amber-500/10 p-2 text-amber-400 text-[11px] border border-amber-500/20">
                    <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
                    <div>
                      <div className="font-semibold">Name Fallback Resolution Used</div>
                      <div className="text-[10px] text-amber-300/80">
                        Exact semantic key match missing; joined by display name fallback.
                      </div>
                    </div>
                  </div>
                )}

                <div className="space-y-1">
                  <div className="text-zinc-400 text-[11px]">Mapped Source File:</div>
                  <div className="font-mono text-cyan-300 bg-zinc-950 p-2 rounded border border-zinc-800 truncate">
                    {crossLink.code_rel_path}
                  </div>
                </div>

                {crossLink.candidates.length > 1 && (
                  <div className="space-y-1">
                    <div className="text-amber-400 font-medium text-[11px]">
                      Ambiguous Candidates ({crossLink.candidates.length}):
                    </div>
                    <ul className="space-y-1 font-mono text-[10px] text-zinc-400 max-h-24 overflow-y-auto">
                      {crossLink.candidates.map((cand, idx) => (
                        <li key={idx} className="bg-zinc-950 px-2 py-1 rounded border border-zinc-800 truncate">
                          {cand}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )}

            {/* Provenance */}
            {selectedNode.provenance && selectedNode.provenance.length > 0 && (
              <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-3.5 space-y-2">
                <div className="flex items-center gap-1.5 text-zinc-300 font-medium">
                  <FileText className="h-3.5 w-3.5 text-purple-400" /> Document Provenance
                </div>
                <div className="space-y-1 max-h-32 overflow-y-auto">
                  {selectedNode.provenance.map((prov, i) => (
                    <div key={i} className="rounded bg-zinc-950 p-2 font-mono text-[10px] text-zinc-400 border border-zinc-800">
                      <div>Doc: {prov.doc_id || 'N/A'}</div>
                      {(prov.doc_span?.section_id ?? prov.section_id) && (
                        <div>Section: {prov.doc_span?.section_id ?? prov.section_id}</div>
                      )}
                      {(prov.doc_span?.line_start ?? prov.line) && (
                        <div>Line: {prov.doc_span?.line_start ?? prov.line}</div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}

        {/* Selected Edge Details */}
        {selectedEdge && (
          <>
            <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-3.5 space-y-3">
              <div className="flex items-center justify-between">
                <span className="font-mono text-xs font-semibold text-zinc-200 truncate">
                  {selectedEdge.edge_type}
                </span>
                <span
                  className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${
                    (VERDICT_STYLES[selectedEdge.endpoint_verdict] || VERDICT_STYLES.UNKNOWN).badgeBg
                  } ${(VERDICT_STYLES[selectedEdge.endpoint_verdict] || VERDICT_STYLES.UNKNOWN).badgeText}`}
                >
                  {selectedEdge.endpoint_verdict}
                </span>
              </div>

              <div className="space-y-1 font-mono text-[11px] bg-zinc-950 p-2 rounded border border-zinc-800">
                <div className="text-zinc-400">Src: <span className="text-zinc-200">{selectedEdge.src}</span></div>
                <div className="text-zinc-400">Dst: <span className="text-zinc-200">{selectedEdge.dst}</span></div>
              </div>

              {/* Two-Sided Eligibility */}
              <div className="space-y-1.5 pt-1">
                <div className="text-zinc-400 text-[11px]">Two-Sided Eligibility:</div>
                <div className="flex items-center justify-between bg-zinc-950 p-2 rounded border border-zinc-800">
                  <span className="text-zinc-300">Subject File Parse</span>
                  {selectedEdge.subject_ok ? (
                    <span className="flex items-center gap-1 text-emerald-400 font-medium">
                      <CheckCircle2 className="h-3.5 w-3.5" /> OK
                    </span>
                  ) : (
                    <span className="flex items-center gap-1 text-rose-400 font-medium">
                      <XCircle className="h-3.5 w-3.5" /> NON-OK
                    </span>
                  )}
                </div>
                <div className="flex items-center justify-between bg-zinc-950 p-2 rounded border border-zinc-800">
                  <span className="text-zinc-300">Object File Parse</span>
                  {selectedEdge.object_ok ? (
                    <span className="flex items-center gap-1 text-emerald-400 font-medium">
                      <CheckCircle2 className="h-3.5 w-3.5" /> OK
                    </span>
                  ) : (
                    <span className="flex items-center gap-1 text-rose-400 font-medium">
                      <XCircle className="h-3.5 w-3.5" /> NON-OK
                    </span>
                  )}
                </div>
              </div>

              {/* Count Delta */}
              <div className="flex items-center justify-between bg-zinc-950 p-2.5 rounded border border-zinc-800">
                <div>
                  <div className="text-zinc-400 text-[10px]">Doc Sites</div>
                  <div className="text-zinc-100 font-bold text-sm">{selectedEdge.doc_count}</div>
                </div>
                <div className="text-center">
                  <div className="text-zinc-400 text-[10px]">Multiplicity</div>
                  <div className="text-amber-400 font-medium text-[11px]">{selectedEdge.multiplicity_verdict}</div>
                </div>
                <div className="text-right">
                  <div className="text-zinc-400 text-[10px]">Code Sites</div>
                  <div className="text-zinc-100 font-bold text-sm">{selectedEdge.code_count}</div>
                </div>
              </div>

              {selectedEdge.count_differs && (
                <div className="flex items-center gap-2 rounded bg-amber-500/10 p-2 text-amber-300 text-[11px] border border-amber-500/20">
                  <AlertTriangle className="h-4 w-4 shrink-0" />
                  <span>Count mismatch hint: Doc count ({selectedEdge.doc_count}) != Code count ({selectedEdge.code_count})</span>
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
