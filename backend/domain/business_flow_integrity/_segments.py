"""Deterministic route segment building for Phase 4 Big-Picture Business Flow Integrity."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from typing import Any

ALLOWED_FLOW_EDGE_TYPES = {"menu_option", "calls", "submits", "shows_panel", "stacks"}


@dataclass(frozen=True)
class Segment:
    segment_id: str
    node_ids: list[str]
    edge_ids: list[str]
    bindings: list[str]
    edge_kinds: list[str]
    rel_paths: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "node_ids": list(self.node_ids),
            "edge_ids": list(self.edge_ids),
            "bindings": list(self.bindings),
            "edge_kinds": list(self.edge_kinds),
            "rel_paths": list(self.rel_paths),
        }


async def build_route_segments(db: Any, snapshot_id: str) -> list[Segment]:
    """Walk code_flow_edges to extract maximal non-branching ordered program chains."""
    async with db.execute(
        "SELECT id, rel_path, node_kind, binding, binding_type, ordinal FROM code_flow_nodes WHERE snapshot_id=? ORDER BY ordinal ASC",
        (snapshot_id,),
    ) as cur:
        node_rows = [dict(r) for r in await cur.fetchall()]

    if not node_rows:
        return []

    node_by_id = {r["id"]: r for r in node_rows}

    async with db.execute(
        "SELECT id, src_node_id, dst_node_id, edge_kind FROM code_flow_edges WHERE snapshot_id=?",
        (snapshot_id,),
    ) as cur:
        edge_rows = [dict(r) for r in await cur.fetchall()]

    # Filter to allowed edge kinds & valid endpoint nodes
    allowed_edges = [
        e for e in edge_rows
        if e["edge_kind"] in ALLOWED_FLOW_EDGE_TYPES and e["src_node_id"] in node_by_id and e["dst_node_id"] in node_by_id
    ]

    out_edges: dict[str, list[dict[str, Any]]] = {}
    in_edges: dict[str, list[dict[str, Any]]] = {}
    for e in allowed_edges:
        out_edges.setdefault(e["src_node_id"], []).append(e)
        in_edges.setdefault(e["dst_node_id"], []).append(e)

    in_degree = {nid: len(in_edges.get(nid, [])) for nid in node_by_id}
    out_degree = {nid: len(out_edges.get(nid, [])) for nid in node_by_id}

    # Find starting nodes for segment building
    # Start at any node with in_degree != 1 (roots / fan-in junctions)
    start_nodes = [nid for nid in node_by_id if in_degree[nid] != 1]
    if not start_nodes and node_rows:
        # Cyclic graph without in_degree!=1 nodes -> pick smallest ordinal as root
        start_nodes = [node_rows[0]["id"]]

    visited_edge_ids: set[str] = set()
    segments: list[Segment] = []
    visited_nodes: set[str] = set()

    for start_id in start_nodes:
        outgoing = out_edges.get(start_id, [])
        if not outgoing and in_degree[start_id] == 0:
            # Isolated node -> single-node segment
            visited_nodes.add(start_id)
            seg_id = f"seg:{hashlib.sha1(start_id.encode('utf-8')).hexdigest()[:16]}"
            n_obj = node_by_id[start_id]
            b_list = [n_obj["binding"]] if n_obj.get("binding") else []
            r_list = [n_obj["rel_path"]] if n_obj.get("rel_path") else []
            segments.append(Segment(
                segment_id=seg_id,
                node_ids=[start_id],
                edge_ids=[],
                bindings=b_list,
                edge_kinds=[],
                rel_paths=r_list,
            ))
            continue

        for start_edge in outgoing:
            if start_edge["id"] in visited_edge_ids:
                continue

            seg_node_ids = [start_id]
            seg_edge_ids = []
            seg_edge_kinds = []

            curr_id = start_id
            curr_edge = start_edge

            while curr_edge and curr_edge["id"] not in visited_edge_ids:
                visited_edge_ids.add(curr_edge["id"])
                seg_edge_ids.append(curr_edge["id"])
                seg_edge_kinds.append(curr_edge["edge_kind"])
                next_id = curr_edge["dst_node_id"]
                seg_node_ids.append(next_id)

                # Check if we can extend chain
                if out_degree.get(next_id, 0) == 1 and in_degree.get(next_id, 0) == 1 and next_id not in seg_node_ids[:-1]:
                    next_out = out_edges.get(next_id, [])
                    curr_edge = next_out[0] if next_out else None
                    curr_id = next_id
                else:
                    break

            visited_nodes.update(seg_node_ids)

            seg_id = f"seg:{hashlib.sha1('|'.join(seg_node_ids).encode('utf-8')).hexdigest()[:16]}"
            b_list = [node_by_id[nid]["binding"] for nid in seg_node_ids if node_by_id[nid].get("binding")]
            r_list = [node_by_id[nid]["rel_path"] for nid in seg_node_ids if node_by_id[nid].get("rel_path")]

            segments.append(Segment(
                segment_id=seg_id,
                node_ids=seg_node_ids,
                edge_ids=seg_edge_ids,
                bindings=b_list,
                edge_kinds=seg_edge_kinds,
                rel_paths=r_list,
            ))

    # Catch any unvisited reachable nodes
    for nid, n_obj in node_by_id.items():
        if nid not in visited_nodes:
            visited_nodes.add(nid)
            seg_id = f"seg:{hashlib.sha1(nid.encode('utf-8')).hexdigest()[:16]}"
            b_list = [n_obj["binding"]] if n_obj.get("binding") else []
            r_list = [n_obj["rel_path"]] if n_obj.get("rel_path") else []
            segments.append(Segment(
                segment_id=seg_id,
                node_ids=[nid],
                edge_ids=[],
                bindings=b_list,
                edge_kinds=[],
                rel_paths=r_list,
            ))

    # Sort segments deterministically by first node's ordinal
    segments.sort(key=lambda s: (node_by_id.get(s.node_ids[0], {}).get("ordinal", 0), s.segment_id))
    return segments
