"""Helper module for converting resolved COBOL/JCL facts into graph database rows."""
from __future__ import annotations

import json
from typing import Any, NamedTuple


class GraphEdgeRows(NamedTuple):
    file_edge_inputs: list[tuple[str, str, str, int]]  # (src_path, dst_path, edge_type, is_external)
    file_edge_rows: list[tuple]  # DB insert tuples for structural_graph_edges
    symbol_edge_rows: list[tuple]  # DB insert tuples for symbol_graph_edges


def convert_resolved_edges_to_rows(
    snapshot_id: str,
    resolved_edges: list[Any],
    now_iso: str,
) -> GraphEdgeRows:
    """Convert resolved COBOL/JCL edges into DB row tuples."""
    file_edge_inputs: list[tuple[str, str, str, int]] = []
    file_edge_rows: list[tuple] = []
    
    # Map (src_symbol, dst_symbol, edge_type) -> (confidence_score, resolution_method, set_of_lines)
    symbol_edge_map: dict[tuple[str, str, str], tuple[float, str, set[int]]] = {}

    for edge in resolved_edges:
        file_edge_inputs.append((edge.src_file, edge.dst_file, edge.edge_type, int(edge.is_external)))
        file_edge_rows.append(
            (
                snapshot_id,
                edge.src_file,
                edge.dst_file,
                edge.edge_type,
                int(edge.is_external),
                now_iso,
                edge.confidence_score,
                edge.resolution_method,
            )
        )

        # Only emit symbol edge if dst_symbol is present and not external/unresolved
        if edge.dst_symbol and not edge.is_external:
            key = (edge.src_symbol, edge.dst_symbol, edge.edge_type)
            if key not in symbol_edge_map:
                symbol_edge_map[key] = (
                    edge.confidence_score,
                    edge.resolution_method,
                    set(edge.evidence_lines),
                )
            else:
                conf, res_m, lines = symbol_edge_map[key]
                lines.update(edge.evidence_lines)

    symbol_edge_rows: list[tuple] = []
    for (src_sym, dst_sym, edge_t), (conf_score, res_m, lines) in symbol_edge_map.items():
        sorted_lines = sorted(lines)
        symbol_edge_rows.append(
            (
                snapshot_id,
                src_sym,
                dst_sym,
                edge_t,
                conf_score,
                res_m,
                "high" if conf_score >= 0.7 else "low",
                json.dumps(sorted_lines),
            )
        )

    return GraphEdgeRows(
        file_edge_inputs=file_edge_inputs,
        file_edge_rows=file_edge_rows,
        symbol_edge_rows=symbol_edge_rows,
    )
