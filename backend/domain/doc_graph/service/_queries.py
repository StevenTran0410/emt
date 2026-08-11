"""Query mixin for DocGraphService."""

from __future__ import annotations

import json
from typing import Any

from infrastructure.db.database import get_db

from ..types import (
    DocGraphClusterSummary,
    DocGraphEdge,
    DocGraphEdgesResponse,
    DocGraphMismatch,
    DocGraphMismatchesResponse,
    DocGraphNode,
    DocGraphNodesResponse,
    DocGraphSummary,
)


class _QueryMixin:
    """Read queries for DocGraphService."""

    async def _ensure_cluster_exists(self, cluster_id: str) -> None:
        """F10: nodes()/edges()/mismatches() must 404 on an unknown cluster like summary()
        does, rather than silently returning an empty 200."""
        db = get_db()
        async with db.execute(
            "SELECT 1 FROM doc_graph_clusters WHERE id=?",
            (cluster_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            raise ValueError(f"Cluster not found: {cluster_id}")

    async def summary(self, cluster_id: str) -> DocGraphSummary:
        """Return cluster summary metrics."""
        db = get_db()
        async with db.execute(
            "SELECT * FROM doc_graph_clusters WHERE id=?",
            (cluster_id,),
        ) as cur:
            cluster_row = await cur.fetchone()

        if not cluster_row:
            raise ValueError(f"Cluster not found: {cluster_id}")

        async with db.execute(
            "SELECT COUNT(*) as cnt FROM doc_graph_documents WHERE cluster_id=?",
            (cluster_id,),
        ) as cur:
            doc_cnt = (await cur.fetchone())["cnt"]

        async with db.execute(
            "SELECT COUNT(*) as cnt FROM doc_graph_assertions WHERE cluster_id=?",
            (cluster_id,),
        ) as cur:
            ast_cnt = (await cur.fetchone())["cnt"]

        async with db.execute(
            "SELECT COUNT(*) as cnt FROM doc_graph_nodes WHERE cluster_id=?",
            (cluster_id,),
        ) as cur:
            node_cnt = (await cur.fetchone())["cnt"]

        async with db.execute(
            "SELECT COUNT(*) as cnt FROM doc_graph_edges WHERE cluster_id=?",
            (cluster_id,),
        ) as cur:
            edge_cnt = (await cur.fetchone())["cnt"]

        async with db.execute(
            "SELECT severity, COUNT(*) as cnt FROM doc_graph_mismatches "
            "WHERE cluster_id=? GROUP BY severity",
            (cluster_id,),
        ) as cur:
            sev_rows = await cur.fetchall()

        mismatches_by_sev = {"error": 0, "warning": 0, "info": 0}
        total_mismatches = 0
        for r in sev_rows:
            sev = r["severity"]
            cnt = r["cnt"]
            mismatches_by_sev[sev] = cnt
            total_mismatches += cnt

        return DocGraphSummary(
            cluster_id=cluster_id,
            cluster_name=cluster_row["cluster_name"],
            source_dir=cluster_row["source_dir"],
            bd_path=cluster_row["bd_path"],
            document_count=doc_cnt,
            assertion_count=ast_cnt,
            node_count=node_cnt,
            edge_count=edge_cnt,
            mismatch_count=total_mismatches,
            mismatches_by_severity=mismatches_by_sev,
            generated_at=cluster_row["generated_at"] or "",
        )

    async def nodes(
        self,
        cluster_id: str,
        limit: int = 500,
        offset: int = 0,
        node_type: str | None = None,
    ) -> DocGraphNodesResponse:
        """List paginated nodes for a cluster."""
        await self._ensure_cluster_exists(cluster_id)
        db = get_db()
        params: list[Any] = [cluster_id]
        where_clause = "WHERE cluster_id=?"

        if node_type:
            where_clause += " AND node_type=?"
            params.append(node_type)

        async with db.execute(
            f"SELECT COUNT(*) as cnt FROM doc_graph_nodes {where_clause}", params
        ) as cur:
            total = (await cur.fetchone())["cnt"]

        query = (
            f"SELECT * FROM doc_graph_nodes {where_clause} ORDER BY node_type, id LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])

        async with db.execute(query, params) as cur:
            rows = await cur.fetchall()

        nodes: list[DocGraphNode] = []
        for r in rows:
            nodes.append(
                DocGraphNode(
                    id=r["id"],
                    cluster_id=r["cluster_id"],
                    node_type=r["node_type"],
                    display_name=r["display_name"],
                    attributes=json.loads(r["attributes"]) if r["attributes"] else {},
                    provenance=json.loads(r["provenance"]) if r["provenance"] else [],
                    created_at=r["created_at"],
                )
            )

        return DocGraphNodesResponse(
            cluster_id=cluster_id,
            total=total,
            limit=limit,
            offset=offset,
            nodes=nodes,
        )

    async def edges(
        self,
        cluster_id: str,
        limit: int = 2000,
        offset: int = 0,
        edge_type: str | None = None,
    ) -> DocGraphEdgesResponse:
        """List paginated edges for a cluster."""
        await self._ensure_cluster_exists(cluster_id)
        db = get_db()
        params: list[Any] = [cluster_id]
        where_clause = "WHERE cluster_id=?"

        if edge_type:
            where_clause += " AND edge_type=?"
            params.append(edge_type)

        async with db.execute(
            f"SELECT COUNT(*) as cnt FROM doc_graph_edges {where_clause}", params
        ) as cur:
            total = (await cur.fetchone())["cnt"]

        query = (
            f"SELECT * FROM doc_graph_edges {where_clause} ORDER BY edge_type, id LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])

        async with db.execute(query, params) as cur:
            rows = await cur.fetchall()

        edges: list[DocGraphEdge] = []
        for r in rows:
            edges.append(
                DocGraphEdge(
                    id=r["id"],
                    cluster_id=r["cluster_id"],
                    src_node_id=r["src_node_id"],
                    dst_node_id=r["dst_node_id"],
                    edge_type=r["edge_type"],
                    edge_key=r["edge_key"],
                    attributes=json.loads(r["attributes"]) if r["attributes"] else {},
                    created_at=r["created_at"],
                )
            )

        return DocGraphEdgesResponse(
            cluster_id=cluster_id,
            total=total,
            limit=limit,
            offset=offset,
            edges=edges,
        )

    async def mismatches(
        self,
        cluster_id: str,
        severity: str | None = None,
    ) -> DocGraphMismatchesResponse:
        """List detected mismatches for a cluster."""
        await self._ensure_cluster_exists(cluster_id)
        db = get_db()
        params: list[Any] = [cluster_id]
        where_clause = "WHERE cluster_id=?"

        if severity:
            where_clause += " AND severity=?"
            params.append(severity)

        query = f"SELECT * FROM doc_graph_mismatches {where_clause} ORDER BY severity, id"
        async with db.execute(query, params) as cur:
            rows = await cur.fetchall()

        mismatches: list[DocGraphMismatch] = []
        for r in rows:
            mismatches.append(
                DocGraphMismatch(
                    id=r["id"],
                    cluster_id=r["cluster_id"],
                    fingerprint=r["fingerprint"],
                    mismatch_type=r["mismatch_type"],
                    severity=r["severity"],
                    derivation=r["derivation"] if "derivation" in r.keys() else "deterministic",
                    bd_location=json.loads(r["bd_location"]) if r["bd_location"] else None,
                    dd_location=json.loads(r["dd_location"]) if r["dd_location"] else None,
                    description=r["description"],
                    evidence=json.loads(r["evidence"]) if r["evidence"] else None,
                    confidence=r["confidence"],
                    created_at=r["created_at"],
                )
            )

        return DocGraphMismatchesResponse(
            cluster_id=cluster_id,
            total=len(mismatches),
            mismatches=mismatches,
        )

    async def list_clusters(self) -> list[DocGraphClusterSummary]:
        """Return list of all stored doc graph clusters ordered by generated_at DESC."""
        db = get_db()
        async with db.execute(
            """
            SELECT c.id as cluster_id, c.cluster_name, c.generated_at, c.snapshot_id,
                   (SELECT COUNT(*) FROM doc_graph_nodes n WHERE n.cluster_id = c.id)
                       as node_count,
                   (SELECT COUNT(*) FROM doc_graph_edges e WHERE e.cluster_id = c.id)
                       as edge_count,
                   (SELECT COUNT(*) FROM doc_graph_mismatches m WHERE m.cluster_id = c.id)
                       as mismatch_count
            FROM doc_graph_clusters c
            ORDER BY c.generated_at DESC
            """
        ) as cur:
            rows = await cur.fetchall()

        return [
            DocGraphClusterSummary(
                cluster_id=r["cluster_id"],
                cluster_name=r["cluster_name"],
                generated_at=r["generated_at"] or "",
                node_count=r["node_count"],
                edge_count=r["edge_count"],
                mismatch_count=r["mismatch_count"],
                snapshot_id=r["snapshot_id"],
            )
            for r in rows
        ]

    async def delete_cluster(self, cluster_id: str) -> None:
        """Permanently delete a cluster and cascade to all child tables."""
        db = get_db()
        await db.execute("DELETE FROM doc_graph_clusters WHERE id=?", (cluster_id,))
        await db.commit()
