"""Report and query helpers for Phase U User Flow Alignment (TICKET U4).

Builds the structured user flow report containing:
- The publication run_id: the newest run that wrote a completion marker (an in-progress or failed
  run is invisible; every run-scoped read is filtered by that run_id).
- High-level user flows with flow_match rollup and step-level counts.
- Activities with per-(activity, bd_flow) pair cells plus the best status over those pairs.
- Step-level cards with primary English labels (text_en / name_en) + Japanese originals.
- Verifiable evidence: verifier-KEPT BD mappings and code citations with verbatim fetched_text.
- BD_EXTRA list (spec finding) and BD_UNMAPPED list (tier-2 recall artifact), kept separate.
- Branch-coverage rollup with per-branch fan-in and the user-flow-gap flag.
- Summary counts for flows and steps.
"""
from __future__ import annotations

import json
from typing import Any

from ._align import COVERAGE_RELATIONS


async def _resolve_published_run_id(
    db: Any,
    doc_id: str,
    cluster_id: str | None,
    snapshot_id: str | None,
    pinned_run_id: str | None = None,
) -> str | None:
    """Return the run this report may be built from, or None when nothing is published.

    A run qualifies only if it wrote a `__run_complete__` marker whose payload names the SAME
    (doc_id, cluster_id, snapshot_id) the caller asked about — a marker is the run's own record of
    what it analysed, so a run of another cluster/snapshot can never answer this request. A pinned
    `run_id` narrows the search; it does not waive the marker or the tuple check.
    """
    async with db.execute(
        "SELECT run_id, payload FROM user_run_artifacts "
        "WHERE doc_id = ? AND ref_id = '__run_complete__' "
        "ORDER BY created_at DESC",
        (doc_id,),
    ) as cur:
        rows = await cur.fetchall()

    for r in rows:
        r_run_id = r[0] if isinstance(r, (tuple, list)) else r["run_id"]
        r_payload = r[1] if isinstance(r, (tuple, list)) else r["payload"]
        if pinned_run_id is not None and r_run_id != pinned_run_id:
            continue
        try:
            payload = json.loads(r_payload or "{}")
        except Exception:
            payload = {}
        if cluster_id is not None and payload.get("cluster_id") != cluster_id:
            continue
        if snapshot_id is not None and payload.get("snapshot_id") != snapshot_id:
            continue
        return r_run_id

    return None


