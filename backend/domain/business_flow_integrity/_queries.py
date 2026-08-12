import json
import re
from typing import Any


async def get_e2e_flow_map(db: Any, cluster_id: str, snapshot_id: str) -> dict[str, Any]:
    """Assemble union overlay of BD flow and code routes for React-Flow rendering."""
    # 1. Fetch BD flow nodes & edges
    async with db.execute(
        "SELECT id, node_kind, local_id, binding, binding_type, label, ordinal, guard_text, doc_line_start, doc_line_end FROM bd_flow_nodes WHERE cluster_id=?",
        (cluster_id,),
    ) as cur:
        bd_nodes = [dict(r) for r in await cur.fetchall()]

    async with db.execute(
        "SELECT id, src_node_id, dst_node_id, edge_kind, label, guard_text, doc_line FROM bd_flow_edges WHERE cluster_id=?",
        (cluster_id,),
    ) as cur:
        bd_edges = [dict(r) for r in await cur.fetchall()]

    # 2. Fetch Code flow nodes & edges
    async with db.execute(
        "SELECT id, rel_path, node_kind, binding, binding_type, label, ordinal FROM code_flow_nodes WHERE snapshot_id=?",
        (snapshot_id,),
    ) as cur:
        code_nodes = [dict(r) for r in await cur.fetchall()]

    async with db.execute(
        "SELECT id, src_node_id, dst_node_id, edge_kind, label, guard_text, rel_path FROM code_flow_edges WHERE snapshot_id=?",
        (snapshot_id,),
    ) as cur:
        code_edges = [dict(r) for r in await cur.fetchall()]

    # 3. Fetch Alignment & Verdicts
    async with db.execute(
        "SELECT id, bd_node_id, code_node_id, match_method, tag FROM flow_alignment WHERE cluster_id=? AND snapshot_id=?",
        (cluster_id, snapshot_id),
    ) as cur:
        alignments = [dict(r) for r in await cur.fetchall()]

    async with db.execute(
        "SELECT id, bd_edge_id, code_subpath_json, verdict, guard_verdict, ai_bucket, reason, evidence_json FROM flow_verdicts WHERE cluster_id=? AND snapshot_id=?",
        (cluster_id, snapshot_id),
    ) as cur:
        verdicts = [dict(r) for r in await cur.fetchall()]

    align_by_bd_node = {a["bd_node_id"]: a for a in alignments if a.get("bd_node_id")}
    tag_by_code_node = {a["code_node_id"]: a["tag"] for a in alignments if a.get("code_node_id")}
    code_to_bd_map = {a["code_node_id"]: a["bd_node_id"] for a in alignments if a.get("code_node_id") and a.get("bd_node_id")}
    code_node_map = {cn["id"]: cn for cn in code_nodes}
    verdict_by_bd_edge = {v["bd_edge_id"]: v for v in verdicts if v.get("bd_edge_id")}

    rf_nodes: list[dict[str, Any]] = []
    seen_node_ids: set[str] = set()

    for bd in bd_nodes:
        nid = bd["id"]
        seen_node_ids.add(nid)
        align = align_by_bd_node.get(nid, {})
        tag = align.get("tag", "UNKNOWN")
        match_method = align.get("match_method")
        code_node_id = align.get("code_node_id")
        rel_path = code_node_map.get(code_node_id, {}).get("rel_path") if code_node_id else None

        rf_nodes.append({
            "id": nid,
            "type": "customFlow",
            "data": {
                "id": nid,
                "label": bd["label"] or bd["binding"] or bd["local_id"] or "Step",
                "node_kind": bd["node_kind"],
                "binding": bd["binding"],
                "binding_type": bd["binding_type"],
                "tag": tag,
                "match_method": match_method,
                "rel_path": rel_path,
                "ordinal": bd["ordinal"] or 1,
                "doc_line_start": bd.get("doc_line_start"),
                "doc_line_end": bd.get("doc_line_end"),
                "guard_text": bd.get("guard_text"),
            },
        })

    # Emit unaligned Code nodes (CODE_ONLY) — skip duplicate if already aligned to a BD node (FIX 1a)
    for cn in code_nodes:
        cid = cn["id"]
        if cid in code_to_bd_map:
            continue
        if cid in seen_node_ids:
            continue
        seen_node_ids.add(cid)
        tag = tag_by_code_node.get(cid, "CODE_ONLY")
        rf_nodes.append({
            "id": cid,
            "type": "customFlow",
            "data": {
                "id": cid,
                "label": cn["label"] or cn["binding"] or cn["rel_path"],
                "node_kind": cn["node_kind"],
                "binding": cn["binding"],
                "binding_type": cn["binding_type"],
                "tag": tag,
                "rel_path": cn["rel_path"],
                "ordinal": cn["ordinal"] or 1,
            },
        })

    rf_edges: list[dict[str, Any]] = []
    seen_edge_keys: set[tuple[str, str]] = set()

    for be in bd_edges:
        v_rec = verdict_by_bd_edge.get(be["id"])
        verdict = v_rec["verdict"] if v_rec else "UNKNOWN"
        reason = v_rec["reason"] if v_rec else ""
        ai_bucket = v_rec["ai_bucket"] if v_rec else None
        guard_verdict = v_rec["guard_verdict"] if v_rec else None

        evidence = None
        if v_rec and v_rec.get("evidence_json"):
            try:
                evidence = json.loads(v_rec["evidence_json"])
            except Exception:
                evidence = None

        code_subpath = None
        if v_rec and v_rec.get("code_subpath_json"):
            try:
                code_subpath = json.loads(v_rec["code_subpath_json"])
            except Exception:
                code_subpath = None

        rf_edges.append({
            "id": be["id"],
            "source": be["src_node_id"],
            "target": be["dst_node_id"],
            "label": be.get("guard_text") or be.get("edge_kind") or "",
            "data": {
                "verdict": verdict,
                "guard_verdict": guard_verdict,
                "ai_bucket": ai_bucket,
                "reason": reason,
                "edge_kind": be["edge_kind"],
                "doc_line": be.get("doc_line"),
                "is_code_edge": False,
                "evidence": evidence,
                "code_subpath": code_subpath,
            },
        })
        seen_edge_keys.add((be["src_node_id"], be["dst_node_id"]))

    # Emit code route edges (FIX 1)
    for ce in code_edges:
        src_id = code_to_bd_map.get(ce["src_node_id"], ce["src_node_id"])
        dst_id = code_to_bd_map.get(ce["dst_node_id"], ce["dst_node_id"])

        if src_id == dst_id:
            continue
        if (src_id, dst_id) in seen_edge_keys:
            continue
        seen_edge_keys.add((src_id, dst_id))

        edge_id = f"rf_code_edge:{ce['id']}"
        rf_edges.append({
            "id": edge_id,
            "source": src_id,
            "target": dst_id,
            "label": ce.get("guard_text") or ce.get("edge_kind") or "",
            "data": {
                "verdict": "CODE_ROUTE",
                "guard_verdict": "CLASS_MATCH",
                "ai_bucket": None,
                "reason": f"Code route backbone edge ({ce.get('edge_kind', 'flow')})",
                "edge_kind": ce.get("edge_kind"),
                "is_code_edge": True,
            },
        })

    return {
        "cluster_id": cluster_id,
        "snapshot_id": snapshot_id,
        "nodes": rf_nodes,
        "edges": rf_edges,
    }


