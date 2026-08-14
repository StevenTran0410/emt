import json
import re
from typing import Any

_REASON_CODE_WHITELIST = {
    "NO_FILE_IN_SNAPSHOT",
    "UNRESOLVED_ASSET",
    "NO_OCCURRENCE",
    "EXTERNAL_TARGET",
    "PARSE_PARTIAL_ONLY",
    "FILE_UNREADABLE",
    "CITATION_INVALID",
    "CITATION_OUT_OF_WINDOW",
    "NO_CITATION",
    "CONTRADICTION_FLOOR",
    "BROKEN_NO_CONTRADICTION_ASPECT",
    "ASPECT_CONTRADICTION",
    "LLM_NO_RESPONSE",
    "OFFLINE_NO_LLM",
    "MALFORMED_PERSISTED_ARTIFACT",
}



def _build_unit_evidence_fields(
    uv: dict[str, Any], art_payload: dict[str, Any] | None
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, list[str]]:
    try:
        ev_data = json.loads(uv["evidence_json"]) if uv.get("evidence_json") else {}
    except Exception:
        # FIX 9: one corrupt evidence_json row must degrade only this unit, never 500 the endpoint.
        return [], None, ["MALFORMED_PERSISTED_ARTIFACT"]
    art_payload = art_payload or {}

    citations: list[dict[str, Any]] = []
    art_citations = art_payload.get("citation_resolutions") or []

    if art_citations:
        for rc in art_citations:
            if rc.get("valid"):
                citations.append({
                    "rel_path": rc["rel_path"],
                    "line_start": rc["line_start"],
                    "line_end": rc["line_end"],
                    "fetched_text": rc.get("fetched_text"),
                })
    else:
        for c in ev_data.get("citations", []):
            if c.get("valid", True):
                citations.append({
                    "rel_path": c["rel_path"],
                    "line_start": c["line_start"],
                    "line_end": c["line_end"],
                    "fetched_text": None,
                })

    model_output = art_payload.get("model_raw_output")
    aspects = (
        model_output.get("aspects")
        if (isinstance(model_output, dict) and "aspects" in model_output)
        else None
    )

    raw_codes = ev_data.get("reason_codes", []) if isinstance(ev_data, dict) else []
    filtered_codes = [code for code in raw_codes if code in _REASON_CODE_WHITELIST]

    return citations, aspects, filtered_codes


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

    # Fetch business_unit_verdicts & business_flows presence for P4-2
    async with db.execute(
        "SELECT id, unit_id, unit_kind, mapping_status, mapping_method, route_segment_json, verdict, guard_verdict, ai_bucket, reason, evidence_json FROM business_unit_verdicts WHERE cluster_id=? AND snapshot_id=?",
        (cluster_id, snapshot_id),
    ) as cur:
        uv_rows = [dict(r) for r in await cur.fetchall()]

    async with db.execute(
        "SELECT COUNT(*) as c FROM bd_business_flows WHERE cluster_id=?", (cluster_id,)
    ) as cur:
        row = await cur.fetchone()
        has_bf = (row["c"] if row else 0) > 0

    # TICKET P5-UI-FIX4: join bfi_run_artifacts (same pattern as get_flow_integrity_findings) so
    # graph-panel citations carry fetched_text — P5 verdicts' evidence_json alone has no fetched text.
    verdict_run_id: str | None = None
    for uv in uv_rows:
        if not uv.get("evidence_json"):
            continue
        try:
            ev = json.loads(uv["evidence_json"])
        except Exception:
            continue
        rid = ev.get("run_id") if isinstance(ev, dict) else None
        if rid:
            verdict_run_id = rid
            break

    artifacts_by_unit: dict[str, dict[str, Any]] = {}
    if verdict_run_id:
        async with db.execute(
            "SELECT unit_id, payload FROM bfi_run_artifacts WHERE snapshot_id=? AND run_id=?",
            (snapshot_id, verdict_run_id),
        ) as cur:
            for r in await cur.fetchall():
                try:
                    artifacts_by_unit[r["unit_id"]] = json.loads(r["payload"])
                except Exception:
                    pass  # one corrupt artifact payload must not break the whole query
    else:
        # Legacy verdict rows carry no run_id — fall back to the old newest-per-unit behavior.
        async with db.execute(
            "SELECT unit_id, payload FROM bfi_run_artifacts WHERE cluster_id=? AND snapshot_id=? ORDER BY created_at ASC",
            (cluster_id, snapshot_id),
        ) as cur:
            for r in await cur.fetchall():
                try:
                    artifacts_by_unit[r["unit_id"]] = json.loads(r["payload"])
                except Exception:
                    pass

    unit_annotations: dict[str, Any] = {}
    for uv in uv_rows:
        citations, aspects, _reason_codes = _build_unit_evidence_fields(uv, artifacts_by_unit.get(uv["unit_id"]))
        unit_annotations[uv["unit_id"]] = {
            "verdict": uv["verdict"],
            "mapping_status": uv["mapping_status"],
            "mapping_method": uv["mapping_method"],
            "guard_verdict": uv["guard_verdict"],
            "ai_bucket": uv["ai_bucket"],
            "reason": uv["reason"],
            "evidence": json.loads(uv["evidence_json"]) if uv.get("evidence_json") else None,
            "segment": json.loads(uv["route_segment_json"]) if uv.get("route_segment_json") else None,
            "citations": citations,
            "aspects": aspects,
        }

    return {
        "cluster_id": cluster_id,
        "snapshot_id": snapshot_id,
        "nodes": rf_nodes,
        "edges": rf_edges,
        "business_flows_present": has_bf,
        "unit_annotations": unit_annotations,
    }


