"""Service implementing Doc↔Code completeness & Structural Link comparison algorithms."""

import json
import re
from datetime import UTC, datetime
from typing import Any

from infrastructure.db.database import get_db

from .relation_adapter import CanonicalCodeRelation, load_canonical_code_relations
from .types import (
    ComparisonSummary,
    DocCodeCompareResponse,
    DocCodeRelationCompareResponse,
    EligibilityInfo,
    PredicateRelationResult,
    RelationComparisonDetail,
    RelationEvidenceItem,
    RelationSummary,
    TypeComparisonResult,
)


class DocCodeCompareService:
    async def compare(self, cluster_id: str, snapshot_id: str) -> DocCodeCompareResponse:
        db = get_db()

        # 1. Fetch doc_graph_nodes for cluster_id
        async with db.execute(
            """
            SELECT id, node_type, display_name, provenance
            FROM doc_graph_nodes WHERE cluster_id=?
            """,
            (cluster_id,),
        ) as cur:
            doc_nodes = [dict(r) for r in await cur.fetchall()]

        # Determine SCOPE of cluster
        scope_programs = {
            r["display_name"].strip().upper()
            for r in doc_nodes
            if r["node_type"] == "program"
        }
        scope_jobs = {
            r["display_name"].strip().upper()
            for r in doc_nodes
            if r["node_type"] == "job"
        }

        # 2. Fetch source_facts for snapshot_id
        async with db.execute(
            """
            SELECT fact_type, semantic_key, parent_key, rel_path, line_start, line_end, name, value
            FROM source_facts
            WHERE snapshot_id=? AND fact_type IN ('program', 'job', 'step', 'dd', 'dataset')
            """,
            (snapshot_id,),
        ) as cur:
            code_facts = [dict(r) for r in await cur.fetchall()]

        # Filter code facts by cluster scope
        code_by_type: dict[str, dict[str, list[dict[str, Any]]]] = {
            t: {} for t in ["program", "job", "step", "dd", "dataset"]
        }
        scope_rel_paths: set[str] = set()

        for fact in code_facts:
            ft = fact["fact_type"]
            skey = fact["semantic_key"]
            pkey = fact["parent_key"] or ""
            in_scope = False

            if ft == "program":
                prog_name = skey.rsplit("/", 1)[-1].upper()
                if prog_name in scope_programs:
                    in_scope = True
            elif ft in ("job", "step", "dd"):
                parts = skey.split("/")
                if len(parts) >= 2:
                    job_part = parts[1].split(".")[0].upper()
                    if job_part in scope_jobs:
                        in_scope = True
            elif ft == "dataset":
                if pkey.startswith("step/"):
                    step_part = pkey.split("/", 1)[1]
                    job_part = step_part.split(".")[0].upper()
                    if job_part in scope_jobs:
                        in_scope = True

            if in_scope and ft in code_by_type:
                code_by_type[ft].setdefault(skey, []).append(fact)
                scope_rel_paths.add(fact["rel_path"])

        # Organize doc nodes by type
        doc_by_type: dict[str, dict[str, dict[str, Any]]] = {
            t: {} for t in ["program", "job", "step", "dd", "dataset"]
        }
        for node in doc_nodes:
            t = node["node_type"]
            if t in doc_by_type:
                doc_by_type[t][node["id"]] = node

        # 3. Check Eligibility Gate
        async with db.execute(
            "SELECT rel_path, status FROM source_parse_diagnostics WHERE snapshot_id=?",
            (snapshot_id,),
        ) as cur:
            diag_rows = [dict(r) for r in await cur.fetchall()]

        diag_map = {r["rel_path"]: r["status"] for r in diag_rows}
        non_ok_scope_files = [p for p in scope_rel_paths if diag_map.get(p) != "ok"]

        authoritative = len(scope_rel_paths) > 0 and len(non_ok_scope_files) == 0
        if len(scope_rel_paths) == 0:
            eligibility_reason = "No source files found in snapshot matching cluster scope"
        elif authoritative:
            eligibility_reason = "All source files in cluster scope parsed with status ok"
        else:
            eligibility_reason = (
                f"Source parse not authoritative ({len(non_ok_scope_files)} scope file(s) "
                "have non-ok status or missing diagnostics)"
            )

        per_type_results: list[TypeComparisonResult] = []
        total_matched = 0
        total_undocumented = 0
        total_missing = 0
        total_unknown = 0

        for t in ["program", "job", "step", "dd", "dataset"]:
            doc_keys = set(doc_by_type[t].keys())
            code_keys = set(code_by_type[t].keys())

            matched_keys = doc_keys & code_keys
            undocumented_keys = code_keys - doc_keys
            doc_only_keys = doc_keys - code_keys

            undocumented_list = []
            for k in sorted(undocumented_keys):
                rep = code_by_type[t][k][0]
                undocumented_list.append({
                    "key": k,
                    "rel_path": rep["rel_path"],
                    "line_start": rep["line_start"],
                    "line_end": rep["line_end"],
                    "name": rep["name"] or rep["value"] or k,
                })

            missing_list = []
            unknown_list = []

            for k in sorted(doc_only_keys):
                d_item = doc_by_type[t][k]
                prov = d_item["provenance"]
                if isinstance(prov, str) and prov.strip():
                    try:
                        prov = json.loads(prov)
                    except Exception:
                        pass
                item_entry = {
                    "key": k,
                    "display_name": d_item["display_name"],
                    "provenance": prov,
                }
                if authoritative:
                    missing_list.append(item_entry)
                else:
                    unknown_list.append(item_entry)

            per_type_results.append(
                TypeComparisonResult(
                    type=t,
                    doc_count=len(doc_keys),
                    code_count=len(code_keys),
                    matched=len(matched_keys),
                    undocumented=undocumented_list,
                    missing=missing_list,
                    unknown=unknown_list,
                )
            )

            total_matched += len(matched_keys)
            total_undocumented += len(undocumented_list)
            total_missing += len(missing_list)
            total_unknown += len(unknown_list)

        return DocCodeCompareResponse(
            cluster_id=cluster_id,
            snapshot_id=snapshot_id,
            per_type=per_type_results,
            not_assessed=[
                "field",
                "section",
                "paragraph",
                "performs",
                "branch",
                "loop",
                "handler",
                "exec_block",
                "copybook",
            ],
            eligibility=EligibilityInfo(
                authoritative=authoritative,
                reason=eligibility_reason,
            ),
            summary=ComparisonSummary(
                matched=total_matched,
                undocumented=total_undocumented,
                missing=total_missing,
                unknown=total_unknown,
            ),
        )

    async def compare_relations(
        self, cluster_id: str, snapshot_id: str
    ) -> DocCodeRelationCompareResponse:
        """Compare structural relationships (calls, copies, runs, binds_dd) between Doc and Code."""
        db = get_db()

        # 1. Snapshot binding check
        async with db.execute(
            "SELECT id, snapshot_id FROM doc_graph_clusters WHERE id=?",
            (cluster_id,),
        ) as cur:
            cluster_row = await cur.fetchone()

        status: str = "OK"
        if not cluster_row:
            status = "UNBOUND"
        else:
            bound_snap = cluster_row["snapshot_id"]
            if bound_snap is None:
                status = "UNBOUND"
            elif bound_snap != snapshot_id:
                status = "STALE_INPUT"

        if status == "STALE_INPUT":
            return DocCodeRelationCompareResponse(
                cluster_id=cluster_id,
                snapshot_id=snapshot_id,
                status="STALE_INPUT",
                per_predicate=[],
                summary=RelationSummary(matched=0, doc_only=0, code_only=0, unknown=0),
            )

        # 2. Scope determination from doc_graph_nodes
        async with db.execute(
            "SELECT id, node_type, display_name FROM doc_graph_nodes WHERE cluster_id=?",
            (cluster_id,),
        ) as cur:
            doc_nodes = [dict(r) for r in await cur.fetchall()]

        scope_programs = {
            r["display_name"].strip().upper()
            for r in doc_nodes
            if r["node_type"] == "program"
        }
        scope_jobs = {
            r["display_name"].strip().upper()
            for r in doc_nodes
            if r["node_type"] == "job"
        }

        # 3. Fetch doc_graph_assertions
        async with db.execute(
            """
            SELECT id, side, predicate, subject, object, value, qualifiers
            FROM doc_graph_assertions
            WHERE cluster_id=? AND predicate IN ('calls', 'copies', 'runs', 'binds_dd')
            """,
            (cluster_id,),
        ) as cur:
            doc_assertions = [dict(r) for r in await cur.fetchall()]

        # 4. Fetch canonical code relations
        code_relations = await load_canonical_code_relations(
            db, snapshot_id, scope_programs, scope_jobs
        )

        # 5. Fetch parse diagnostics for eligibility
        async with db.execute(
            "SELECT rel_path, status FROM source_parse_diagnostics WHERE snapshot_id=?",
            (snapshot_id,),
        ) as cur:
            diag_rows = [dict(r) for r in await cur.fetchall()]
        diag_map = {r["rel_path"]: r["status"] for r in diag_rows}

        # Build subject_rel_path_map for BUG 2:
        async with db.execute(
            """
            SELECT rel_path, fact_type, semantic_key, parent_key, name
            FROM source_facts
            WHERE snapshot_id=?
            """,
            (snapshot_id,),
        ) as cur:
            all_snap_facts = [dict(r) for r in await cur.fetchall()]

        subject_rel_path_map: dict[str, str] = {}
        for sf in all_snap_facts:
            skey = sf["semantic_key"]
            rpath = sf["rel_path"]
            ft = sf["fact_type"]
            subject_rel_path_map[skey] = rpath
            if ft == "program" and sf.get("name"):
                subject_rel_path_map[f"program/{sf['name'].strip().upper()}"] = rpath
            elif ft == "job" and sf.get("name"):
                subject_rel_path_map[f"job/{sf['name'].strip().upper()}"] = rpath

        # Group doc assertions by (side, predicate, subject, object) for BUG 4:
        doc_rel_map: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
        for a in doc_assertions:
            side = a["side"] or "DOC"
            pred = a["predicate"]
            subj = a["subject"]
            obj = a["object"] or ""
            doc_rel_map.setdefault((side, pred, subj, obj), []).append(a)

        # Group code relations by (predicate, subject_key, object_key)
        code_rel_map: dict[tuple[str, str, str], list[CanonicalCodeRelation]] = {}
        for cr in code_relations:
            key = (cr.predicate, cr.subject_key, cr.object_key)
            code_rel_map.setdefault(key, []).append(cr)

        doc_keys_3tuple = {(p, s, o) for (_, p, s, o) in doc_rel_map.keys()}
        code_only_3tuples = set(code_rel_map.keys()) - doc_keys_3tuple

        all_units: list[tuple[str | None, str, str, str]] = []
        for key_4 in doc_rel_map.keys():
            all_units.append(key_4)
        for (p, s, o) in code_only_3tuples:
            all_units.append((None, p, s, o))

        all_units.sort(key=lambda x: (x[1], x[2], x[3], x[0] or ""))

        # Organize by predicate
        pred_results_map: dict[str, list[RelationComparisonDetail]] = {
            p: [] for p in ["calls", "copies", "runs", "binds_dd"]
        }

        total_matched = 0
        total_doc_only = 0
        total_code_only = 0
        total_unknown = 0

        for side, pred, subj, obj in all_units:
            if pred not in pred_results_map:
                continue

            # BUG 1 FIX: dd -> dataset is NOT_ASSESSED, do NOT compare or emit DOC_ONLY
            if pred == "binds_dd" and subj.startswith("dd/") and obj.startswith("dataset/"):
                continue

            doc_items = doc_rel_map.get((side, pred, subj, obj), []) if side else []
            code_items = code_rel_map.get((pred, subj, obj), [])

            doc_count = len(doc_items)
            code_count = len(code_items)

            # BUG 2 FIX: Resolve subject source file path correctly when code_items is empty
            if code_items:
                sub_rel_path = code_items[0].rel_path
            else:
                sub_rel_path = subject_rel_path_map.get(subj)

            is_auth = (
                sub_rel_path is not None and diag_map.get(sub_rel_path) == "ok"
            )

            # Determine endpoint_verdict
            if doc_items and code_items:
                endpoint_verdict = "MATCH"
            elif doc_items and not code_items:
                endpoint_verdict = "DOC_ONLY" if is_auth else "UNKNOWN"
            elif code_items and not doc_items:
                endpoint_verdict = "CODE_ONLY"
            else:
                endpoint_verdict = "UNKNOWN"

            # BUG 3 & BUG 4 FIX: Site parsing and multiplicity/count logic
            multiplicity_verdict = "NOT_APPLICABLE"
            if pred == "calls":
                doc_lines: list[int] = []
                numeric_val: int | None = None

                for di in doc_items:
                    val = di.get("value")
                    if val and str(val).strip().isdigit():
                        numeric_val = int(str(val).strip())

                    quals = di.get("qualifiers")
                    if quals:
                        if isinstance(quals, str):
                            try:
                                quals = json.loads(quals)
                            except Exception:
                                quals = {}
                        if isinstance(quals, dict) and "call_sites" in quals:
                            sites = quals["call_sites"]
                            for s in sites:
                                m = re.search(r"L?0*(\d+)", str(s).strip())
                                if m:
                                    doc_lines.append(int(m.group(1)))

                code_lines = [ci.line_start for ci in code_items if ci.line_start > 0]

                if doc_lines:
                    doc_count = len(doc_lines)
                    if code_lines:
                        if sorted(doc_lines) == sorted(code_lines):
                            multiplicity_verdict = "EXACT_SITE_MATCH"
                        elif len(doc_lines) == len(code_lines):
                            multiplicity_verdict = "COUNT_ONLY_MATCH"
                        else:
                            multiplicity_verdict = "COUNT_MISMATCH"
                    else:
                        multiplicity_verdict = "COUNT_MISMATCH"
                elif numeric_val is not None:
                    doc_count = numeric_val
                    if code_items:
                        if doc_count == len(code_items):
                            multiplicity_verdict = "COUNT_ONLY_MATCH"
                        else:
                            multiplicity_verdict = "COUNT_MISMATCH"
                    else:
                        multiplicity_verdict = "COUNT_MISMATCH"
                else:
                    doc_count = len(doc_items)
                    multiplicity_verdict = "NOT_APPLICABLE"

            # Build evidence items
            evidence_items: list[RelationEvidenceItem] = []
            for ci in code_items:
                evidence_items.append(
                    RelationEvidenceItem(
                        occurrence_key=ci.occurrence_key,
                        rel_path=ci.rel_path,
                        line_start=ci.line_start,
                        line_end=ci.line_end,
                        source_store=ci.source_store,
                        match_kind="code_site",
                    )
                )
            for di in doc_items:
                evidence_items.append(
                    RelationEvidenceItem(
                        occurrence_key=f"doc_assertion_{di['id']}",
                        rel_path=di.get("doc_id"),
                        source_store="doc_graph_assertions",
                        match_kind="doc_site",
                    )
                )

            eligibility_str = "authoritative" if is_auth else "non_authoritative"
            reason_str = (
                "Subject source file parsed ok"
                if is_auth
                else "Subject source file parse status not ok or unresolvable"
            )

            detail = RelationComparisonDetail(
                doc_assertion_id=doc_items[0]["id"] if doc_items else None,
                side=side,
                subject_key=subj,
                object_key=obj,
                endpoint_verdict=endpoint_verdict,
                multiplicity_verdict=multiplicity_verdict,
                doc_count=doc_count,
                code_count=code_count,
                eligibility=eligibility_str,
                reason=reason_str,
                evidence=evidence_items,
            )

            pred_results_map[pred].append(detail)

            if endpoint_verdict == "MATCH":
                total_matched += 1
            elif endpoint_verdict == "DOC_ONLY":
                total_doc_only += 1
            elif endpoint_verdict == "CODE_ONLY":
                total_code_only += 1
            elif endpoint_verdict == "UNKNOWN":
                total_unknown += 1

        # Purge and persist into DB tables doc_code_relation_comparisons & evidence
        now = datetime.now(UTC).isoformat()
        await db.execute(
            """
            DELETE FROM doc_code_relation_evidence
            WHERE comparison_id IN (
                SELECT id FROM doc_code_relation_comparisons WHERE cluster_id=? AND snapshot_id=?
            )
            """,
            (cluster_id, snapshot_id),
        )
        await db.execute(
            "DELETE FROM doc_code_relation_comparisons WHERE cluster_id=? AND snapshot_id=?",
            (cluster_id, snapshot_id),
        )

        per_predicate_list: list[PredicateRelationResult] = []
        for p in ["calls", "copies", "runs", "binds_dd"]:
            details = pred_results_map[p]
            p_matched = sum(1 for d in details if d.endpoint_verdict == "MATCH")
            p_doc_only = sum(1 for d in details if d.endpoint_verdict == "DOC_ONLY")
            p_code_only = sum(1 for d in details if d.endpoint_verdict == "CODE_ONLY")
            p_unknown = sum(1 for d in details if d.endpoint_verdict == "UNKNOWN")

            # Persist to DB
            for d in details:
                cur = await db.execute(
                    """
                    INSERT INTO doc_code_relation_comparisons (
                        cluster_id, snapshot_id, doc_assertion_id, side, predicate,
                        subject_key, object_key, qualifier_key, endpoint_verdict,
                        multiplicity_verdict, doc_count, code_count, eligibility,
                        reason, comparator_version, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, '1.0', ?)
                    """,
                    (
                        cluster_id,
                        snapshot_id,
                        d.doc_assertion_id,
                        d.side,
                        p,
                        d.subject_key,
                        d.object_key,
                        d.endpoint_verdict,
                        d.multiplicity_verdict,
                        d.doc_count,
                        d.code_count,
                        d.eligibility,
                        d.reason,
                        now,
                    ),
                )
                comp_id = cur.lastrowid
                for ev in d.evidence:
                    await db.execute(
                        """
                        INSERT INTO doc_code_relation_evidence (
                            comparison_id, occurrence_key, rel_path, line_start, line_end,
                            source_store, match_kind
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            comp_id,
                            ev.occurrence_key,
                            ev.rel_path,
                            ev.line_start or 0,
                            ev.line_end or 0,
                            ev.source_store,
                            ev.match_kind,
                        ),
                    )

            per_predicate_list.append(
                PredicateRelationResult(
                    predicate=p,
                    matched=p_matched,
                    doc_only=p_doc_only,
                    code_only=p_code_only,
                    unknown=p_unknown,
                    details=details,
                )
            )

        await db.commit()

        return DocCodeRelationCompareResponse(
            cluster_id=cluster_id,
            snapshot_id=snapshot_id,
            status=status,
            per_predicate=per_predicate_list,
            not_assessed=[
                "binds_dd(dd->dataset)",
                "field/PIC",
                "behavioral(br/tbd/ddlimit)",
                "access_mode_correctness",
            ],
            summary=RelationSummary(
                matched=total_matched,
                doc_only=total_doc_only,
                code_only=total_code_only,
                unknown=total_unknown,
            ),
        )
