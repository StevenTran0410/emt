"""Deterministic alignment of BD flow steps to code flow routes (Phase 3 Stage A)."""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Literal

from shared.utils import new_id, utc_now_iso
from ._resolve import resolve_asset

AlignmentTag = Literal["DOC_MATCHED", "DOC_CONTRADICTED", "CODE_ONLY", "BD_ONLY", "UNKNOWN"]


@dataclass(frozen=True)
class AlignmentRecord:
    id: str
    cluster_id: str
    snapshot_id: str
    bd_node_id: str | None
    code_node_id: str | None
    match_method: str
    tag: AlignmentTag
    ordinal_in_subpath: int = 1


@dataclass(frozen=True)
class AlignmentResult:
    cluster_id: str
    snapshot_id: str
    records: list[AlignmentRecord]
    matched_count: int
    contradicted_count: int
    code_only_count: int
    bd_only_count: int
    unknown_count: int


def _is_bd_claiming_unresolved(bd_node: dict[str, Any]) -> bool:
    """Check if BD node prose or binding metadata asserts the asset is missing/unresolved."""
    b_type = str(bd_node.get("binding_type") or "").upper()
    if "UNRESOLVED" in b_type or "MISSING" in b_type:
        return True
    
    label = str(bd_node.get("label") or "").lower()
    attrs_str = str(bd_node.get("attributes") or "").lower()
    combined = f"{label} {attrs_str}"
    
    keywords = ["unresolved", "missing", "not found", "absent", "no source", "unverifiable"]
    return any(kw in combined for kw in keywords)


