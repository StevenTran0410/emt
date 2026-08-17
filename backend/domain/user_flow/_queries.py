"""Report and query helpers for Phase U User Flow Alignment (TICKET U4).

Builds the structured user flow report containing:
- High-level user flows with flow_match rollup and step-level counts.
- Step-level cards with primary English labels (text_en / name_en) + Japanese originals.
- Verifiable evidence: kept BD mappings and code citations with verbatim fetched_text.
- BD_EXTRA list: BD units not covered by the user flow.
- Summary counts for flows and steps.
"""
from __future__ import annotations

import json
from typing import Any


async def get_user_flow_report(
    db: Any,
    doc_id: str,
    cluster_id: str,
    snapshot_id: str,
) -> dict[str, Any]:
    """Retrieve full User Flow Alignment report JSON for API/UI."""
    # 1. Fetch user doc info
    async with db.execute("SELECT id, source_name, file_hash, imported_at FROM user_flow_docs WHERE id = ?", (doc_id,)) as cur:
        doc_row = await cur.fetchone()
    doc_info = dict(doc_row) if doc_row and hasattr(doc_row, "keys") else (
        {"id": doc_row[0], "source_name": doc_row[1], "file_hash": doc_row[2], "imported_at": doc_row[3]} if doc_row else None
    )

    # 2. Fetch user flows
    async with db.execute(
        "SELECT id, doc_id, ordinal, name_ja, name_en, kind, sheet, scope_note FROM user_flows WHERE doc_id = ? ORDER BY ordinal",
        (doc_id,),
    ) as cur:
        flow_rows = await cur.fetchall()
    flows = [
        dict(r) if hasattr(r, "keys") else {
            "id": r[0], "doc_id": r[1], "ordinal": r[2], "name_ja": r[3], "name_en": r[4], "kind": r[5], "sheet": r[6], "scope_note": r[7]
        }
        for r in flow_rows
    ]

    # 3. Fetch user steps
    async with db.execute(
        "SELECT s.id, s.flow_id, s.ordinal, s.kind, s.section_id, s.text_ja, s.text_en, "
        "       s.trigger_ja, s.expected_ja, s.screen_name_ja, s.in_scope, s.scope_note, "
        "       s.sheet, s.row_start, s.row_end "
        "FROM user_steps s JOIN user_flows f ON s.flow_id = f.id "
        "WHERE f.doc_id = ? ORDER BY s.ordinal",
        (doc_id,),
    ) as cur:
        step_rows = await cur.fetchall()
    steps = [
        dict(r) if hasattr(r, "keys") else {
            "id": r[0], "flow_id": r[1], "ordinal": r[2], "kind": r[3], "section_id": r[4],
            "text_ja": r[5], "text_en": r[6], "trigger_ja": r[7], "expected_ja": r[8],
            "screen_name_ja": r[9], "in_scope": r[10], "scope_note": r[11],
            "sheet": r[12], "row_start": r[13], "row_end": r[14]
        }
        for r in step_rows
    ]

    # 4. Fetch verdicts for this (doc_id, cluster_id, snapshot_id)
    async with db.execute(
        "SELECT id, run_id, side, ref_id, ref_kind, verdict, divergence, reason, evidence_json "
        "FROM user_verdicts WHERE doc_id = ? AND cluster_id = ? AND snapshot_id = ?",
        (doc_id, cluster_id, snapshot_id),
    ) as cur:
        verdict_rows = await cur.fetchall()

    step_verdicts: dict[str, dict[str, Any]] = {}
    activity_verdicts: dict[str, dict[str, Any]] = {}
    flow_verdicts: dict[str, dict[str, Any]] = {}
    bd_verdicts: dict[str, dict[str, Any]] = {}

    for r in verdict_rows:
        d = dict(r) if hasattr(r, "keys") else {
            "id": r[0], "run_id": r[1], "side": r[2], "ref_id": r[3], "ref_kind": r[4],
            "verdict": r[5], "divergence": r[6], "reason": r[7], "evidence_json": r[8]
        }
        if d["side"] == "user" and d["ref_kind"] == "step":
            step_verdicts[d["ref_id"]] = d
        elif d["side"] == "user" and d["ref_kind"] == "activity":
            activity_verdicts[d["ref_id"]] = d
        elif d["side"] == "user" and d["ref_kind"] == "flow":
            flow_verdicts[d["ref_id"]] = d
        elif d["side"] == "bd":
            bd_verdicts[d["ref_id"]] = d

    # 4b. Fetch user_activities and user_activity_matches (Phase U2)
    async with db.execute(
        "SELECT id, flow_id, ordinal, name_en, name_ja, summary_en, member_step_ids_json, sheet_span_json, "
        "       step_count, action_count, error_rule_count, expectation_count, row_start, row_end, origin, created_at "
        "FROM user_activities WHERE flow_id IN (SELECT id FROM user_flows WHERE doc_id = ?) ORDER BY ordinal",
        (doc_id,),
    ) as cur:
        activity_rows = await cur.fetchall()

    activities_by_flow: dict[str, list[dict[str, Any]]] = {}
    for r in activity_rows:
        act = dict(r) if hasattr(r, "keys") else {
            "id": r[0], "flow_id": r[1], "ordinal": r[2], "name_en": r[3], "name_ja": r[4], "summary_en": r[5],
            "member_step_ids_json": r[6], "sheet_span_json": r[7], "step_count": r[8], "action_count": r[9],
            "error_rule_count": r[10], "expectation_count": r[11], "row_start": r[12], "row_end": r[13],
            "origin": r[14], "created_at": r[15],
        }
        activities_by_flow.setdefault(act["flow_id"], []).append(act)

    async with db.execute(
        "SELECT activity_id, bd_flow_id, match_status, confidence, reason FROM user_activity_matches "
        "WHERE activity_id IN (SELECT id FROM user_activities WHERE flow_id IN (SELECT id FROM user_flows WHERE doc_id = ?))",
        (doc_id,),
    ) as cur:
        act_match_rows = await cur.fetchall()

    matches_by_act: dict[str, list[dict[str, Any]]] = {}
    for r in act_match_rows:
        m = dict(r) if hasattr(r, "keys") else {
            "activity_id": r[0], "bd_flow_id": r[1], "match_status": r[2], "confidence": r[3], "reason": r[4]
        }
        matches_by_act.setdefault(m["activity_id"], []).append(m)

    # 5. Fetch step verdict audit artifacts for fetched_text extraction
    async with db.execute(
        "SELECT ref_id, payload FROM user_run_artifacts WHERE doc_id = ? AND ref_id LIKE 'verdict:%'",
        (doc_id,),
    ) as cur:
        verdict_art_rows = await cur.fetchall()

    artifacts_by_step: dict[str, dict[str, Any]] = {}
    for r in verdict_art_rows:
        ref_id = r[0] if isinstance(r, (tuple, list)) else r["ref_id"]
        payload_str = r[1] if isinstance(r, (tuple, list)) else r["payload"]
        step_id = ref_id.split(":", 1)[1] if ":" in ref_id else ref_id
        try:
            artifacts_by_step[step_id] = json.loads(payload_str)
        except Exception:
            artifacts_by_step[step_id] = {}

    # 6. Fetch BD units metadata
    async with db.execute("SELECT id, name, description FROM bd_business_flows WHERE cluster_id = ?", (cluster_id,)) as cur:
        b_flows = {r[0]: dict(r) if hasattr(r, "keys") else {"id": r[0], "name": r[1], "description": r[2]} for r in await cur.fetchall()}

    flow_ids = list(b_flows.keys())
    flow_ids_str = ",".join("?" for _ in flow_ids) if flow_ids else "''"

    bd_steps: dict[str, dict[str, Any]] = {}
    bd_branches: dict[str, dict[str, Any]] = {}
    if flow_ids:
        async with db.execute(
            f"SELECT id, flow_id, name, functionality FROM bd_business_steps WHERE flow_id IN ({flow_ids_str})",
            flow_ids,
        ) as cur:
            for r in await cur.fetchall():
                d = dict(r) if hasattr(r, "keys") else {"id": r[0], "flow_id": r[1], "name": r[2], "functionality": r[3]}
                bd_steps[d["id"]] = d

        async with db.execute(
            f"SELECT id, flow_id, branch_kind, guard_description FROM bd_business_branches WHERE flow_id IN ({flow_ids_str})",
            flow_ids,
        ) as cur:
            for r in await cur.fetchall():
                d = dict(r) if hasattr(r, "keys") else {"id": r[0], "flow_id": r[1], "branch_kind": r[2], "guard_description": r[3]}
                bd_branches[d["id"]] = d

    # Fetch BD unit verdicts
    async with db.execute(
        "SELECT unit_id, verdict FROM business_unit_verdicts WHERE cluster_id = ? AND snapshot_id = ?",
        (cluster_id, snapshot_id),
    ) as cur:
        bu_verdict_map = {r[0]: (r[1] if isinstance(r, (tuple, list)) else r["verdict"]) for r in await cur.fetchall()}

    # 7. Fetch user_bd_mappings for step mappings
    async with db.execute(
        "SELECT user_step_id, bd_kind, bd_id, relation, confidence, reason FROM user_bd_mappings "
        "WHERE user_step_id IN (SELECT s.id FROM user_steps s JOIN user_flows f ON s.flow_id = f.id WHERE f.doc_id = ?)",
        (doc_id,),
    ) as cur:
        mapping_rows = await cur.fetchall()
    mappings_by_step: dict[str, list[dict[str, Any]]] = {}
    for r in mapping_rows:
        d = dict(r) if hasattr(r, "keys") else {
            "user_step_id": r[0], "bd_kind": r[1], "bd_id": r[2], "relation": r[3], "confidence": r[4], "reason": r[5]
        }
        mappings_by_step.setdefault(d["user_step_id"], []).append(d)

    # 8. Assemble Steps into Flows
    steps_by_flow: dict[str, list[dict[str, Any]]] = {}
    step_counts = {
        "COVERED": 0,
        "BD_MISSING": 0,
        "CONTRADICTED": 0,
        "UNVERIFIABLE": 0,
        "OUT_OF_SCOPE": 0,
    }

    for s in steps:
        sid = s["id"]
        v_row = step_verdicts.get(sid, {})
        verdict = v_row.get("verdict", "OUT_OF_SCOPE" if s.get("in_scope", 1) == 0 else "UNVERIFIABLE")
        divergence = v_row.get("divergence")
        reason = v_row.get("reason", "")

        step_counts[verdict] = step_counts.get(verdict, 0) + 1

        ev_data = {}
        if v_row.get("evidence_json"):
            try:
                ev_data = json.loads(v_row["evidence_json"])
            except Exception:
                ev_data = {}

        art_payload = artifacts_by_step.get(sid, {})
        corrected = bool(ev_data.get("corrected") or art_payload.get("corrected", False))
        basis = ev_data.get("basis") or art_payload.get("basis")

        # Resolve citations with fetched_text
        citations: list[dict[str, Any]] = []
        art_cits = art_payload.get("citation_resolutions") or ev_data.get("citations") or []
        for c in art_cits:
            citations.append({
                "rel_path": c.get("rel_path", ""),
                "line_start": c.get("line_start", 0),
                "line_end": c.get("line_end", 0),
                "fetched_text": c.get("fetched_text"),
                "valid": bool(c.get("valid", True)),
            })

        # Assemble kept BD mappings
        kept_bd_ids = set(ev_data.get("kept_bd_ids") or art_payload.get("kept_bd_ids") or [])
        step_raw_mappings = mappings_by_step.get(sid, [])
        kept_mappings = []

        for m in step_raw_mappings:
            b_id = m["bd_id"]
            if b_id in kept_bd_ids or (verdict == "COVERED" and len(kept_bd_ids) == 0):
                b_step = bd_steps.get(b_id)
                b_branch = bd_branches.get(b_id)
                name = b_step["name"] if b_step else (f"Branch ({b_branch['branch_kind']})" if b_branch else b_id)
                func = b_step["functionality"] if b_step else (b_branch["guard_description"] if b_branch else "")
                b_verdict = bu_verdict_map.get(b_id)

                kept_mappings.append({
                    "bd_id": b_id,
                    "name": name,
                    "functionality": func,
                    "bd_kind": m.get("bd_kind", "step"),
                    "relation": m.get("relation", "realizes"),
                    "confidence": m.get("confidence", 0.8),
                    "bd_verdict": b_verdict,
                    "reason": m.get("reason", ""),
                })

        step_dict = {
            "id": sid,
            "flow_id": s["flow_id"],
            "ordinal": s["ordinal"],
            "kind": s["kind"],
            "section_id": s["section_id"],
            "text_en": s["text_en"] or s["text_ja"],
            "text_ja": s["text_ja"],
            "trigger_ja": s["trigger_ja"],
            "expected_ja": s["expected_ja"],
            "screen_name_ja": s["screen_name_ja"],
            "in_scope": bool(s["in_scope"]),
            "scope_note": s["scope_note"],
            "sheet": s["sheet"],
            "row_start": s["row_start"],
            "row_end": s["row_end"],
            "verdict": verdict,
            "divergence": divergence,
            "reason": reason,
            "corrected": corrected,
            "basis": basis,
            "citations": citations,
            "kept_bd_mappings": kept_mappings,
        }
        steps_by_flow.setdefault(s["flow_id"], []).append(step_dict)

    # 9. Assemble Flows with Activities (Phase U2)
    flow_counts = {
        "MATCHED": 0,
        "PARTIAL": 0,
        "DIVERGENT": 0,
        "UNCOVERED": 0,
        "OUT_OF_SCOPE": 0,
    }
    activity_counts = {
        "MATCHED": 0,
        "PARTIAL": 0,
        "DIVERGENT": 0,
        "UNCOVERED": 0,
        "OUT_OF_SCOPE": 0,
    }
    report_flows = []
    total_activities_count = 0

    for f in flows:
        f_id = f["id"]
        f_steps = steps_by_flow.get(f_id, [])
        f_v_row = flow_verdicts.get(f_id, {})

        f_match = f_v_row.get("verdict", "OUT_OF_SCOPE" if len(f_steps) == 0 else "UNCOVERED")
        flow_counts[f_match] = flow_counts.get(f_match, 0) + 1

        f_counts = {
            "COVERED": 0,
            "BD_MISSING": 0,
            "CONTRADICTED": 0,
            "UNVERIFIABLE": 0,
            "OUT_OF_SCOPE": 0,
        }
        for st in f_steps:
            f_counts[st["verdict"]] = f_counts.get(st["verdict"], 0) + 1

        # Assemble activities for this flow
        f_raw_activities = activities_by_flow.get(f_id, [])
        f_activities = []

        for act in f_raw_activities:
            total_activities_count += 1
            act_id = act["id"]
            act_v_row = activity_verdicts.get(act_id, {})
            act_match = act_v_row.get("verdict", "UNCOVERED")
            activity_counts[act_match] = activity_counts.get(act_match, 0) + 1

            act_matches = matches_by_act.get(act_id, [])
            match_status = act_matches[0]["match_status"] if act_matches else "NONE"
            matched_bd_flows = []
            for m in act_matches:
                if m.get("bd_flow_id") and m["bd_flow_id"] in b_flows:
                    bf = b_flows[m["bd_flow_id"]]
                    matched_bd_flows.append({
                        "bd_flow_id": bf["id"],
                        "name": bf["name"],
                        "description": bf.get("description", ""),
                    })

            try:
                member_ids = json.loads(act.get("member_step_ids_json") or "[]")
            except Exception:
                member_ids = []
            try:
                sheet_span = json.loads(act.get("sheet_span_json") or "[]")
            except Exception:
                sheet_span = []

            # Compute step counts for activity
            a_steps = [st for st in f_steps if st["id"] in member_ids]
            a_counts = {
                "COVERED": 0,
                "BD_MISSING": 0,
                "CONTRADICTED": 0,
                "UNVERIFIABLE": 0,
                "OUT_OF_SCOPE": 0,
            }
            for ast in a_steps:
                a_counts[ast["verdict"]] = a_counts.get(ast["verdict"], 0) + 1

            f_activities.append({
                "id": act_id,
                "flow_id": f_id,
                "ordinal": act["ordinal"],
                "name_en": act["name_en"],
                "name_ja": act["name_ja"],
                "summary_en": act["summary_en"],
                "member_step_ids": member_ids,
                "sheet_span": sheet_span,
                "step_count": len(a_steps),
                "action_count": act.get("action_count", 0),
                "error_rule_count": act.get("error_rule_count", 0),
                "expectation_count": act.get("expectation_count", 0),
                "row_start": act.get("row_start"),
                "row_end": act.get("row_end"),
                "origin": act.get("origin", "llm"),
                "match_status": match_status,
                "matched_bd_flows": matched_bd_flows,
                "activity_match": act_match,
                "counts": a_counts,
                "reason": act_v_row.get("reason", ""),
            })

        report_flows.append({
            "id": f_id,
            "doc_id": f["doc_id"],
            "ordinal": f["ordinal"],
            "name_en": f["name_en"] or f["name_ja"],
            "name_ja": f["name_ja"],
            "kind": f["kind"],
            "sheet": f["sheet"],
            "scope_note": f["scope_note"],
            "flow_match": f_match,
            "counts": f_counts,
            "activities": f_activities,
            "steps": f_steps,
        })

    # 10. Assemble BD_EXTRA list
    bd_extra_list = []
    for u_id, v_row in bd_verdicts.items():
        if v_row.get("verdict") == "BD_EXTRA":
            b_step = bd_steps.get(u_id)
            b_branch = bd_branches.get(u_id)
            name = b_step["name"] if b_step else (f"Branch ({b_branch['branch_kind']})" if b_branch else u_id)
            func = b_step["functionality"] if b_step else (b_branch["guard_description"] if b_branch else "")
            bd_extra_list.append({
                "bd_id": u_id,
                "name": name,
                "functionality": func,
                "bd_kind": v_row.get("ref_kind", "step"),
                "verdict": "BD_EXTRA",
                "divergence": v_row.get("divergence", "scope"),
                "reason": v_row.get("reason", "BD describes behavior not referenced in user flows"),
                "bd_verdict": bu_verdict_map.get(u_id),
            })

    in_scope_steps_count = sum(1 for s in steps if s.get("in_scope", 1) == 1)

    return {
        "doc": doc_info,
        "summary": {
            "doc_id": doc_id,
            "cluster_id": cluster_id,
            "snapshot_id": snapshot_id,
            "total_flows": len(flows),
            "total_activities": total_activities_count,
            "total_steps": len(steps),
            "in_scope_steps": in_scope_steps_count,
            "flow_counts": flow_counts,
            "activity_counts": activity_counts,
            "step_counts": step_counts,
            "bd_extra_count": len(bd_extra_list),
        },
        "flows": report_flows,
        "bd_extra": bd_extra_list,
    }


