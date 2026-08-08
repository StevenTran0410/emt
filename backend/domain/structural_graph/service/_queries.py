"""Read-only graph queries: summary, edges, neighbors, cycles, symbol drill-down."""
from __future__ import annotations

import json
from pathlib import Path

from infrastructure.db.database import get_db
from shared.errors import NotFoundError
from shared.logger import logger
from shared.toolchain import detect_cpp_toolchain

from ..types import (
    CyclesResponse,
    FileSymbolEdgesResponse,
    GraphEdge,
    GraphEdgesResponse,
    GraphNeighborsResponse,
    GraphNodeScore,
    StructuralGraphSummary,
    SymbolEdgeInfo,
)
from ._scoring import _expand_neighbors_python, _load_native_graph


class _QueryMixin:
    async def summary(self, snapshot_id: str) -> StructuralGraphSummary:
        async with get_db().execute(
            "SELECT * FROM structural_graph_summaries WHERE snapshot_id=?",
            (snapshot_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise NotFoundError("StructuralGraphSummary", snapshot_id)
        # Always return live toolchain status — DB value is stale if module was
        # built after the graph was last computed.
        return StructuralGraphSummary(
            snapshot_id=row["snapshot_id"],
            total_nodes=row["total_nodes"],
            total_edges=row["total_edges"],
            external_edges=row["external_edges"],
            entrypoints=json.loads(row["entrypoints"] or "[]"),
            top_central_files=[GraphNodeScore(**x) for x in json.loads(row["top_central_files"] or "[]")],
            generated_at=row["generated_at"],
            native_toolchain=detect_cpp_toolchain(),
        )

    async def edges(
        self, snapshot_id: str, limit: int = 2000, internal_only: bool = False
    ) -> GraphEdgesResponse:
        where = "WHERE snapshot_id=?" + (" AND is_external=0" if internal_only else "")
        async with get_db().execute(
            f"SELECT snapshot_id, src_path, dst_path, edge_type, is_external"
            f" FROM structural_graph_edges {where}"
            f" ORDER BY src_path ASC, dst_path ASC LIMIT ?",
            (snapshot_id, limit),
        ) as cur:
            rows = await cur.fetchall()
        return GraphEdgesResponse(
            snapshot_id=snapshot_id,
            edges=[
                GraphEdge(
                    snapshot_id=r["snapshot_id"],
                    src_path=r["src_path"],
                    dst_path=r["dst_path"],
                    edge_type=r["edge_type"],
                    is_external=bool(r["is_external"]),
                )
                for r in rows
            ],
        )

    async def neighbors(
        self, snapshot_id: str, seed_path: str, hops: int = 1, limit: int = 300
    ) -> GraphNeighborsResponse:
        seed = seed_path.strip()
        if not seed:
            raise ValueError("seed path is required")
        if hops < 1:
            hops = 1
        if hops > 4:
            hops = 4
        if limit < 10:
            limit = 10
        if limit > 2000:
            limit = 2000

        native_graph = _load_native_graph()

        async with get_db().execute(
            """
            SELECT snapshot_id, src_path, dst_path, edge_type, is_external
            FROM structural_graph_edges
            WHERE snapshot_id=? AND is_external=0
            """,
            (snapshot_id,),
        ) as cur:
            rows = await cur.fetchall()

        if not rows:
            return GraphNeighborsResponse(
                snapshot_id=snapshot_id,
                seed_path=seed,
                hops=hops,
                nodes=[seed],
                edges=[],
            )

        edge_inputs = [
            (r["src_path"], r["dst_path"], r["edge_type"], int(r["is_external"]))
            for r in rows
        ]
        if native_graph and hasattr(native_graph, "expand_neighbors"):
            expanded = native_graph.expand_neighbors(seed, edge_inputs, hops, limit)
        else:
            logger.info(
                "[structural_graph] native expand_neighbors unavailable; using Python fallback"
            )
            expanded = _expand_neighbors_python(seed, edge_inputs, hops, limit)
        nodes = [str(x) for x in expanded.get("nodes", [])]
        edges: list[GraphEdge] = []
        for t in expanded.get("edges", []):
            src_path = str(t[0])
            dst_path = str(t[1])
            edge_type = str(t[2]) if len(t) > 2 else "import"
            edges.append(
                GraphEdge(
                    snapshot_id=snapshot_id,
                    src_path=src_path,
                    dst_path=dst_path,
                    edge_type=edge_type,
                    is_external=False,
                )
            )

        return GraphNeighborsResponse(
            snapshot_id=snapshot_id,
            seed_path=seed,
            hops=hops,
            nodes=nodes,
            edges=edges,
        )

    async def cycles(self, snapshot_id: str) -> CyclesResponse:
        """Return circular import cycles (SCCs) via C++ native or Python fallback."""
        db = get_db()

        async with db.execute(
            "SELECT src_path, dst_path FROM structural_graph_edges WHERE snapshot_id=? AND is_external=0",
            (snapshot_id,),
        ) as cur:
            rows = await cur.fetchall()

        edge_tuples = [(r["src_path"], r["dst_path"]) for r in rows]

        native_graph = _load_native_graph()
        if native_graph and hasattr(native_graph, "compute_scc"):
            try:
                sccs = native_graph.compute_scc(edge_tuples)
            except Exception as e:
                logger.debug("[structural_graph] native compute_scc failed: %s", e)
                sccs = None
        else:
            sccs = None

        if sccs is None:
            from .._scc_fallback import compute_scc_python
            sccs = compute_scc_python(edge_tuples)

        return CyclesResponse(snapshot_id=snapshot_id, cycles=sccs)

    async def symbol_edges_for_file(self, snapshot_id: str, file_path: str) -> FileSymbolEdgesResponse:
        """Function-level drill-down: symbol_graph_edges for one file.

        Uses an exact length-bounded prefix match (not LIKE) on "file_path::",
        same collision-safe convention as copy_unchanged_symbol_edges,
        so "foo.py" never matches "foo2.py::...".
        """
        db = get_db()
        prefix = f"{file_path}::"
        plen = len(prefix)

        # Combined into one UNION ALL round-trip instead of two separate
        # queries. A direction discriminator distinguishes outgoing vs incoming rows
        # (UNION ALL, not UNION, so a self-referential edge -- a symbol in this file
        # calling another symbol in the same file -- correctly appears in BOTH halves,
        # exactly as the original two-query version did, not collapsed by a dedup pass).
        async with db.execute(
            """
            SELECT src_symbol, dst_symbol, edge_type, confidence_score, resolution_method,
                   'outgoing' AS direction
            FROM symbol_graph_edges
            WHERE snapshot_id=? AND substr(src_symbol, 1, ?) = ?
            UNION ALL
            SELECT src_symbol, dst_symbol, edge_type, confidence_score, resolution_method,
                   'incoming' AS direction
            FROM symbol_graph_edges
            WHERE snapshot_id=? AND substr(dst_symbol, 1, ?) = ?
            """,
            (snapshot_id, plen, prefix, snapshot_id, plen, prefix),
        ) as cur:
            combined_rows = await cur.fetchall()

        outgoing_rows = [r for r in combined_rows if r["direction"] == "outgoing"]
        incoming_rows = [r for r in combined_rows if r["direction"] == "incoming"]

        outgoing = [
            SymbolEdgeInfo(
                src_symbol=r["src_symbol"],
                dst_symbol=r["dst_symbol"],
                edge_type=r["edge_type"],
                confidence_score=r["confidence_score"],
                resolution_method=r["resolution_method"],
            )
            for r in outgoing_rows
        ]
        incoming = [
            SymbolEdgeInfo(
                src_symbol=r["src_symbol"],
                dst_symbol=r["dst_symbol"],
                edge_type=r["edge_type"],
                confidence_score=r["confidence_score"],
                resolution_method=r["resolution_method"],
            )
            for r in incoming_rows
        ]

        defined: set[str] = set()
        for r in outgoing_rows:
            defined.add(r["src_symbol"][plen:])
        for r in incoming_rows:
            defined.add(r["dst_symbol"][plen:])

        return FileSymbolEdgesResponse(
            snapshot_id=snapshot_id,
            file_path=file_path,
            defined_symbols=sorted(defined),
            outgoing=outgoing,
            incoming=incoming,
        )

    async def get_graph_json_path(self, snapshot_id: str) -> Path | None:
        """Return path to graph.json if it exists, else None."""
        from ..graph_json import graph_json_path

        p = graph_json_path(snapshot_id, self._data_dir)
        return p if p.exists() else None
