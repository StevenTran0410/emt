"""Louvain community detection, singleton absorption, and community reads."""
from __future__ import annotations

import json
from collections import defaultdict

from infrastructure.db.database import get_db
from shared.logger import logger
from shared.utils import utc_now_iso

from ..types import CommunityInfo, GraphCommunitiesResponse, NodeCommunityResponse
from ._path_resolve import _build_py_suffix_index, _is_init_file, _resolve_relative_import


class _CommunityMixin:
    # ── Community detection ───────────────────────────────────────────

    async def detect_communities(
        self, snapshot_id: str, resolution: float = 0.4
    ) -> GraphCommunitiesResponse:
        """Run Louvain community detection and persist results.

        Uses C++ native compute_louvain if available, falls back to Python.
        Safe to call concurrently with reads (WAL mode).
        """
        db = get_db()

        # Load ALL edges with deterministic order
        async with db.execute(
            "SELECT src_path, dst_path, is_external FROM structural_graph_edges WHERE snapshot_id=? ORDER BY src_path ASC, dst_path ASC",
            (snapshot_id,),
        ) as cur:
            edge_rows = await cur.fetchall()

        # Load all manifest nodes with deterministic order
        async with db.execute(
            "SELECT rel_path FROM manifest_files WHERE snapshot_id=? AND category IN ('source','infra') ORDER BY rel_path ASC",
            (snapshot_id,),
        ) as cur:
            node_rows = await cur.fetchall()

        node_ids = sorted(list(dict.fromkeys(
            r["rel_path"] for r in node_rows if not _is_init_file(r["rel_path"])
        )))
        node_id_set = set(node_ids)

        py_suffix_index = _build_py_suffix_index(node_ids)

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
                if dst in node_id_set and (src, dst) not in edge_set:
                    edge_tuples.append((src, dst, 1.0))
                    edge_set.add((src, dst))
            elif src.endswith(".py") and "/" not in dst and not dst.startswith("."):
                py_guess = dst.replace(".", "/") + ".py"
                resolved = py_suffix_index.get(py_guess)
                if not resolved:
                    pkg_init = dst.replace(".", "/") + "/__init__.py"
                    resolved = py_suffix_index.get(pkg_init)
                if resolved and resolved != src and (src, resolved) not in edge_set:
                    edge_tuples.append((src, resolved, 1.0))
                    edge_set.add((src, resolved))
            elif dst.startswith("."):
                resolved = _resolve_relative_import(src, dst, node_id_set)
                if resolved and resolved != src and (src, resolved) not in edge_set:
                    edge_tuples.append((src, resolved, 1.0))
                    edge_set.add((src, resolved))

        edge_tuples = sorted(edge_tuples)

        # Run Louvain via Python fallback
        from .._louvain_fallback import compute_louvain_python
        raw = compute_louvain_python(edge_tuples, node_ids, resolution)

        # ── Community-level absorption (<3 nodes) ──────────────────────────────────
        _comm_members_temp: dict[int, list[str]] = defaultdict(list)
        for _node, _cid in raw.items():
            _comm_members_temp[_cid].append(_node)

        _node_nbrs: dict[str, list[str]] = defaultdict(list)
        for _s, _d, _w in edge_tuples:
            _node_nbrs[_s].append(_d)
            _node_nbrs[_d].append(_s)

        for _cid, _m_list in list(_comm_members_temp.items()):
            if len(_m_list) >= 3:
                continue
            # Tally boundary edges from all nodes in this small community to external communities
            _votes: dict[int, int] = defaultdict(int)
            for _node in _m_list:
                for _nbr in _node_nbrs[_node]:
                    _nbr_cid = raw.get(_nbr, -1)
                    if _nbr_cid >= 0 and _nbr_cid != _cid:
                        _votes[_nbr_cid] += 1
            if _votes:
                target_cid = sorted(_votes.keys(), key=lambda c: (-_votes[c], c))[0]
                for _node in _m_list:
                    raw[_node] = target_cid

        # Renumber community IDs deterministically based on sorted members
        final_comm_members: dict[int, list[str]] = defaultdict(list)
        for _node, _cid in raw.items():
            final_comm_members[_cid].append(_node)

        sorted_groups = sorted(
            final_comm_members.values(), key=lambda members: sorted(members)[0]
        )

        canonical_raw: dict[str, int] = {}
        for new_cid, members in enumerate(sorted_groups):
            for node in members:
                canonical_raw[node] = new_cid
        raw = canonical_raw

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

        # Real per-community modularity term (Newman): Q_c = L_c/m - (D_c/2m)^2,
        # where L_c = intra-community edge weight, D_c = total degree of members,
        # m = total edge weight. Summing Q_c over communities gives graph modularity.
        _degree: dict[str, float] = defaultdict(float)
        _intra_w: dict[int, float] = defaultdict(float)
        _total_w = 0.0
        for s, d, w in edge_tuples:
            _total_w += w
            _degree[s] += w
            _degree[d] += w
            cs, cd = raw.get(s, -1), raw.get(d, -1)
            if cs >= 0 and cs == cd:
                _intra_w[cs] += w
        _deg_sum: dict[int, float] = defaultdict(float)
        for node, cid in raw.items():
            _deg_sum[cid] += _degree.get(node, 0.0)
        _m = _total_w or 1.0

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
            mod_contrib = _intra_w[cid] / _m - (_deg_sum[cid] / (2.0 * _m)) ** 2

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
