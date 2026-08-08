"""Graph context loading (symbol refs, communities, structural edges, centrality) and
the in-memory symbol index cache used for definition-bonus scoring."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from infrastructure.db.database import get_db


@dataclass
class _GraphContext:
    file_symbol_refs: dict[str, set[str]] = field(default_factory=dict)
    file_community: dict[str, int] = field(default_factory=dict)
    community_members: dict[int, set[str]] = field(default_factory=dict)
    edge_tuples: list[tuple] = field(default_factory=list)
    central_files: set[str] = field(default_factory=set)


async def _load_graph_context(snapshot_id: str, min_confidence: float | None = None) -> _GraphContext:
    """Load graph context for symbol expansion.

    Args:
        snapshot_id: The snapshot to load.
        min_confidence: Optional numeric confidence threshold (0.0-1.0); when set,
                        filters symbol_graph_edges to confidence_score >= min_confidence.
                        Default (None) is unfiltered, preserving current full-recall behavior.
    """
    db = get_db()
    ctx = _GraphContext()

    query = "SELECT DISTINCT src_symbol, dst_symbol, confidence_score FROM symbol_graph_edges WHERE snapshot_id=?"
    params: list = [snapshot_id]
    if min_confidence is not None:
        query += " AND confidence_score >= ?"
        params.append(min_confidence)

    async with db.execute(query, tuple(params)) as cur:
        rows = await cur.fetchall()
    for row in rows:
        src_file = row["src_symbol"].split("::")[0] if "::" in (row["src_symbol"] or "") else row["src_symbol"]
        dst_file = row["dst_symbol"].split("::")[0] if "::" in (row["dst_symbol"] or "") else row["dst_symbol"]
        if src_file and dst_file and src_file != dst_file:
            ctx.file_symbol_refs.setdefault(src_file, set()).add(dst_file)

    async with db.execute(
        "SELECT node_path, community_id FROM graph_community_members WHERE snapshot_id=?",
        (snapshot_id,),
    ) as cur:
        rows = await cur.fetchall()
    for row in rows:
        cid = int(row["community_id"])
        ctx.file_community[row["node_path"]] = cid
        ctx.community_members.setdefault(cid, set()).add(row["node_path"])

    async with db.execute(
        "SELECT src_path, dst_path, edge_type, is_external FROM structural_graph_edges WHERE snapshot_id=?",
        (snapshot_id,),
    ) as cur:
        rows = await cur.fetchall()
    for row in rows:
        ctx.edge_tuples.append((row["src_path"], row["dst_path"], row["edge_type"] or "", int(row["is_external"] or 0)))

    async with db.execute(
        "SELECT top_central_files FROM structural_graph_summaries WHERE snapshot_id=?",
        (snapshot_id,),
    ) as cur:
        row = await cur.fetchone()
    if row and row["top_central_files"]:
        try:
            arr = json.loads(row["top_central_files"])
            for item in arr:
                rp = item.get("rel_path") if isinstance(item, dict) else None
                if isinstance(rp, str):
                    ctx.central_files.add(rp)
        except Exception:
            pass

    return ctx


# Symbol cache: snapshot_id -> dict[term_lower, list[(rel_path, start_line, end_line)]]
_symbol_cache: dict[str, dict[str, list[tuple[str, int, int]]]] = {}


async def clear_symbol_cache(snapshot_id: str) -> None:
    """Clear symbol index cache for a snapshot (called on force-rebuild)."""
    _symbol_cache.pop(snapshot_id, None)


async def load_symbol_index(
    snapshot_id: str,
) -> dict[str, list[tuple[str, int, int]]]:
    """Load symbol index for a snapshot, cached in-memory.

    Returns: dict[term_lower, list[(rel_path, start_line, end_line)]]
    """
    if snapshot_id in _symbol_cache:
        return _symbol_cache[snapshot_id]

    db = get_db()
    index: dict[str, list[tuple[str, int, int]]] = {}

    async with db.execute(
        "SELECT name, parent_name, rel_path, line_start, line_end FROM code_symbols WHERE snapshot_id=?",
        (snapshot_id,),
    ) as cur:
        rows = await cur.fetchall()

    for row in rows:
        name = row["name"] or ""
        parent = row["parent_name"] or ""
        rel_path = row["rel_path"] or ""
        line_start = int(row["line_start"])
        line_end = int(row["line_end"])
        entry = (rel_path, line_start, line_end)

        name_lower = name.lower()
        if name_lower:
            index.setdefault(name_lower, []).append(entry)

        if parent:
            q_key = f"{parent}.{name}".lower()
            index.setdefault(q_key, []).append(entry)

        fqn_key = (f"{rel_path}::{parent + '.' if parent else ''}{name}").lower()
        index.setdefault(fqn_key, []).append(entry)

    _symbol_cache[snapshot_id] = index
    return index
