"""Export mixin for DocGraphService."""

from __future__ import annotations

import json
from typing import Any

from infrastructure.db.database import get_db


class _ExportMixin:
    """Export functionality for DocGraphService."""

    async def export_json(self, cluster_id: str) -> dict[str, Any]:
        """Export full doc_graph cluster representation as JSON blob."""
        summary = await self.summary(cluster_id)
        # F10: size the fetch to the cluster's real totals instead of a fixed cap, so a
        # large cluster's export is never silently truncated.
        nodes_res = await self.nodes(cluster_id, limit=max(summary.node_count, 1))
        edges_res = await self.edges(cluster_id, limit=max(summary.edge_count, 1))
        mismatches_res = await self.mismatches(cluster_id)

        db = get_db()
        async with db.execute(
            "SELECT * FROM doc_graph_assertions WHERE cluster_id=? ORDER BY id",
            (cluster_id,),
        ) as cur:
            ast_rows = await cur.fetchall()

        assertions = [
            {
                "side": r["side"],
                "predicate": r["predicate"],
                "subject": r["subject"],
                "object": r["object"],
                "value": r["value"],
                "qualifiers": json.loads(r["qualifiers"]) if r["qualifiers"] else {},
                "status": r["status"],
                "doc_id": r["doc_id"],
                "doc_span": json.loads(r["doc_span"]) if r["doc_span"] else {},
                "source_span": json.loads(r["source_span"]) if r["source_span"] else {},
                "confidence": r["confidence"],
            }
            for r in ast_rows
        ]

        return {
            "summary": summary.model_dump(),
            "nodes": [n.model_dump() for n in nodes_res.nodes],
            "edges": [e.model_dump() for e in edges_res.edges],
            "assertions": assertions,
            "mismatches": [m.model_dump() for m in mismatches_res.mismatches],
        }