async def get_flow_integrity_findings(db: Any, cluster_id: str, snapshot_id: str) -> dict[str, Any]:
    """Assemble findings payload: Broken/Unknown list, Code-Only list, Recovery Gaps list, Calibration Banner."""
    # 1. Fetch verdicts
    async with db.execute(
        "SELECT id, bd_edge_id, code_subpath_json, verdict, guard_verdict, ai_bucket, reason, evidence_json FROM flow_verdicts WHERE cluster_id=? AND snapshot_id=?",
        (cluster_id, snapshot_id),
    ) as cur:
        verdicts = [dict(r) for r in await cur.fetchall()]

    # 2. Fetch alignment
    async with db.execute(
        "SELECT id, bd_node_id, code_node_id, match_method, tag FROM flow_alignment WHERE cluster_id=? AND snapshot_id=?",
        (cluster_id, snapshot_id),
    ) as cur:
        align_rows = [dict(r) for r in await cur.fetchall()]

    # 3. Fetch BD flow edges & nodes for details lookup
    async with db.execute(
        "SELECT id, src_node_id, dst_node_id, edge_kind, label, guard_text, doc_line FROM bd_flow_edges WHERE cluster_id=?",
        (cluster_id,),
    ) as cur:
        bd_edges = {r["id"]: dict(r) for r in await cur.fetchall()}

    async with db.execute(
        "SELECT id, rel_path, binding, node_kind FROM code_flow_nodes WHERE snapshot_id=?",
        (snapshot_id,),
    ) as cur:
        code_nodes = {r["id"]: dict(r) for r in await cur.fetchall()}

    async with db.execute(
        "SELECT id, node_kind, binding, binding_type, label FROM bd_flow_nodes WHERE cluster_id=?",
        (cluster_id,),
    ) as cur:
        bd_nodes_map = {r["id"]: dict(r) for r in await cur.fetchall()}

    align_by_bd_node = {a["bd_node_id"]: a for a in align_rows if a.get("bd_node_id")}

    # Compute calibration metrics (FIX 2: strictly 80.0 <= match% < 100.0)
    total_units = len(verdicts)
    match_cnt = sum(1 for v in verdicts if v["verdict"] == "MATCH")
    broken_cnt = sum(1 for v in verdicts if v["verdict"] == "BROKEN")
    code_only_cnt = sum(1 for v in verdicts if v["verdict"] == "CODE_ONLY")
    unknown_cnt = sum(1 for v in verdicts if v["verdict"] == "UNKNOWN")

    resolved_units = total_units - unknown_cnt - code_only_cnt
    match_percentage = (match_cnt / resolved_units * 100.0) if resolved_units > 0 else 100.0
    calibration_pass = 80.0 <= match_percentage < 100.0

    bucket_counts: dict[str, int] = {
        "stale_missing": sum(1 for v in verdicts if v.get("ai_bucket") == "stale_missing"),
        "fabricated": sum(1 for v in verdicts if v.get("ai_bucket") == "fabricated"),
        "silent_omission": sum(1 for v in verdicts if v.get("ai_bucket") == "silent_omission"),
        "over_generalized": sum(1 for v in verdicts if v.get("ai_bucket") == "over_generalized"),
    }

    # Findings lists (SPEC §7). Actionable = BROKEN/DOC_ONLY/GRAPH_GAP/RECOVERY_GAP.
    # UNKNOWN kept in a SEPARATE list the UI renders collapsed/hidden (not an error, but still shown
    # on demand): below-altitude prose steps or external boundaries, not evaluable at route altitude.
    broken_unknown_findings: list[dict[str, Any]] = []
    unknown_findings: list[dict[str, Any]] = []

    for v in verdicts:
        verdict = v["verdict"]
        if verdict in ("BROKEN", "DOC_ONLY", "GRAPH_GAP", "RECOVERY_GAP"):
            bd_edge = bd_edges.get(v["bd_edge_id"], {}) if v.get("bd_edge_id") else {}
            broken_unknown_findings.append({
                "id": v["id"],
                "bd_span": f"Line {bd_edge.get('doc_line', 1)}: {bd_edge.get('label') or bd_edge.get('guard_text') or 'Transition'}",
                "code_fact": v.get("code_subpath_json") or "No code route",
                "verdict": verdict,
                "ai_bucket": v.get("ai_bucket"),
                "rule_violated": "Transition flow / guard invariant broken" if verdict == "BROKEN" else "Document transition missing in code",
                "reason": v["reason"],
            })
        elif verdict == "UNKNOWN":
            bd_edge = bd_edges.get(v["bd_edge_id"], {}) if v.get("bd_edge_id") else {}
            src_node = bd_nodes_map.get(bd_edge.get("src_node_id"), {}) if bd_edge else {}
            dst_node = bd_nodes_map.get(bd_edge.get("dst_node_id"), {}) if bd_edge else {}

            bd_ref = (
                dst_node.get("binding")
                or dst_node.get("label")
                or src_node.get("binding")
                or src_node.get("label")
                or bd_edge.get("label")
                or "BD Step"
            )
            bd_ref = re.sub(r"<br\s*/?>", " ", bd_ref, flags=re.IGNORECASE).strip()

            doc_line = bd_edge.get("doc_line", 1)
            reason_str = (
                f"Referenced in BD (line {doc_line}) but no matching source file exists in the repository — "
                "below route altitude or external boundary; cannot be verified (UNKNOWN)."
            )

            src_align = align_by_bd_node.get(bd_edge.get("src_node_id"), {})
            dst_align = align_by_bd_node.get(bd_edge.get("dst_node_id"), {})

            src_is_ext = src_align.get("match_method") == "external_target"
            dst_is_ext = dst_align.get("match_method") == "external_target"

            if src_is_ext or dst_is_ext:
                broken_unknown_findings.append({
                    "id": v["id"],
                    "bd_span": f"Line {doc_line}: {bd_edge.get('label') or 'External Transition'}",
                    "bd_reference": bd_ref,
                    "code_fact": "External boundary / unresolved asset",
                    "verdict": "UNKNOWN",
                    "ai_bucket": None,
                    "rule_violated": "External target boundary prevents end-to-end verification",
                    "reason": reason_str,
                })
            else:
                unknown_findings.append({
                    "id": v["id"],
                    "bd_span": f"Line {doc_line}: {bd_edge.get('label') or bd_edge.get('guard_text') or 'Transition'}",
                    "bd_reference": bd_ref,
                    "code_fact": "Below route altitude / external boundary — not evaluable",
                    "verdict": "UNKNOWN",
                    "ai_bucket": None,
                    "rule_violated": "Not comparable at route altitude",
                    "reason": reason_str,
                })

    collapsed_unknown_count = len(unknown_findings)

    # Code-Only findings list
    code_only_findings: list[dict[str, Any]] = []
    seen_code_only_nodes: set[str] = set()

    for a in align_rows:
        if a["tag"] == "CODE_ONLY" and a.get("code_node_id"):
            cid = a["code_node_id"]
            if cid in seen_code_only_nodes:
                continue
            seen_code_only_nodes.add(cid)
            c_node = code_nodes.get(cid, {})
            code_only_findings.append({
                "id": f"code_only:{cid}",
                "rel_path": c_node.get("rel_path", ""),
                "node_kind": c_node.get("node_kind", "route"),
                "reason": "Reachable code route absent from BD documentation",
            })

    # Recovery gaps list (empty state for HSBMENU5)
    recovery_gaps: list[dict[str, Any]] = []

    return {
        "cluster_id": cluster_id,
        "snapshot_id": snapshot_id,
        "calibration": {
            "match_percentage": round(match_percentage, 2),
            "total_units": total_units,
            "resolved_units": resolved_units,
            "match_count": match_cnt,
            "broken_count": broken_cnt,
            "code_only_count": code_only_cnt,
            "unknown_count": unknown_cnt,
            "calibration_pass": calibration_pass,
            "ai_bucket_counts": bucket_counts,
            "banner_message": f"{resolved_units} resolved of {total_units} units; {collapsed_unknown_count} transitions below route altitude",
        },
        "broken_unknown_findings": broken_unknown_findings,
        "unknown_findings": unknown_findings,
        "code_only_findings": code_only_findings,
        "recovery_gaps": recovery_gaps,
        "collapsed_unknown_count": collapsed_unknown_count,
    }
