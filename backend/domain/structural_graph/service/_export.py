"""Full graph JSON export — nodes, edges, communities, cycles — for debugging/persistence."""
from __future__ import annotations

from collections import defaultdict

from infrastructure.db.database import get_db
from shared.utils import utc_now_iso

from ._path_resolve import _is_init_file

SYSTEM_COPYBOOK_PREFIXES = ("CMQ", "DFH", "IGZ", "CEE", "DSN", "ELX")
EXTERNAL_RUNTIMES = {"CEE3ABD", "CBLTDLI", "DFHEI1", "CEETEST"}


def _classify_resolution_class(dst: str, res_m: str) -> str:
    """Classify resolution class for edge targets in graph export."""
    if res_m == "external_utility":
        return "predefined_utility"
    if res_m in {"symbolic_program", "symbolic_dsn", "jcl_back_reference"}:
        return "unresolved_symbolic"
    if res_m == "system_copybook" or dst.startswith("__external__/copybook/"):
        return "system_copybook"

    stem = dst.rsplit("/", 1)[-1].rsplit(".", 1)[0].upper()
    if stem.startswith(SYSTEM_COPYBOOK_PREFIXES):
        return "system_copybook"
    if stem in EXTERNAL_RUNTIMES or stem.startswith("MQ") or stem.startswith("DFH") or res_m == "external_runtime":
        return "external_runtime"

    if res_m in {
        "program_not_in_snapshot",
        "copybook_not_found",
        "proc_not_in_snapshot",
        "ambiguous_program_id",
        "ambiguous_copybook",
        "ambiguous_proc",
    }:
        if stem.startswith("CEE") or stem.startswith("CBL") or stem.startswith("DFH") or stem.startswith("MQ"):
            return "external_runtime"
        return "not_in_snapshot"

    if dst.startswith("__unresolved__/"):
        return "unresolved_symbolic" if ("&" in dst or "%26" in dst) else "not_in_snapshot"
    if dst.startswith("__external__/"):
        return "external_runtime"

    return "resolved"


class _ExportMixin:
    async def export_graph_json(self, snapshot_id: str) -> dict:
        """Export full graph structure as a single JSON-serialisable dict.

        Includes nodes, all edges (internal + external), community assignments,
        per-community member lists, and circular import cycles.
        Designed for copy-paste debugging: share the output to diagnose clustering.
        """
        db = get_db()

        async with db.execute(
            "SELECT src_path, dst_path, edge_type, is_external, confidence_score, resolution_method FROM structural_graph_edges WHERE snapshot_id=? ORDER BY src_path, dst_path",
            (snapshot_id,),
        ) as cur:
            edge_rows = await cur.fetchall()

        async with db.execute(
            "SELECT node_path, community_id FROM graph_community_members WHERE snapshot_id=? ORDER BY community_id, node_path",
            (snapshot_id,),
        ) as cur:
            member_rows = await cur.fetchall()

        async with db.execute(
            "SELECT rel_path FROM manifest_files WHERE snapshot_id=? AND category IN ('source','infra') ORDER BY rel_path",
            (snapshot_id,),
        ) as cur:
            node_rows = await cur.fetchall()

        async with db.execute(
            "SELECT rel_path FROM manifest_files WHERE snapshot_id=? AND category='test' ORDER BY rel_path",
            (snapshot_id,),
        ) as cur:
            test_rows = await cur.fetchall()

        cycles_resp = await self.cycles(snapshot_id)

        # Test files are tracked separately in the manifest (category='test') and
        # surfaced as `test_files` metadata in the export.  They are excluded from
        # the structural graph because their import edges (test→source) distort
        # community detection without adding architectural information.
        test_files = list(dict.fromkeys(r["rel_path"] for r in test_rows))

        # Deduplicate: pre-migration-20 DBs may have 2× rows per file/edge.
        # __init__.py files are excluded from the graph (namespace markers, no edges).
        nodes = list(dict.fromkeys(
            r["rel_path"] for r in node_rows if not _is_init_file(r["rel_path"])
        ))

        # Collect synthetic, external, and unresolved nodes from edge targets
        synthetic_nodes = sorted({
            r["dst_path"] for r in edge_rows
            if r["dst_path"].startswith("__synthetic__/") or r["dst_path"].startswith("__external__/") or r["dst_path"].startswith("__unresolved__/")
        })
        for s_node in synthetic_nodes:
            if s_node not in nodes:
                nodes.append(s_node)

        node_set = set(nodes)
        seen_edges: set[tuple[str, str, str, bool]] = set()
        edges = []
        for r in edge_rows:
            if _is_init_file(r["src_path"]) or _is_init_file(r["dst_path"]):
                continue
            # Drop edges involving nodes excluded from the graph (test files, etc.)
            if r["src_path"] not in node_set and not bool(r["is_external"]):
                continue
            edge_type = r["edge_type"] if "edge_type" in r and r["edge_type"] else "import"
            conf = float(r["confidence_score"]) if "confidence_score" in r and r["confidence_score"] is not None else 1.0
            res_m = r["resolution_method"] if "resolution_method" in r and r["resolution_method"] else "import_statement"
            res_class = _classify_resolution_class(r["dst_path"], res_m)

            key = (r["src_path"], r["dst_path"], edge_type, bool(r["is_external"]))
            if key not in seen_edges:
                seen_edges.add(key)
                edges.append({
                    "src": r["src_path"],
                    "dst": r["dst_path"],
                    "edge_type": edge_type,
                    "external": key[3],
                    "confidence_score": conf,
                    "resolution_method": res_m,
                    "resolution_class": res_class,
                })

        communities: dict[str, int] = {}
        community_groups: dict[str, list[str]] = {}
        for r in member_rows:
            path, cid = r["node_path"], str(r["community_id"])
            communities[path] = r["community_id"]
            community_groups.setdefault(cid, []).append(path)

        internal_count = sum(1 for e in edges if not e["external"])

        # Compute community-level adjacency: which communities are connected by import edges.
        # This lets tools and humans see "Community A uses Community B" at a glance,
        # without having to manually trace individual file edges.
        inter_comm_counts: dict[tuple[int, int], int] = defaultdict(int)
        for e in edges:
            if e["external"]:
                continue
            src_cid = communities.get(e["src"])
            dst_cid = communities.get(e["dst"])
            if src_cid is None or dst_cid is None or src_cid == dst_cid:
                continue
            # Directed: src_community imports dst_community
            inter_comm_counts[(src_cid, dst_cid)] += 1

        community_edges = [
            {"src_community": src, "dst_community": dst, "edge_count": cnt}
            for (src, dst), cnt in sorted(
                inter_comm_counts.items(), key=lambda kv: -kv[1]
            )
        ]

        singleton_community_count = sum(
            1 for members in community_groups.values() if len(members) == 1
        )

        return {
            "snapshot_id": snapshot_id,
            "exported_at": utc_now_iso(),
            "stats": {
                "node_count": len(nodes),
                "total_edge_count": len(edges),
                "internal_edge_count": internal_count,
                "community_count": len(community_groups),
                "singleton_community_count": singleton_community_count,
                "cycle_count": len(cycles_resp.cycles),
                "test_file_count": len(test_files),
            },
            "nodes": nodes,
            "edges": edges,
            "communities": communities,
            "community_groups": community_groups,
            "community_edges": community_edges,
            "cycles": cycles_resp.cycles,
            # Test files are tracked here for agent context (so agents know tests exist)
            # but are NOT part of the structural graph — they distort community detection.
            "test_files": test_files,
        }