async def get_user_flow_report(
    db: Any,
    doc_id: str,
    cluster_id: str | None = None,
    snapshot_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Retrieve full User Flow Alignment report JSON for the specified doc_id and last completed run_id."""
    # 0. Resolve the publication run_id: the newest run that wrote a completion marker FOR THIS
    # (doc, cluster, snapshot). A run without the marker (in progress or failed mid-way) is
    # invisible, and a completed run of another cluster/snapshot must never answer this request.
    # An explicit `run_id` pins a run but does not bypass either check.
    chosen_run_id = await _resolve_published_run_id(db, doc_id, cluster_id, snapshot_id, run_id)

    # 1. Fetch document metadata
    async with db.execute("SELECT id, source_name, file_hash, imported_at FROM user_flow_docs WHERE id = ?", (doc_id,)) as cur:
        row = await cur.fetchone()
        doc_info = dict(row) if row and hasattr(row, "keys") else (
            {"id": row[0], "source_name": row[1], "file_hash": row[2], "imported_at": row[3]} if row else None
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

    # 4. Fetch verdicts for this (doc_id, cluster_id, snapshot_id, chosen_run_id). With no
    # published run every run-scoped read stays empty — partial state is never displayed.
    verdict_rows: list[Any] = []
    if chosen_run_id:
        async with db.execute(
            "SELECT id, run_id, side, ref_id, ref_kind, verdict, divergence, reason, evidence_json "
            "FROM user_verdicts WHERE doc_id = ? AND cluster_id = ? AND snapshot_id = ? AND run_id = ?",
            (doc_id, cluster_id, snapshot_id, chosen_run_id),
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

    act_match_rows: list[Any] = []
    if chosen_run_id:
        async with db.execute(
            "SELECT activity_id, bd_flow_id, match_status, confidence, reason FROM user_activity_matches "
            "WHERE activity_id IN (SELECT id FROM user_activities WHERE flow_id IN (SELECT id FROM user_flows WHERE doc_id = ?)) "
            "AND run_id = ?",
            (doc_id, chosen_run_id),
        ) as cur:
            act_match_rows = await cur.fetchall()

    matches_by_act: dict[str, list[dict[str, Any]]] = {}
    for r in act_match_rows:
        m = dict(r) if hasattr(r, "keys") else {
            "activity_id": r[0], "bd_flow_id": r[1], "match_status": r[2], "confidence": r[3], "reason": r[4]
        }
        matches_by_act.setdefault(m["activity_id"], []).append(m)

    # 5. Fetch step verdict audit artifacts for fetched_text extraction
    verdict_art_rows: list[Any] = []
    align_art_rows: list[Any] = []
    if chosen_run_id:
        async with db.execute(
            "SELECT ref_id, payload FROM user_run_artifacts WHERE doc_id = ? AND run_id = ? AND ref_id LIKE 'verdict:%'",
            (doc_id, chosen_run_id),
        ) as cur:
            verdict_art_rows = await cur.fetchall()
        async with db.execute(
            "SELECT ref_id, payload FROM user_run_artifacts WHERE doc_id = ? AND run_id = ? AND ref_id LIKE 'align:%'",
            (doc_id, chosen_run_id),
        ) as cur:
            align_art_rows = await cur.fetchall()

    # Mapper-side review flags per (step, bd unit) — e.g. a confident member_of whose guard class
    # nothing corroborates. The gate keeps such a mapping; the report must not hide it.
    mapping_flags: dict[str, dict[str, str]] = {}
    for r in align_art_rows:
        ref_id = r[0] if isinstance(r, (tuple, list)) else r["ref_id"]
        payload_str = r[1] if isinstance(r, (tuple, list)) else r["payload"]
        step_id = ref_id.split(":", 1)[1] if ":" in ref_id else ref_id
        try:
            payload = json.loads(payload_str)
        except Exception:
            continue
        for m in payload.get("mappings", []):
            flag = m.get("guard_class_flag")
            if flag and m.get("bd_id"):
                mapping_flags.setdefault(step_id, {})[m["bd_id"]] = flag

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
    mapping_rows: list[Any] = []
    if chosen_run_id:
        async with db.execute(
            "SELECT user_step_id, bd_kind, bd_id, relation, confidence, reason FROM user_bd_mappings "
            "WHERE user_step_id IN (SELECT s.id FROM user_steps s JOIN user_flows f ON s.flow_id = f.id WHERE f.doc_id = ?) "
            "AND run_id = ?",
            (doc_id, chosen_run_id),
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

        # Assemble kept BD mappings (strict: verifier-kept mappings only)
        kept_bd_ids = set(ev_data.get("kept_bd_ids") or art_payload.get("kept_bd_ids") or [])
        step_raw_mappings = mappings_by_step.get(sid, [])
        kept_mappings = []

        for m in step_raw_mappings:
            b_id = m["bd_id"]
            if b_id in kept_bd_ids:
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
                    # None for verifier-added mappings: the verifier asserts the link, it does not
                    # score it — inventing 0.8 here would fake a confidence nobody produced.
                    "confidence": m.get("confidence"),
                    "coverage_bearing": (m.get("relation") or "").strip().lower() in COVERAGE_RELATIONS,
                    "guard_class_flag": mapping_flags.get(sid, {}).get(b_id),
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

            def _status_rank(s: str) -> int:
                return {"FULLY": 3, "PARTIAL": 2, "UNRESOLVED": 1, "NONE": 0}.get(s, 0)

            act_matches = matches_by_act.get(act_id, [])
            if act_matches:
                match_status = max((m["match_status"] for m in act_matches), key=_status_rank)
            else:
                # No published run yet => the match was never attempted; "NO BD FLOW" would be a lie.
                match_status = "NONE" if chosen_run_id else "UNRESOLVED"

            matched_bd_flows = []
            seen_bf_ids = set()
            for m in act_matches:
                bf_id = m.get("bd_flow_id")
                if bf_id and bf_id in b_flows and bf_id not in seen_bf_ids:
                    seen_bf_ids.add(bf_id)
                    bf = b_flows[bf_id]
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

            pair_matches = [
                {
                    "bd_flow_id": m.get("bd_flow_id"),
                    "bd_flow_name": b_flows.get(m["bd_flow_id"], {}).get("name", "") if m.get("bd_flow_id") else None,
                    "match_status": m["match_status"],
                    "confidence": m.get("confidence", 0.0),
                    "reason": m.get("reason", ""),
                }
                for m in act_matches
            ]

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
                "pair_matches": pair_matches,
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
            "divergence": f_v_row.get("divergence"),
            "reason": f_v_row.get("reason", ""),
            "counts": f_counts,
            "activities": f_activities,
            "steps": f_steps,
        })

    # 10. Assemble BD_EXTRA and BD_UNMAPPED lists (Codex R2/R5)
    bd_extra_list = []
    bd_unmapped_list = []
    for u_id, v_row in bd_verdicts.items():
        v = v_row.get("verdict")
        b_step = bd_steps.get(u_id)
        b_branch = bd_branches.get(u_id)
        name = b_step["name"] if b_step else (f"Branch ({b_branch['branch_kind']})" if b_branch else u_id)
        func = b_step["functionality"] if b_step else (b_branch["guard_description"] if b_branch else "")
        flow_id = b_step["flow_id"] if b_step else (b_branch["flow_id"] if b_branch else "")
        flow_name = b_flows.get(flow_id, {}).get("name", "")

        entry = {
            "bd_id": u_id,
            "name": name,
            "flow_id": flow_id,
            "flow_name": flow_name,
            "functionality": func,
            "bd_kind": v_row.get("ref_kind", "step"),
            "verdict": v,
            "divergence": v_row.get("divergence"),
            "reason": v_row.get("reason", ""),
            "bd_verdict": bu_verdict_map.get(u_id),
        }
        if v == "BD_EXTRA":
            bd_extra_list.append(entry)
        elif v == "BD_UNMAPPED":
            bd_unmapped_list.append(entry)

    # 11. Assemble Branch Coverage Rollup (Task C / D / G)
    steps_lookup = {s["id"]: s for s in steps}
    flows_lookup = {fl["id"]: fl for fl in flows}
    branch_coverage = []
    for b_id, b_branch in bd_branches.items():
        b_kind = (b_branch.get("branch_kind") or "").lower()
        if b_kind in ("success", "normal"):
            continue
        flow_id = b_branch.get("flow_id", "")
        flow_name = b_flows.get(flow_id, {}).get("name", "")

        # Fan-in = COVERED steps whose verifier-KEPT mapping onto this branch is coverage-bearing
        # (`related` marks adjacency, so it must not count as a user case realizing the guard).
        incoming_step_ids = []
        incoming_steps = []
        for sid, m_list in mappings_by_step.items():
            if step_verdicts.get(sid, {}).get("verdict") != "COVERED":
                continue
            ev_str = step_verdicts.get(sid, {}).get("evidence_json") or "{}"
            try:
                k_ids = json.loads(ev_str).get("kept_bd_ids", [])
            except Exception:
                k_ids = []
            if b_id not in k_ids:
                continue
            rel = next(
                ((m.get("relation") or "").strip().lower() for m in m_list if m["bd_id"] == b_id),
                "",
            )
            if rel in COVERAGE_RELATIONS:
                incoming_step_ids.append(sid)
                s_obj = steps_lookup.get(sid)
                if s_obj:
                    fl_obj = flows_lookup.get(s_obj.get("flow_id", ""))
                    incoming_steps.append({
                        "id": sid,
                        "ordinal": s_obj.get("ordinal", 0),
                        "text_en": s_obj.get("text_en") or s_obj.get("text_ja", ""),
                        "flow_name": (fl_obj.get("name_en") or fl_obj.get("name_ja", "")) if fl_obj else "",
                    })

        # Check parent flow Tier-1 match status across activities
        parent_matches = [
            m.get("match_status")
            for m_list in matches_by_act.values() for m in m_list
            if m.get("bd_flow_id") == flow_id
        ]
        if any(ms == "FULLY" for ms in parent_matches):
            parent_tier1_status = "FULLY"
        elif any(ms == "PARTIAL" for ms in parent_matches):
            parent_tier1_status = "PARTIAL"
        elif any(ms == "UNRESOLVED" for ms in parent_matches):
            parent_tier1_status = "UNRESOLVED"
        else:
            parent_tier1_status = "NONE" if chosen_run_id else "UNRESOLVED"

        flow_has_match = parent_tier1_status in ("FULLY", "PARTIAL")
        is_user_flow_gap = len(incoming_step_ids) == 0 and flow_has_match

        branch_coverage.append({
            "branch_id": b_id,
            "flow_id": flow_id,
            "flow_name": flow_name,
            "branch_kind": b_branch.get("branch_kind", "error"),
            "guard_description": b_branch.get("guard_description", ""),
            "incoming_step_count": len(incoming_step_ids),
            "incoming_step_ids": incoming_step_ids,
            "incoming_steps": incoming_steps,
            "parent_tier1_status": parent_tier1_status,
            "is_user_flow_gap": is_user_flow_gap,
            "bd_verdict": bu_verdict_map.get(b_id),
        })

    in_scope_steps_count = sum(1 for s in steps if s.get("in_scope", 1) == 1)

    return {
        "doc": doc_info,
        "run_id": chosen_run_id,
        "summary": {
            "doc_id": doc_id,
            "run_id": chosen_run_id,
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
            "bd_unmapped_count": len(bd_unmapped_list),
        },
        "flows": report_flows,
        "bd_extra": bd_extra_list,
        "bd_unmapped": bd_unmapped_list,
        "branch_coverage": branch_coverage,
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