async def get_flow_integrity_findings(
    db: Any, cluster_id: str, snapshot_id: str, provider_id: str | None = None
) -> dict[str, Any]:
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

    # 4. Phase 4 business unit rollup

    async with db.execute(
        "SELECT id, unit_id, unit_kind, mapping_status, mapping_method, route_segment_json, verdict, guard_verdict, ai_bucket, reason, evidence_json FROM business_unit_verdicts WHERE cluster_id=? AND snapshot_id=?",
        (cluster_id, snapshot_id),
    ) as cur:
        unit_verdict_rows = [dict(r) for r in await cur.fetchall()]

    # FIX 4: pin artifacts to the SAME run the verdicts came from (evidence_json.run_id) instead of
    # newest-per-unit — the append-only artifacts table can otherwise pair a verdict from one run
    # with citations from a different run. Verdict rows share one run_id after delete+replace, so
    # the first row that carries one names the run for the whole scope. One bulk query either way.
    verdict_run_id: str | None = None
    for uv in unit_verdict_rows:
        if not uv.get("evidence_json"):
            continue
        try:
            ev = json.loads(uv["evidence_json"])
        except Exception:
            continue
        rid = ev.get("run_id") if isinstance(ev, dict) else None
        if rid:
            verdict_run_id = rid
            break

    artifacts_by_unit: dict[str, dict[str, Any]] = {}
    if verdict_run_id:
        async with db.execute(
            "SELECT unit_id, payload FROM bfi_run_artifacts WHERE snapshot_id=? AND run_id=?",
            (snapshot_id, verdict_run_id),
        ) as cur:
            for r in await cur.fetchall():
                try:
                    artifacts_by_unit[r["unit_id"]] = json.loads(r["payload"])
                except Exception:
                    pass  # FIX 9: one corrupt artifact payload must not break the whole query
    else:
        # Legacy verdict rows carry no run_id — fall back to the old newest-per-unit behavior.
        async with db.execute(
            "SELECT unit_id, payload FROM bfi_run_artifacts WHERE cluster_id=? AND snapshot_id=? ORDER BY created_at ASC",
            (cluster_id, snapshot_id),
        ) as cur:
            for r in await cur.fetchall():
                try:
                    artifacts_by_unit[r["unit_id"]] = json.loads(r["payload"])
                except Exception:
                    pass

    async with db.execute(
        "SELECT id, doc_id, sub_ix, block_key, name, description, ordinal, origin FROM bd_business_flows WHERE cluster_id=? ORDER BY ordinal ASC",
        (cluster_id,),
    ) as cur:
        b_flows = [dict(r) for r in await cur.fetchall()]

    b_steps_by_flow: dict[str, list[dict[str, Any]]] = {}
    b_branches_by_flow: dict[str, list[dict[str, Any]]] = {}

    if b_flows:
        flow_ids = [f["id"] for f in b_flows]
        f_str = ",".join("?" for _ in flow_ids)
        async with db.execute(
            f"SELECT id, flow_id, name, functionality, ordinal, source_node_ids, doc_line_start, doc_line_end FROM bd_business_steps WHERE flow_id IN ({f_str}) ORDER BY ordinal ASC",
            flow_ids,
        ) as cur:
            for r in await cur.fetchall():
                b_steps_by_flow.setdefault(r["flow_id"], []).append(dict(r))

        async with db.execute(
            f"SELECT id, flow_id, source_step_id, target_step_id, branch_kind, guard_description, source_edge_ids FROM bd_business_branches WHERE flow_id IN ({f_str})",
            flow_ids,
        ) as cur:
            for r in await cur.fetchall():
                b_branches_by_flow.setdefault(r["flow_id"], []).append(dict(r))

    uv_map = {r["unit_id"]: r for r in unit_verdict_rows}

    # Per-flow narratives: on a Run (provider set) regenerate + persist; otherwise read the stored text
    # so an LLM-blocked machine shows the persisted narratives (fall back to deterministic if none stored).
    from ._narrative import generate_flow_narratives, load_flow_narratives
    if provider_id:
        flow_narratives = await generate_flow_narratives(db, cluster_id, snapshot_id, provider_id=provider_id)
    else:
        flow_narratives = await load_flow_narratives(db, cluster_id, snapshot_id)
        if not flow_narratives:
            flow_narratives = await generate_flow_narratives(db, cluster_id, snapshot_id, provider_id=None)

    SECTION_NAME_MAP = {1: "Screen Spec", 2: "Business Flow", 3: "Job Flow", 6: "Event Flows"}

    # Roll up per-flow statistics
    per_flow_rollup: list[dict[str, Any]] = []
    matched_units: list[dict[str, Any]] = []
    contradicted_units: list[dict[str, Any]] = []
    unknown_units: list[dict[str, Any]] = []
    mapped_segment_node_ids: set[str] = set()

    for flow in b_flows:
        fid = flow["id"]
        steps = b_steps_by_flow.get(fid, [])
        branches = b_branches_by_flow.get(fid, [])
        step_name_by_id = {s["id"]: s["name"] for s in steps}

        # FIX 5: expected unit set = steps + branches; a MATCH/PARTIAL count spans BOTH (a branch
        # can legitimately MATCH too), steps_matched below stays STEP-only for the existing display.
        steps_matched = 0
        steps_backed = 0  # TICKET P5-UI-FIX: steps with ANY backing (MATCH or PARTIAL), not MATCH-only
        units_matched = 0
        units_broken = 0
        units_partial = 0
        units_unknown = 0
        total_expected = len(steps) + len(branches)

        for step in steps:
            uv = uv_map.get(step["id"])
            if uv is None:
                units_unknown += 1  # FIX 5: expected unit with no verdict row is UNKNOWN, not dropped
                continue

            citations, aspects, reason_codes = _build_unit_evidence_fields(uv, artifacts_by_unit.get(step["id"]))
            malformed = "MALFORMED_PERSISTED_ARTIFACT" in reason_codes
            v = "UNKNOWN" if malformed else uv["verdict"]  # FIX 9: corrupt evidence degrades this unit only

            if v == "MATCH":
                steps_matched += 1
                steps_backed += 1
                units_matched += 1
            elif v == "BROKEN":
                units_broken += 1
            elif v == "PARTIAL":
                units_partial += 1
                steps_backed += 1
            elif v == "UNKNOWN":
                units_unknown += 1

            seg_data = None
            if uv.get("route_segment_json"):
                try:
                    seg = json.loads(uv["route_segment_json"])
                    mapped_segment_node_ids.update(seg.get("node_ids", []))
                    seg_data = {
                        "bindings": seg.get("bindings", []),
                        "rel_paths": seg.get("rel_paths", []),
                    }
                except Exception:
                    pass

            try:
                uv_evidence = json.loads(uv["evidence_json"]) if (not malformed and uv.get("evidence_json")) else None
            except Exception:
                uv_evidence = None

            unit_detail = {
                "flow_id": fid,
                "flow_name": flow["name"],
                "unit_id": step["id"],
                "unit_name": step["name"],
                "unit_kind": "step",
                "prose": step["functionality"],
                "verdict": v,
                "reason": uv["reason"] if not malformed else "Stored evidence for this unit is corrupted and could not be parsed.",
                "reason_codes": reason_codes,
                "citations": citations,
                "aspects": aspects,
                "evidence": uv_evidence,
                "segment": seg_data,
            }
            if v == "MATCH":
                matched_units.append(unit_detail)
            elif v == "BROKEN":
                contradicted_units.append(unit_detail)
            else:
                unknown_units.append(unit_detail)

        for branch in branches:
            uv = uv_map.get(branch["id"])
            if uv is None:
                units_unknown += 1  # FIX 5: expected unit with no verdict row is UNKNOWN, not dropped
                continue

            citations, aspects, reason_codes = _build_unit_evidence_fields(uv, artifacts_by_unit.get(branch["id"]))
            malformed = "MALFORMED_PERSISTED_ARTIFACT" in reason_codes
            v = "UNKNOWN" if malformed else uv["verdict"]  # FIX 9: corrupt evidence degrades this unit only

            if v == "MATCH":
                units_matched += 1
            elif v == "BROKEN":
                units_broken += 1
            elif v == "PARTIAL":
                units_partial += 1
            elif v == "UNKNOWN":
                units_unknown += 1

            seg_data = None
            if uv.get("route_segment_json"):
                try:
                    seg = json.loads(uv["route_segment_json"])
                    mapped_segment_node_ids.update(seg.get("node_ids", []))
                    seg_data = {
                        "bindings": seg.get("bindings", []),
                        "rel_paths": seg.get("rel_paths", []),
                    }
                except Exception:
                    pass

            try:
                uv_evidence = json.loads(uv["evidence_json"]) if (not malformed and uv.get("evidence_json")) else None
            except Exception:
                uv_evidence = None

            unit_detail = {
                "flow_id": fid,
                "flow_name": flow["name"],
                "unit_id": branch["id"],
                "unit_name": f"Branch ({branch['branch_kind']})",
                "unit_kind": "branch",
                "branch_kind": branch.get("branch_kind"),
                "source_step_id": branch.get("source_step_id"),
                "target_step_id": branch.get("target_step_id"),
                "target_step_name": step_name_by_id.get(branch.get("target_step_id")),
                "prose": branch["guard_description"],
                "verdict": v,
                "reason": uv["reason"] if not malformed else "Stored evidence for this unit is corrupted and could not be parsed.",
                "reason_codes": reason_codes,
                "citations": citations,
                "aspects": aspects,
                "evidence": uv_evidence,
                "segment": seg_data,
            }
            if v == "MATCH":
                matched_units.append(unit_detail)
            elif v == "BROKEN":
                contradicted_units.append(unit_detail)
            else:
                unknown_units.append(unit_detail)

        # FIX 5: honest status — BROKEN dominates; MATCHED only when EVERY expected unit MATCH;
        # PARTIAL when there's some coverage short of full match; else UNKNOWN (nothing resolved).
        if units_broken > 0:
            status = "BROKEN"
        elif total_expected > 0 and units_matched == total_expected:
            status = "MATCHED"
        elif units_matched + units_partial > 0:
            status = "PARTIAL"
        else:
            status = "UNKNOWN"

        # BD provenance line computation
        doc_lines = [st["doc_line_start"] for st in steps if st.get("doc_line_start")] + [
            st["doc_line_end"] for st in steps if st.get("doc_line_end")
        ]
        line_start = min(doc_lines) if doc_lines else None
        line_end = max(doc_lines) if doc_lines else None
        sub_ix = flow.get("sub_ix")
        section_name = SECTION_NAME_MAP.get(
            sub_ix, f"Section {sub_ix}" if sub_ix is not None else "Business Specification"
        )

        per_flow_rollup.append({
            "flow_id": fid,
            "flow_name": flow["name"],
            "steps_total": len(steps),
            "steps_matched": steps_matched,
            "steps_backed": steps_backed,  # TICKET P5-UI-FIX: MATCH or PARTIAL steps (any backing)
            "branches_total": len(branches),
            "units_matched": units_matched,  # TICKET P5-UI: steps+branches MATCH count (not steps-only)
            "total_expected": total_expected,  # TICKET P5-UI: steps_total + branches_total
            "units_broken": units_broken,
            "units_partial": units_partial,
            "units_unknown": units_unknown,
            "status": status,
            "sub_ix": sub_ix,
            "section_name": section_name,
            "block_key": flow.get("block_key"),
            "doc_line_start": line_start,
            "doc_line_end": line_end,
            "description": flow.get("description"),
            "narrative": flow_narratives.get(fid, ""),
        })

    # Unmapped route segments (big-picture omissions)
    from ._segments import build_route_segments
    all_segments = await build_route_segments(db, snapshot_id)
    code_only_segments: list[dict[str, Any]] = [
        s.to_dict() for s in all_segments
        if not any(nid in mapped_segment_node_ids for nid in s.node_ids)
    ]

    # Unit calibration metrics
    b_total = len(unit_verdict_rows)
    b_match = sum(1 for u in unit_verdict_rows if u["verdict"] == "MATCH")
    b_partial = sum(1 for u in unit_verdict_rows if u["verdict"] == "PARTIAL")
    b_broken = sum(1 for u in unit_verdict_rows if u["verdict"] == "BROKEN")
    b_unknown = sum(1 for u in unit_verdict_rows if u["verdict"] == "UNKNOWN")
    b_resolved = b_match + b_partial + b_broken
    b_match_pct = (b_match / b_resolved * 100.0) if b_resolved > 0 else 100.0
    b_pass = 80.0 <= b_match_pct < 100.0

    # Persisted executive summary (generated during a Run) so an LLM-blocked machine reads it back.
    async with db.execute(
        "SELECT content FROM business_flow_llm_output WHERE cluster_id=? AND snapshot_id=? AND kind='summary' LIMIT 1",
        (cluster_id, snapshot_id),
    ) as cur:
        _sum_row = await cur.fetchone()
    executive_summary = None
    if _sum_row:
        try:
            executive_summary = json.loads(_sum_row["content"])
        except Exception:
            executive_summary = None

    business_payload = {
        "calibration": {
            "match_percentage": round(b_match_pct, 2),
            "total_units": b_total,
            "resolved_units": b_resolved,
            "match_count": b_match,
            "partial_count": b_partial,
            "broken_count": b_broken,
            "unknown_count": b_unknown,
            "calibration_pass": b_pass,
            "fail_blind": b_match_pct == 100.0,
        },
        "per_flow": per_flow_rollup,
        "matched_units": matched_units,
        "contradicted_units": contradicted_units,
        "unknown_units": unknown_units,
        "code_only_segments": code_only_segments,
        "executive_summary": executive_summary,
    }

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
        # Only expose the BD-centric payload once the cluster actually has parsed business flows —
        # otherwise it's an all-zero dict that would (a) make the frontend's "not built yet" banner
        # never fire and (b) make the legacy executive-summary path unreachable for old clusters.
        "business": business_payload if b_flows else None,
    }