async def get_user_flow_graph(db: Any, doc_id: str) -> dict[str, Any]:
    """Retrieve user flow structured as BD-graph compatible business_flows envelope with activity nodes (Phase U2).

    Synthesizes:
    - Flow groups from user_flows
    - Activity nodes from user_activities (default high-level overview)
    - Sequential edges between consecutive activities
    - Detailed step payloads attached for leaf-level drilling
    """
    async with db.execute(
        "SELECT id, doc_id, ordinal, name_ja, name_en, kind, sheet, scope_note FROM user_flows WHERE doc_id = ? ORDER BY ordinal",
        (doc_id,),
    ) as cur:
        flow_rows = await cur.fetchall()
    flows = [
        dict(r) if hasattr(r, "keys") else {
            "id": r[0], "doc_id": r[1], "ordinal": r[2], "name_ja": r[3], "name_en": r[4], "kind": r[5], "sheet": r[6], "scope_note": r[7]
        }
        for r in flow_rows
    ]

    async with db.execute(
        "SELECT s.id, s.flow_id, s.ordinal, s.kind, s.section_id, s.text_ja, s.text_en, "
        "       s.trigger_ja, s.expected_ja, s.screen_name_ja, s.in_scope, s.scope_note, "
        "       s.sheet, s.row_start, s.row_end "
        "FROM user_steps s JOIN user_flows f ON s.flow_id = f.id "
        "WHERE f.doc_id = ? ORDER BY s.ordinal",
        (doc_id,),
    ) as cur:
        step_rows = await cur.fetchall()
    steps_by_flow: dict[str, list[dict[str, Any]]] = {}
    for r in step_rows:
        s = dict(r) if hasattr(r, "keys") else {
            "id": r[0], "flow_id": r[1], "ordinal": r[2], "kind": r[3], "section_id": r[4],
            "text_ja": r[5], "text_en": r[6], "trigger_ja": r[7], "expected_ja": r[8],
            "screen_name_ja": r[9], "in_scope": r[10], "scope_note": r[11],
            "sheet": r[12], "row_start": r[13], "row_end": r[14]
        }
        steps_by_flow.setdefault(s["flow_id"], []).append(s)

    async with db.execute(
        "SELECT id, flow_id, ordinal, name_en, name_ja, summary_en, member_step_ids_json, sheet_span_json, "
        "       step_count, action_count, error_rule_count, expectation_count, row_start, row_end, origin, created_at "
        "FROM user_activities WHERE flow_id IN (SELECT id FROM user_flows WHERE doc_id = ?) ORDER BY ordinal",
        (doc_id,),
    ) as cur:
        act_rows = await cur.fetchall()
    activities_by_flow: dict[str, list[dict[str, Any]]] = {}
    for r in act_rows:
        act = dict(r) if hasattr(r, "keys") else {
            "id": r[0], "flow_id": r[1], "ordinal": r[2], "name_en": r[3], "name_ja": r[4], "summary_en": r[5],
            "member_step_ids_json": r[6], "sheet_span_json": r[7], "step_count": r[8], "action_count": r[9],
            "error_rule_count": r[10], "expectation_count": r[11], "row_start": r[12], "row_end": r[13],
            "origin": r[14], "created_at": r[15],
        }
        activities_by_flow.setdefault(act["flow_id"], []).append(act)

    business_flows = []
    for f in flows:
        flow_id = f["id"]
        f_steps = sorted(steps_by_flow.get(flow_id, []), key=lambda x: x["ordinal"])
        f_activities = sorted(activities_by_flow.get(flow_id, []), key=lambda x: x["ordinal"])

        # Format leaf step payloads
        leaf_step_payloads = []
        for s in f_steps:
            func_parts = []
            if s.get("trigger_ja"):
                func_parts.append(f"Trigger: {s['trigger_ja']}")
            if s.get("expected_ja"):
                func_parts.append(f"Expected: {s['expected_ja']}")
            func_str = " | ".join(func_parts) if func_parts else (s.get("section_id") or s.get("kind") or "")

            leaf_step_payloads.append({
                "id": s["id"],
                "flow_id": flow_id,
                "name": s.get("text_en") or s.get("text_ja") or f"Step {s['ordinal']}",
                "functionality": func_str,
                "ordinal": s["ordinal"],
                "source_node_ids": [],
                "doc_line_start": None,
                "doc_line_end": None,
                "section_id": s.get("section_id"),
                "kind": s.get("kind"),
            })

        # Activity nodes (Level A)
        activity_step_payloads = []
        for act in f_activities:
            activity_step_payloads.append({
                "id": f"act:{act['id']}",
                "flow_id": flow_id,
                "name": act["name_en"],
                "functionality": act.get("summary_en") or "",
                "ordinal": act["ordinal"],
                "source_node_ids": json.loads(act.get("member_step_ids_json") or "[]"),
                "doc_line_start": act.get("row_start"),
                "doc_line_end": act.get("row_end"),
                "section_id": None,
                "kind": "activity",
            })

        active_nodes = activity_step_payloads if activity_step_payloads else leaf_step_payloads
        branches = []
        # Sequential branches between consecutive active nodes
        for i in range(len(active_nodes) - 1):
            a1 = active_nodes[i]
            a2 = active_nodes[i + 1]
            branches.append({
                "id": f"br:{a1['id']}->{a2['id']}",
                "flow_id": flow_id,
                "source_step_id": a1["id"],
                "target_step_id": a2["id"],
                "branch_kind": "SUCCESS",
                "guard_description": "",
                "source_edge_ids": [],
            })

        # When in leaf step mode (no activities), also synthesize error branches
        if not activity_step_payloads:
            for idx, s in enumerate(f_steps):
                if s.get("kind") == "error_rule":
                    prec_action = None
                    for p_idx in range(idx - 1, -1, -1):
                        if f_steps[p_idx].get("kind") == "action":
                            prec_action = f_steps[p_idx]
                            break
                    source_id = prec_action["id"] if prec_action else s["id"]
                    branches.append({
                        "id": f"br:err:{s['id']}",
                        "flow_id": flow_id,
                        "source_step_id": source_id,
                        "target_step_id": None,
                        "branch_kind": "ERROR",
                        "guard_description": s.get("text_en") or s.get("text_ja") or "Error Rule",
                        "source_edge_ids": [],
                    })

        business_flows.append({
            "id": f["id"],
            "ordinal": f["ordinal"],
            "name": f.get("name_en") or f.get("name_ja") or f"Flow {f['ordinal']}",
            "block_key": f.get("sheet") or "",
            "origin": "fallback",
            "description": f.get("scope_note") or "",
            "steps": active_nodes,
            "leaf_steps": leaf_step_payloads,
            "branches": branches,
        })

    return {"business_flows": business_flows}


