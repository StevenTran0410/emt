"""Louvain community detection, singleton absorption, and community reads."""
from __future__ import annotations

import json
from collections import defaultdict

from infrastructure.db.database import get_db
from shared.logger import logger
from shared.utils import utc_now_iso

from ..types import CommunityInfo, GraphCommunitiesResponse, NodeCommunityResponse
from ._path_resolve import _build_py_suffix_index, _is_init_file, _resolve_relative_import
from ._scoring import _load_native_graph


class _CommunityMixin:
    # ── Community detection ───────────────────────────────────────────

    async def detect_communities(
        self, snapshot_id: str, resolution: float = 1.0
    ) -> GraphCommunitiesResponse:
        """Run Louvain community detection and persist results.

        Uses C++ native compute_louvain if available, falls back to Python.
        Safe to call concurrently with reads (WAL mode).
        """
        db = get_db()

        # Load ALL edges — we re-resolve "external" Python absolute imports below,
        # which fixes existing graph data built before the suffix-index fix.
        async with db.execute(
            "SELECT src_path, dst_path, is_external FROM structural_graph_edges WHERE snapshot_id=?",
            (snapshot_id,),
        ) as cur:
            edge_rows = await cur.fetchall()

        # Load all manifest nodes (ensures isolated nodes are included)
        async with db.execute(
            "SELECT rel_path FROM manifest_files WHERE snapshot_id=? AND category IN ('source','infra')",
            (snapshot_id,),
        ) as cur:
            node_rows = await cur.fetchall()

        # dict.fromkeys preserves insertion order while deduplicating.
        # manifest_files may have duplicate rows (pre-migration-20 DBs); passing
        # duplicates to Louvain inflates iteration count and can skew convergence.
        # __init__.py files and test files are excluded:
        #   - __init__.py: namespace markers with no edges → singleton communities
        #   - test files: import everything they test → noisy cross-community edges
        node_ids = list(dict.fromkeys(
            r["rel_path"] for r in node_rows if not _is_init_file(r["rel_path"])
        ))
        node_id_set = set(node_ids)

        # Suffix index — resolve Python absolute imports stored as unresolved externals.
        # E.g. edge dst_path "domain.structural_graph.service" → "backend/domain/structural_graph/service.py"
        py_suffix_index = _build_py_suffix_index(node_ids)

        # Build de-duplicated edge list; attempt to re-resolve external Python imports.
        edge_set: set[tuple[str, str]] = set()
        edge_tuples: list[tuple[str, str, float]] = []

        for r in edge_rows:
            src, dst = r["src_path"], r["dst_path"]
            if src not in node_id_set or src == dst:
                continue
            if src.startswith("__synthetic__/") or src.startswith("__external__/") or src.startswith("__unresolved__/"):
                continue
            if dst.startswith("__synthetic__/") or dst.startswith("__external__/") or dst.startswith("__unresolved__/"):
                continue
            if not r["is_external"]:
                # Already-resolved internal edge
                if dst in node_id_set and (src, dst) not in edge_set:
                    edge_tuples.append((src, dst, 1.0))
                    edge_set.add((src, dst))
            elif src.endswith(".py") and "/" not in dst and not dst.startswith("."):
                # Unresolved Python absolute import stored pre-suffix-fix — re-resolve now.
                # Also handles package imports: "domain.pkg" → "backend/domain/pkg/__init__.py".
                py_guess = dst.replace(".", "/") + ".py"
                resolved = py_suffix_index.get(py_guess)
                if not resolved:
                    pkg_init = dst.replace(".", "/") + "/__init__.py"
                    resolved = py_suffix_index.get(pkg_init)
                if resolved and resolved != src and (src, resolved) not in edge_set:
                    edge_tuples.append((src, resolved, 1.0))
                    edge_set.add((src, resolved))
            elif dst.startswith("."):
                # Unresolved relative TS/JS import stored pre-normpath-fix — re-resolve now.
                # dst holds the raw import string (e.g. "../../store/local-repo.store").
                resolved = _resolve_relative_import(src, dst, node_id_set)
                if resolved and resolved != src and (src, resolved) not in edge_set:
                    edge_tuples.append((src, resolved, 1.0))
                    edge_set.add((src, resolved))

        # Run Louvain — C++ native first, Python fallback
        native_graph = _load_native_graph()
        if native_graph and hasattr(native_graph, "compute_louvain"):
            try:
                raw: dict[str, int] = native_graph.compute_louvain(
                    edge_tuples, node_ids, resolution, 42
                )
            except Exception as e:
                logger.debug("[structural_graph] native compute_louvain failed: %s", e)
                raw = None
        else:
            raw = None

        if raw is None:
            from .._louvain_fallback import compute_louvain_python
            raw = compute_louvain_python(edge_tuples, node_ids, resolution)

        # ── Singleton absorption ──────────────────────────────────────────────
        # Louvain often isolates weakly-connected nodes (e.g. __init__.py files,
        # config files) into singleton communities.  For each singleton, count
        # "votes" from its internal neighbours and reassign it to the majority
        # community.  We work from a snapshot of `raw` so absorbed nodes don't
        # influence each other.  Nodes with no internal neighbours are left as
        # true singletons and flagged later.
        _comm_size: dict[int, int] = defaultdict(int)
        for _cid in raw.values():
            _comm_size[_cid] += 1

        _node_nbrs: dict[str, list[str]] = defaultdict(list)
        for _s, _d, _w in edge_tuples:
            _node_nbrs[_s].append(_d)
            _node_nbrs[_d].append(_s)

        _raw_snap = dict(raw)
        for _node, _cid in _raw_snap.items():
            if _comm_size[_cid] != 1:
                continue
            _votes: dict[int, int] = defaultdict(int)
            for _nbr in _node_nbrs[_node]:
                _nbr_cid = _raw_snap.get(_nbr, -1)
                if _nbr_cid >= 0 and _nbr_cid != _cid:
                    _votes[_nbr_cid] += 1
            if _votes:
                raw[_node] = max(_votes, key=_votes.__getitem__)

        # Compute hub scores (intra-community in-degree) and inter-community edges.
        comm_adj: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        # inter_comm_neighbors[cid] = set of community IDs that cid shares an edge with.
        inter_comm_neighbors: dict[int, set[int]] = defaultdict(set)

        for s, d, w in edge_tuples:
            cs, cd = raw.get(s, -1), raw.get(d, -1)
            if cs < 0 or cd < 0:
                continue
            if cs == cd:
                # Intra-community: count in-degree for hub scoring
                comm_adj[cs][d] += w
            else:
                # Inter-community: record adjacency (undirected)
                inter_comm_neighbors[cs].add(cd)
                inter_comm_neighbors[cd].add(cs)

        # Aggregate: member_count, hub_paths, hub_score per node
        comm_members: dict[int, list[str]] = defaultdict(list)
        for node, cid in raw.items():
            comm_members[cid].append(node)

        now = utc_now_iso()

        # Replace old community rows for this snapshot
        await db.execute("DELETE FROM graph_community_members WHERE snapshot_id=?", (snapshot_id,))
        await db.execute("DELETE FROM graph_community_summaries WHERE snapshot_id=?", (snapshot_id,))

        communities: list[CommunityInfo] = []
        _member_rows: list[tuple] = []
        _summary_rows: list[tuple] = []

        for cid, members in sorted(comm_members.items()):
            hub_scores = {n: comm_adj[cid].get(n, 0.0) for n in members}
            top_hubs = sorted(members, key=lambda n: -hub_scores[n])[:3]
            neighbor_ids = sorted(inter_comm_neighbors.get(cid, set()))
            mod_contrib = float(len(members)) / max(len(node_ids), 1)

            for node in members:
                _member_rows.append((snapshot_id, node, cid, hub_scores[node], now))

            _summary_rows.append((
                snapshot_id, cid, len(members), json.dumps(top_hubs), mod_contrib,
                json.dumps(neighbor_ids), now,
            ))

            communities.append(CommunityInfo(
                community_id=cid,
                member_count=len(members),
                hub_paths=top_hubs,
                modularity_contribution=mod_contrib,
                neighbor_community_ids=neighbor_ids,
                is_singleton=len(members) == 1,
                llm_summary=None,
                generated_at=now,
            ))

        # Batch-insert members and summaries in two round-trips.
        await db.executemany(
            """
            INSERT OR REPLACE INTO graph_community_members
            (snapshot_id, node_path, community_id, hub_score, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            _member_rows,
        )
        await db.executemany(
            """
            INSERT OR REPLACE INTO graph_community_summaries
            (snapshot_id, community_id, member_count, hub_paths, modularity_contribution,
             neighbor_community_ids, llm_summary, generated_at)
            VALUES (?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            _summary_rows,
        )

        await db.commit()

        return GraphCommunitiesResponse(
            snapshot_id=snapshot_id,
            total_communities=len(communities),
            communities=communities,
            node_index=raw,
        )

    async def list_communities(self, snapshot_id: str) -> GraphCommunitiesResponse:
        """Return cached community data from DB (no recomputation)."""
        db = get_db()

        async with db.execute(
            """
            SELECT community_id, member_count, hub_paths, modularity_contribution,
                   neighbor_community_ids, llm_summary, generated_at
            FROM graph_community_summaries WHERE snapshot_id=?
            ORDER BY community_id ASC
            """,
            (snapshot_id,),
        ) as cur:
            rows = await cur.fetchall()

        async with db.execute(
            "SELECT node_path, community_id FROM graph_community_members WHERE snapshot_id=?",
            (snapshot_id,),
        ) as cur:
            member_rows = await cur.fetchall()

        node_index = {r["node_path"]: r["community_id"] for r in member_rows}

        communities = [
            CommunityInfo(
                community_id=r["community_id"],
                member_count=r["member_count"],
                hub_paths=json.loads(r["hub_paths"] or "[]"),
                modularity_contribution=float(r["modularity_contribution"]),
                neighbor_community_ids=json.loads(r["neighbor_community_ids"] or "[]"),
                is_singleton=r["member_count"] == 1,
                llm_summary=r["llm_summary"],
                generated_at=r["generated_at"],
            )
            for r in rows
        ]

        return GraphCommunitiesResponse(
            snapshot_id=snapshot_id,
            total_communities=len(communities),
            communities=communities,
            node_index=node_index,
        )

    async def community_for_node(
        self, snapshot_id: str, rel_path: str
    ) -> NodeCommunityResponse:
        """Return community ID and all members for a given node."""
        db = get_db()

        async with db.execute(
            "SELECT community_id FROM graph_community_members WHERE snapshot_id=? AND node_path=?",
            (snapshot_id, rel_path),
        ) as cur:
            row = await cur.fetchone()

        if row is None:
            raise ValueError(f"Node '{rel_path}' not found in community index for snapshot {snapshot_id}")

        cid = row["community_id"]

        async with db.execute(
            "SELECT node_path FROM graph_community_members WHERE snapshot_id=? AND community_id=? ORDER BY node_path",
            (snapshot_id, cid),
        ) as cur:
            member_rows = await cur.fetchall()

        return NodeCommunityResponse(
            snapshot_id=snapshot_id,
            node_path=rel_path,
            community_id=cid,
            members=[r["node_path"] for r in member_rows],
        )

    async def _on_community_detection_complete(self, snapshot_id: str) -> None:
        """Regenerate graph.json with community IDs after detection finishes."""
        try:
            from ..graph_json import build_graph_json_payload, write_graph_json

            graph_data = await self.export_graph_json(snapshot_id)
            payload = build_graph_json_payload(
                graph_data["nodes"], graph_data["edges"], graph_data.get("communities", [])
            )
            await write_graph_json(snapshot_id, self._data_dir, payload)
            logger.info("[structural_graph] graph.json updated with community IDs for snapshot %s", snapshot_id)
        except Exception as e:
            logger.warning(
                "[structural_graph] failed to update graph.json after community detection for snapshot %s: %s",
                snapshot_id,
                e,
            )
