"""Graph node scoring and neighbor expansion — native C++ backend with Python fallback."""
from __future__ import annotations

import importlib


def _load_native_graph():
    try:
        return importlib.import_module("domain.structural_graph._native_graph")
    except Exception:  # pragma: no cover - platform/build dependent
        return None


def _compute_scores_python(
    nodes: list[str], edge_inputs: list[tuple[str, str, str, int]]
) -> list[dict[str, int | str]]:
    indeg: dict[str, int] = {n: 0 for n in nodes}
    outdeg: dict[str, int] = {n: 0 for n in nodes}
    for src, dst, _edge_type, _is_external in edge_inputs:
        outdeg[src] = outdeg.get(src, 0) + 1
        indeg[dst] = indeg.get(dst, 0) + 1

    seen = set(indeg.keys()) | set(outdeg.keys())
    items: list[dict[str, int | str]] = []
    for rel_path in seen:
        indegree = indeg.get(rel_path, 0)
        outdegree = outdeg.get(rel_path, 0)
        items.append(
            {
                "rel_path": rel_path,
                "indegree": indegree,
                "outdegree": outdegree,
                "score": (indegree * 3) + outdegree,
            }
        )
    items.sort(
        key=lambda x: (-int(x["score"]), -int(x["indegree"]), str(x["rel_path"]))
    )
    return items


def _expand_neighbors_python(
    seed: str, edge_inputs: list[tuple[str, str, str, int]], hops: int, limit: int
) -> dict[str, list]:
    if hops < 1:
        hops = 1
    if hops > 4:
        hops = 4
    if limit < 10:
        limit = 10
    if limit > 2000:
        limit = 2000

    edges = [e for e in edge_inputs if int(e[3]) == 0]
    adjacency: dict[str, list[int]] = {}
    for i, e in enumerate(edges):
        src = str(e[0])
        dst = str(e[1])
        adjacency.setdefault(src, []).append(i)
        adjacency.setdefault(dst, []).append(i)

    visited: set[str] = {seed}
    kept_edge_indexes: set[int] = set()
    frontier: set[str] = {seed}

    for _ in range(hops):
        next_frontier: set[str] = set()
        for node in frontier:
            for edge_idx in adjacency.get(node, []):
                if len(kept_edge_indexes) < limit:
                    kept_edge_indexes.add(edge_idx)
                src, dst, _edge_type, _is_external = edges[edge_idx]
                nxt = str(dst) if str(src) == node else str(src)
                if nxt not in visited and len(visited) < limit:
                    visited.add(nxt)
                    next_frontier.add(nxt)
        if not next_frontier:
            break
        frontier = next_frontier

    nodes = sorted(visited)
    out_edges: list[tuple[str, str, str]] = []
    for idx in sorted(kept_edge_indexes):
        src, dst, edge_type, _is_external = edges[idx]
        out_edges.append((str(src), str(dst), str(edge_type)))
    return {"nodes": nodes, "edges": out_edges}