async def align_bd_to_code(db: Any, cluster_id: str, snapshot_id: str) -> AlignmentResult:
    """Deterministically align BD flow nodes with code flow nodes, generating reflexion tags."""
    # 1. Fetch BD flow nodes for cluster
    async with db.execute(
        "SELECT id, binding, binding_type, label, ordinal, attributes FROM bd_flow_nodes WHERE cluster_id=? ORDER BY ordinal ASC",
        (cluster_id,),
    ) as cur:
        bd_nodes = [dict(r) for r in await cur.fetchall()]

    # 2. Fetch Code flow nodes for snapshot
    async with db.execute(
        "SELECT id, rel_path, binding, binding_type, node_kind, ordinal FROM code_flow_nodes WHERE snapshot_id=?",
        (snapshot_id,),
    ) as cur:
        code_nodes = [dict(r) for r in await cur.fetchall()]

    code_nodes_by_path: dict[str, dict[str, Any]] = {r["rel_path"]: r for r in code_nodes}

    # 3. Fetch manifest files for snapshot
    async with db.execute(
        "SELECT rel_path FROM manifest_files WHERE snapshot_id=?",
        (snapshot_id,),
    ) as cur:
        manifest_paths = [r["rel_path"] for r in await cur.fetchall()]

    records: list[AlignmentRecord] = []
    aligned_code_node_ids: set[str] = set()

    # 4. Align each BD node to Code nodes
    for bd in bd_nodes:
        bd_id = bd["id"]
        binding = bd.get("binding") or bd.get("label") or ""
        b_type = bd.get("binding_type")

        res = resolve_asset(binding, b_type, manifest_paths)
        matched_path = res.rel_path if res.status == "RESOLVED" else None

        if res.status == "AMBIGUOUS" and res.candidate_paths:
            # Disambiguate by selecting candidates present in reachable code_nodes
            route_candidates = [p for p in res.candidate_paths if p in code_nodes_by_path]
            if route_candidates:
                def _candidate_rank(p: str) -> int:
                    kind = code_nodes_by_path[p].get("node_kind", "").lower()
                    b_type_str = str(b_type or "").lower()
                    if b_type_str and kind in b_type_str:
                        return 0
                    if kind == "clist":
                        return 1
                    if kind in ("program", "cobol"):
                        return 2
                    if kind in ("job", "jcl"):
                        return 3
                    return 4

                route_candidates.sort(key=_candidate_rank)
                matched_path = route_candidates[0]

        if matched_path:
            code_node = code_nodes_by_path.get(matched_path)

            if code_node:
                aligned_code_node_ids.add(code_node["id"])
                
                # Check if BD asserts missing/unresolved prose
                if _is_bd_claiming_unresolved(bd):
                    tag: AlignmentTag = "DOC_CONTRADICTED"
                else:
                    tag = "DOC_MATCHED"

                match_method = "exact_path" if matched_path == binding else ("basename" if "/" not in binding else "stem_typed")
                rec_id = f"align:{cluster_id}:{bd_id}:{code_node['id']}"
                records.append(AlignmentRecord(
                    id=rec_id,
                    cluster_id=cluster_id,
                    snapshot_id=snapshot_id,
                    bd_node_id=bd_id,
                    code_node_id=code_node["id"],
                    match_method=match_method,
                    tag=tag,
                ))
            elif matched_path.startswith("__external__/"):
                rec_id = f"align:{cluster_id}:{bd_id}:external"
                records.append(AlignmentRecord(
                    id=rec_id,
                    cluster_id=cluster_id,
                    snapshot_id=snapshot_id,
                    bd_node_id=bd_id,
                    code_node_id=None,
                    match_method="external_target",
                    tag="UNKNOWN",
                ))
            else:
                tag = "DOC_CONTRADICTED" if _is_bd_claiming_unresolved(bd) else "BD_ONLY"
                rec_id = f"align:{cluster_id}:{bd_id}:unmatched"
                records.append(AlignmentRecord(
                    id=rec_id,
                    cluster_id=cluster_id,
                    snapshot_id=snapshot_id,
                    bd_node_id=bd_id,
                    code_node_id=None,
                    match_method="unresolved_token",
                    tag=tag,
                ))
        else:
            # Check if binding refers to an external target (e.g. HND2UP1J)
            is_external_hint = "external" in binding.lower() or "unresolved" in str(b_type).lower() or res.status == "UNRESOLVED_EXTERNAL"
            tag = "UNKNOWN" if is_external_hint else "BD_ONLY"
            rec_id = f"align:{cluster_id}:{bd_id}:unresolved"
            records.append(AlignmentRecord(
                id=rec_id,
                cluster_id=cluster_id,
                snapshot_id=snapshot_id,
                bd_node_id=bd_id,
                code_node_id=None,
                match_method="unresolved",
                tag=tag,
            ))

    # 5. Identify CODE_ONLY nodes (reachable code route nodes with no BD binding)
    for c_node in code_nodes:
        if c_node["id"] not in aligned_code_node_ids and c_node["node_kind"] != "external":
            rec_id = f"align:{cluster_id}:none:{c_node['id']}"
            records.append(AlignmentRecord(
                id=rec_id,
                cluster_id=cluster_id,
                snapshot_id=snapshot_id,
                bd_node_id=None,
                code_node_id=c_node["id"],
                match_method="unmatched_code",
                tag="CODE_ONLY",
            ))

    # 6. Save records to flow_alignment
    await db.execute("DELETE FROM flow_alignment WHERE cluster_id=? AND snapshot_id=?", (cluster_id, snapshot_id))
    await db.commit()

    now = utc_now_iso()
    insert_tuples = [
        (
            r.id, r.cluster_id, r.snapshot_id, r.bd_node_id, r.code_node_id,
            r.match_method, r.tag, r.ordinal_in_subpath, now
        )
        for r in records
    ]
    if insert_tuples:
        await db.executemany(
            "INSERT INTO flow_alignment (id, cluster_id, snapshot_id, bd_node_id, code_node_id, match_method, tag, ordinal_in_subpath, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            insert_tuples,
        )
        await db.commit()

    matched_cnt = sum(1 for r in records if r.tag == "DOC_MATCHED")
    contradicted_cnt = sum(1 for r in records if r.tag == "DOC_CONTRADICTED")
    code_only_cnt = sum(1 for r in records if r.tag == "CODE_ONLY")
    bd_only_cnt = sum(1 for r in records if r.tag == "BD_ONLY")
    unknown_cnt = sum(1 for r in records if r.tag == "UNKNOWN")

    return AlignmentResult(
        cluster_id=cluster_id,
        snapshot_id=snapshot_id,
        records=records,
        matched_count=matched_cnt,
        contradicted_count=contradicted_cnt,
        code_only_count=code_only_cnt,
        bd_only_count=bd_only_cnt,
        unknown_count=unknown_cnt,
    )
