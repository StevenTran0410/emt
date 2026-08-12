"""BD Flow Extractor: Deterministically builds flow graph skeleton from BD ParsedDoc."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ._mermaid_parser import (
    MermaidBlock,
    parse_flowchart,
    parse_state_diagram,
)
from .types import ParsedDoc


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class BdFlowResult:
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)


def extract_bd_flow(doc: ParsedDoc, doc_id: str, cluster_id: str) -> BdFlowResult:
    """Extract deterministic BD flow graph skeleton (nodes, edges, diagnostics)."""
    res = BdFlowResult()
    now = _utc_now_iso()

    raw_lines = doc.raw_content.splitlines()

    # 1. Determine sub-report boundaries based on top-level section titles
    h1_positions: list[tuple[int, str]] = []
    for idx, line in enumerate(raw_lines, 1):
        if line.startswith("# ") and not line.startswith("## "):
            title = line[2:].strip()
            h1_positions.append((idx, title))

    def get_subreport_ix(doc_line: int) -> int:
        curr_ix = 0
        for line_num, title in h1_positions:
            t_clean = title.strip().upper()
            if line_num <= doc_line:
                if t_clean == "SCREEN SPEC":
                    curr_ix = 1
                elif t_clean == "BUSINESS FLOW":
                    curr_ix = 2
                elif t_clean == "JOB FLOW DIAGRAM":
                    curr_ix = 3
                elif t_clean == "PROGRAM DEPENDENCIES":
                    curr_ix = 4
                elif t_clean in ("UTILITY", "DATASET"):
                    curr_ix = 5
                elif t_clean == "EVENT FLOWS":
                    curr_ix = 6
        return curr_ix

    # 2. Extract Event Categories tables (D2 event nodes)
    event_table_nodes: dict[str, dict[str, Any]] = {}
    for table_ix, table in enumerate(doc.tables):
        headers = [h.strip() for h in table.get("headers", [])]
        if "Category" in headers and "Event ID" in headers:
            rows = table.get("rows", [])
            t_line = table.get("line_start", 1)
            sub_ix = get_subreport_ix(t_line)

            for r_ix, row in enumerate(rows):
                cat_val = row.get("Category", "").strip()
                ev_id = row.get("Event ID", "").strip()
                trig_val = row.get("Trigger", "").strip()
                out_val = row.get("Outcome", "").strip()

                if ev_id:
                    node_id = f"bdflow:{cluster_id}:{doc_id}:{sub_ix}:evtab:{ev_id}"
                    event_table_nodes[ev_id] = {
                        "id": node_id,
                        "cluster_id": cluster_id,
                        "doc_id": doc_id,
                        "node_kind": "event",
                        "local_id": ev_id,
                        "binding": ev_id,
                        "binding_type": "event",
                        "label": f"{cat_val}: {ev_id}",
                        "ordinal": r_ix,
                        "guard_text": trig_val,
                        "source_locator": None,
                        "provenance_tier": "D2",
                        "doc_line_start": t_line + r_ix + 2,
                        "doc_line_end": t_line + r_ix + 2,
                        "attributes": json.dumps({
                            "category": cat_val,
                            "trigger": trig_val,
                            "outcome": out_val,
                            "sources": ["event_categories_table"],
                        }),
                        "created_at": now,
                    }

    merged_event_ids: set[str] = set()
    extracted_nodes: list[dict[str, Any]] = []
    extracted_edges: list[dict[str, Any]] = []

    block_id_map: dict[tuple[int, str], str] = {}

    # 3. Process Mermaid blocks
    for block_ix, mb in enumerate(doc.mermaid_diagrams):
        sub_ix = get_subreport_ix(mb.fence_line)
        b_lines = [l.strip() for l in mb.text.splitlines() if l.strip()]
        first_line = b_lines[0] if b_lines else ""

        # Dialect detection
        if first_line.startswith("stateDiagram"):
            state_res = parse_state_diagram(mb)
            res.diagnostics.extend(state_res.diagnostics)

            notes_by_target: dict[str, list[str]] = {}
            for note in state_res.notes:
                notes_by_target.setdefault(note.target_local_id, []).append(note.text)

            for node in state_res.nodes:
                local_id = node.local_id
                global_id = f"bdflow:{cluster_id}:{doc_id}:{sub_ix}:{block_ix}:{local_id}"

                if node.node_kind == "event" and node.binding and node.binding in event_table_nodes:
                    ev_id = node.binding
                    merged_event_ids.add(ev_id)
                    tab_node = event_table_nodes[ev_id]

                    tab_attrs = json.loads(tab_node["attributes"])
                    tab_attrs["sources"] = ["stateDiagram", "event_categories_table"]
                    tab_attrs["diagram_label"] = node.display_label
                    if local_id in notes_by_target:
                        tab_attrs["notes"] = notes_by_target[local_id]

                    tab_node["provenance_tier"] = "D1,D2"
                    tab_node["doc_line_start"] = min(tab_node["doc_line_start"], node.doc_line)
                    tab_node["doc_line_end"] = max(tab_node["doc_line_end"], node.doc_line)
                    tab_node["attributes"] = json.dumps(tab_attrs)

                    block_id_map[(block_ix, local_id)] = tab_node["id"]
                else:
                    attrs = {}
                    if local_id in notes_by_target:
                        attrs["notes"] = notes_by_target[local_id]

                    n_dict = {
                        "id": global_id,
                        "cluster_id": cluster_id,
                        "doc_id": doc_id,
                        "node_kind": node.node_kind,
                        "local_id": local_id,
                        "binding": node.binding,
                        "binding_type": node.binding_type,
                        "label": node.display_label,
                        "ordinal": None,
                        "guard_text": None,
                        "source_locator": None,
                        "provenance_tier": "D1",
                        "doc_line_start": node.doc_line,
                        "doc_line_end": node.doc_line,
                        "attributes": json.dumps(attrs) if attrs else None,
                        "created_at": now,
                    }
                    extracted_nodes.append(n_dict)
                    block_id_map[(block_ix, local_id)] = global_id

            for e_ix, edge in enumerate(state_res.edges):
                src_global = block_id_map.get((block_ix, edge.src_local_id))
                dst_global = block_id_map.get((block_ix, edge.dst_local_id))

                if src_global and dst_global:
                    edge_id = f"bdflow:{cluster_id}:{doc_id}:{sub_ix}:{block_ix}:edge:{e_ix}"
                    extracted_edges.append({
                        "id": edge_id,
                        "cluster_id": cluster_id,
                        "doc_id": doc_id,
                        "src_node_id": src_global,
                        "dst_node_id": dst_global,
                        "edge_kind": "transition",
                        "label": edge.label,
                        "guard_text": edge.guard_text,
                        "provenance_tier": "D1",
                        "doc_line": edge.doc_line,
                        "attributes": None,
                        "created_at": now,
                    })

        elif first_line.startswith("flowchart") or first_line.startswith("graph"):
            if sub_ix == 3:
                res.diagnostics.append(f"skipped_redundant_flowchart at fence_line {mb.fence_line}")
                continue

            flow_res = parse_flowchart(mb)
            res.diagnostics.extend(flow_res.diagnostics)

            for node in flow_res.nodes:
                local_id = node.local_id
                global_id = f"bdflow:{cluster_id}:{doc_id}:{sub_ix}:{block_ix}:{local_id}"
                n_dict = {
                    "id": global_id,
                    "cluster_id": cluster_id,
                    "doc_id": doc_id,
                    "node_kind": "asset",
                    "local_id": local_id,
                    "binding": node.binding,
                    "binding_type": node.binding_type,
                    "label": node.display_label,
                    "ordinal": None,
                    "guard_text": None,
                    "source_locator": None,
                    "provenance_tier": "D3",
                    "doc_line_start": node.doc_line,
                    "doc_line_end": node.doc_line,
                    "attributes": None,
                    "created_at": now,
                }
                extracted_nodes.append(n_dict)
                block_id_map[(block_ix, local_id)] = global_id

            for e_ix, edge in enumerate(flow_res.edges):
                src_global = block_id_map.get((block_ix, edge.src_local_id))
                dst_global = block_id_map.get((block_ix, edge.dst_local_id))

                if src_global and dst_global:
                    edge_id = f"bdflow:{cluster_id}:{doc_id}:{sub_ix}:{block_ix}:edge:{e_ix}"
                    attrs = {}
                    if edge.edge_type:
                        attrs["edge_type"] = edge.edge_type
                    if edge.resolution_mark:
                        attrs["resolution_mark"] = edge.resolution_mark

                    extracted_edges.append({
                        "id": edge_id,
                        "cluster_id": cluster_id,
                        "doc_id": doc_id,
                        "src_node_id": src_global,
                        "dst_node_id": dst_global,
                        "edge_kind": "dependency",
                        "label": edge.label,
                        "guard_text": None,
                        "provenance_tier": "D3",
                        "doc_line": edge.doc_line,
                        "attributes": json.dumps(attrs) if attrs else None,
                        "created_at": now,
                    })

    for ev_id, ev_node in event_table_nodes.items():
        extracted_nodes.append(ev_node)

    # 4. Extract Execution Sequence tables (D2 job_step nodes & precedes edges)
    for table_ix, table in enumerate(doc.tables):
        headers = [h.strip() for h in table.get("headers", [])]
        if "Step Name" in headers and "Pass / Iteration" in headers and "Program / Procedure" in headers:
            rows = table.get("rows", [])
            t_line = table.get("line_start", 1)
            sub_ix = get_subreport_ix(t_line)

            prev_step_node_id: str | None = None

            for r_ix, row in enumerate(rows):
                step_val = row.get("Step Name", "").strip()
                pass_val = row.get("Pass / Iteration", "").strip()
                prog_val = row.get("Program / Procedure", "").strip()
                in_val = row.get("Inputs", "").strip()
                out_val = row.get("Outputs", "").strip()
                cond_val = row.get("Condition / Trigger", "").strip()
                source_val = row.get("Source", "").strip()
                purp_val = row.get("Purpose", "").strip()

                loc_match = re.search(r"^(.+?)@L(\d+)$", step_val.strip("() "))
                if loc_match:
                    local_id = loc_match.group(1).strip()
                    source_locator = f"L{loc_match.group(2)}"
                else:
                    local_id = step_val
                    source_locator = None

                inputs_list = [s.strip() for s in re.split(r",|<br/>|<br>", in_val) if s.strip() and s.strip() != "—"]
                outputs_list = [s.strip() for s in re.split(r",|<br/>|<br>", out_val) if s.strip() and s.strip() != "—"]

                node_id = f"bdflow:{cluster_id}:{doc_id}:{sub_ix}:exseq:{table_ix}:{r_ix}:{local_id}"

                attrs = {
                    "pass_iteration": pass_val,
                    "program_procedure": prog_val,
                    "inputs": inputs_list,
                    "outputs": outputs_list,
                    "source": source_val,
                    "purpose": purp_val,
                }

                n_dict = {
                    "id": node_id,
                    "cluster_id": cluster_id,
                    "doc_id": doc_id,
                    "node_kind": "job_step",
                    "local_id": local_id,
                    "binding": prog_val if prog_val and prog_val != "—" else local_id,
                    "binding_type": "program",
                    "label": f"{local_id} ({prog_val})" if prog_val and prog_val != "—" else local_id,
                    "ordinal": r_ix,
                    "guard_text": cond_val if cond_val and cond_val != "—" else None,
                    "source_locator": source_locator,
                    "provenance_tier": "D2",
                    "doc_line_start": t_line + r_ix + 2,
                    "doc_line_end": t_line + r_ix + 2,
                    "attributes": json.dumps(attrs),
                    "created_at": now,
                }
                extracted_nodes.append(n_dict)

                if prev_step_node_id:
                    edge_id = f"bdflow:{cluster_id}:{doc_id}:{sub_ix}:exseq:{table_ix}:prec:{r_ix}"
                    extracted_edges.append({
                        "id": edge_id,
                        "cluster_id": cluster_id,
                        "doc_id": doc_id,
                        "src_node_id": prev_step_node_id,
                        "dst_node_id": node_id,
                        "edge_kind": "precedes",
                        "label": None,
                        "guard_text": None,
                        "provenance_tier": "D2",
                        "doc_line": t_line + r_ix + 2,
                        "attributes": None,
                        "created_at": now,
                    })

                prev_step_node_id = node_id

    # Deduplicate nodes and edges by Primary Key id
    dedup_nodes: list[dict[str, Any]] = []
    seen_node_ids: set[str] = set()
    for n in extracted_nodes:
        if n["id"] not in seen_node_ids:
            seen_node_ids.add(n["id"])
            dedup_nodes.append(n)

    dedup_edges: list[dict[str, Any]] = []
    seen_edge_ids: set[str] = set()
    for e in extracted_edges:
        if e["id"] not in seen_edge_ids:
            seen_edge_ids.add(e["id"])
            dedup_edges.append(e)

    res.nodes = dedup_nodes
    res.edges = dedup_edges
    return res
