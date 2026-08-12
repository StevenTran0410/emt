"""Code flow extractor and seeder for Phase 3 Business Flow Integrity."""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from shared.utils import utc_now_iso

ALLOWED_FLOW_EDGE_TYPES = {"menu_option", "calls", "submits", "shows_panel", "stacks"}


@dataclass(frozen=True)
class SeedFile:
    rel_path: str
    node_kind: str
    subpath_edges: list[str]
    reachable: bool = True


@dataclass(frozen=True)
class CodeFlowResult:
    snapshot_id: str
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    seed_files: list[SeedFile]


def _determine_node_kind(rel_path: str) -> str:
    if rel_path.startswith("__external__/") or rel_path.startswith("__unresolved__/"):
        return "external"
    lower = rel_path.lower()
    if lower.endswith(".pfd"):
        return "menu"
    if lower.endswith(".clist"):
        return "clist"
    if lower.endswith(".cbl") or lower.endswith(".cob"):
        return "program"
    if lower.endswith(".jcl") or lower.endswith(".prc"):
        return "job"
    if lower.endswith(".ipf"):
        return "panel"
    return "unknown"


async def build_code_flow(db: Any, snapshot_id: str) -> CodeFlowResult:
    """Perform route-aware BFS from root menu files over structural graph edges to extract code flow."""
    # 1. Fetch all structural graph edges for snapshot_id
    async with db.execute(
        "SELECT src_path, dst_path, edge_type, is_external FROM structural_graph_edges WHERE snapshot_id=?",
        (snapshot_id,),
    ) as cur:
        edge_rows = await cur.fetchall()

    # Build adjacency list: src_path -> list of (dst_path, edge_type, is_external)
    adj: dict[str, list[tuple[str, str, int]]] = {}
    for r in edge_rows:
        src = r["src_path"]
        dst = r["dst_path"]
        e_type = r["edge_type"]
        is_ext = r["is_external"]
        adj.setdefault(src, []).append((dst, e_type, is_ext))

    # 2. Find root menu files (.pfd)
    async with db.execute(
        "SELECT rel_path FROM manifest_files WHERE snapshot_id=? AND (rel_path LIKE '%.pfd' OR category='menu')",
        (snapshot_id,),
    ) as cur:
        menu_rows = await cur.fetchall()

    root_files = [r["rel_path"] for r in menu_rows]
    if not root_files:
        root_files = [src for src in adj.keys() if src.lower().endswith(".pfd")]

    # 3. BFS traversal
    visited_subpaths: dict[str, list[str]] = {}
    node_ordinals: dict[str, int] = {}
    queue: list[tuple[str, list[str]]] = []

    ordinal_counter = 1
    for root in root_files:
        visited_subpaths[root] = []
        node_ordinals[root] = ordinal_counter
        ordinal_counter += 1
        queue.append((root, []))

    flow_edges: list[dict[str, Any]] = []
    seen_edge_keys: set[tuple[str, str, str]] = set()

    while queue:
        curr, subpath = queue.pop(0)
        if curr.startswith("__external__/") or curr.startswith("__unresolved__/"):
            continue

        outgoing = adj.get(curr, [])
        for dst, e_type, is_ext in outgoing:
            if e_type not in ALLOWED_FLOW_EDGE_TYPES and not is_ext:
                continue

            edge_key = (curr, dst, e_type)
            if edge_key not in seen_edge_keys:
                seen_edge_keys.add(edge_key)
                flow_edges.append({
                    "src_path": curr,
                    "dst_path": dst,
                    "edge_kind": e_type,
                    "is_external": is_ext,
                })

            if dst not in visited_subpaths:
                new_subpath = subpath + [e_type]
                visited_subpaths[dst] = new_subpath
                node_ordinals[dst] = ordinal_counter
                ordinal_counter += 1
                if not is_ext and not dst.startswith("__external__/"):
                    queue.append((dst, new_subpath))

    # 4. Build node & edge records
    now = utc_now_iso()
    nodes_to_insert: list[tuple] = []
    nodes_dict_list: list[dict[str, Any]] = []

    for rel_path, subpath in visited_subpaths.items():
        node_kind = _determine_node_kind(rel_path)
        node_id = f"cfnode:{snapshot_id}:{rel_path}"
        binding = rel_path.rsplit("/", 1)[-1]
        binding_type = node_kind.upper()
        label = binding
        ordinal = node_ordinals[rel_path]
        attrs_json = json.dumps({"subpath": subpath})

        nodes_dict_list.append({
            "id": node_id,
            "snapshot_id": snapshot_id,
            "node_kind": node_kind,
            "binding": binding,
            "binding_type": binding_type,
            "label": label,
            "ordinal": ordinal,
            "rel_path": rel_path,
            "subpath": subpath,
        })
        nodes_to_insert.append((
            node_id, snapshot_id, node_kind, binding, binding_type, label,
            ordinal, None, rel_path, 1, 1, "code_structure", attrs_json, now
        ))

    edges_to_insert: list[tuple] = []
    edges_dict_list: list[dict[str, Any]] = []

    for e in flow_edges:
        src = e["src_path"]
        dst = e["dst_path"]
        e_kind = e["edge_kind"]
        src_id = f"cfnode:{snapshot_id}:{src}"
        dst_id = f"cfnode:{snapshot_id}:{dst}"
        edge_id = f"cfedge:{snapshot_id}:{src}:{dst}:{e_kind}"

        edges_dict_list.append({
            "id": edge_id,
            "snapshot_id": snapshot_id,
            "src_node_id": src_id,
            "dst_node_id": dst_id,
            "edge_kind": e_kind,
            "src_path": src,
            "dst_path": dst,
        })
        edges_to_insert.append((
            edge_id, snapshot_id, src_id, dst_id, e_kind, e_kind, None,
            src, 1, None, now
        ))

    # 5. Clear old rows & persist to DB
    await db.execute("DELETE FROM code_flow_nodes WHERE snapshot_id=?", (snapshot_id,))
    await db.execute("DELETE FROM code_flow_edges WHERE snapshot_id=?", (snapshot_id,))
    await db.commit()

    if nodes_to_insert:
        await db.executemany(
            "INSERT INTO code_flow_nodes (id, snapshot_id, node_kind, binding, binding_type, label, ordinal, guard_text, rel_path, line_start, line_end, provenance_tier, attributes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            nodes_to_insert,
        )
    if edges_to_insert:
        await db.executemany(
            "INSERT INTO code_flow_edges (id, snapshot_id, src_node_id, dst_node_id, edge_kind, label, guard_text, rel_path, line, attributes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            edges_to_insert,
        )
    await db.commit()

    # 6. Build seed files list
    seed_files = [
        SeedFile(
            rel_path=n["rel_path"],
            node_kind=n["node_kind"],
            subpath_edges=n["subpath"],
            reachable=True,
        )
        for n in nodes_dict_list
        if n["node_kind"] != "external"
    ]

    return CodeFlowResult(
        snapshot_id=snapshot_id,
        nodes=nodes_dict_list,
        edges=edges_dict_list,
        seed_files=seed_files,
    )


async def seed_files(db: Any, snapshot_id: str) -> list[SeedFile]:
    """Retrieve seed files for snapshot_id from code_flow_nodes."""
    async with db.execute(
        "SELECT rel_path, node_kind, attributes FROM code_flow_nodes WHERE snapshot_id=? AND node_kind != 'external' ORDER BY ordinal ASC",
        (snapshot_id,),
    ) as cur:
        rows = await cur.fetchall()

    results: list[SeedFile] = []
    for r in rows:
        attrs = {}
        if r["attributes"]:
            try:
                attrs = json.loads(r["attributes"])
            except Exception:
                attrs = {}
        subpath = attrs.get("subpath") or []
        results.append(
            SeedFile(
                rel_path=r["rel_path"],
                node_kind=r["node_kind"],
                subpath_edges=subpath,
                reachable=True,
            )
        )
    return results
