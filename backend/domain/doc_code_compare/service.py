"""Service implementing Doc↔Code completeness & Structural Link comparison algorithms."""

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any

from infrastructure.db.database import get_db
from shared.utils import utc_now_iso

from .relation_adapter import CanonicalCodeRelation, load_canonical_code_relations
from .types import (
    AiAssessmentResponse,
    AiConcern,
    BdGroupItem,
    ComparisonSummary,
    CrossLinkItem,
    DocCodeCompareResponse,
    DocCodeRelationCompareResponse,
    EligibilityInfo,
    LinkedGraphEdge,
    LinkedGraphNode,
    LinkedGraphResponse,
    NotAssessedCoverage,
    NotAssessedEntityCoverage,
    NotAssessedRelationCoverage,
    PredicateRelationResult,
    RelationComparisonDetail,
    RelationEvidenceItem,
    RelationSummary,
    TypeComparisonResult,
)


def _parse_llm_json(text: str | None) -> dict[str, Any]:
    """Tolerantly parse JSON output from LLM."""
    if not text:
        raise ValueError("empty LLM content")
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1] if t.count("```") >= 2 else t.strip("`")
        if t.lstrip().lower().startswith("json"):
            t = t.lstrip()[4:]
    a, b = t.find("{"), t.rfind("}")
    if a != -1 and b != -1 and b > a:
        t = t[a : b + 1]
    return json.loads(t)


_VERDICTS = {"ADEQUATE", "GAPS_FOUND", "INSUFFICIENT_EVIDENCE"}
_CONFIDENCES = {"low", "medium", "high"}
_SEVERITIES = {"error", "warning", "info"}
_REF_KINDS = {"entity", "relation", "file"}


def _coerce_ref_kind(ref: str) -> str:
    r = ref or ""
    if "->" in r:
        return "relation"
    if ":" in r or r.upper().endswith((".CBL", ".JCL", ".PRC", ".CPY", ".DCL", ".COB")):
        return "file"
    return "entity"


def _normalize_llm_assessment(parsed: Any) -> dict[str, Any]:
    """Coerce a raw LLM object into the strict AiAssessmentResponse shape.

    LLMs routinely drift from the schema (bare-string evidence_refs, unknown enum
    values, missing fields). Normalising here keeps the endpoint from 500-ing on
    reasonable-but-imperfect output; the surfaced error would otherwise be a raw
    pydantic validation dump.
    """
    if not isinstance(parsed, dict):
        parsed = {}

    verdict = str(parsed.get("overall_verdict", "")).upper().replace(" ", "_")
    if verdict not in _VERDICTS:
        verdict = "INSUFFICIENT_EVIDENCE"

    confidence = str(parsed.get("confidence", "")).lower()
    if confidence not in _CONFIDENCES:
        confidence = "low"

    concerns_out: list[dict[str, Any]] = []
    for c in parsed.get("concerns", []) or []:
        if not isinstance(c, dict):
            continue
        sev = str(c.get("severity", "")).lower()
        if sev not in _SEVERITIES:
            sev = "info"
        refs_out: list[dict[str, str]] = []
        for r in c.get("evidence_refs", []) or []:
            if isinstance(r, str):
                refs_out.append({"kind": _coerce_ref_kind(r), "ref": r})
            elif isinstance(r, dict):
                ref_val = str(r.get("ref", "") or "")
                kind = str(r.get("kind", "")).lower()
                if kind not in _REF_KINDS:
                    kind = _coerce_ref_kind(ref_val)
                refs_out.append({"kind": kind, "ref": ref_val})
        concerns_out.append({
            "severity": sev,
            "title": str(c.get("title", "") or ""),
            "detail": str(c.get("detail", "") or ""),
            "evidence_refs": refs_out,
            "recommendation": str(c.get("recommendation", "") or ""),
        })

    caveats = [str(x) for x in (parsed.get("caveats", []) or []) if x is not None]

    return {
        "overall_verdict": verdict,
        "confidence": confidence,
        "completeness_note": str(parsed.get("completeness_note", "") or ""),
        "correctness_note": str(parsed.get("correctness_note", "") or ""),
        "concerns": concerns_out,
        "caveats": caveats,
    }


def _build_evidence_payload(
    entity_res: DocCodeCompareResponse, relation_res: DocCodeRelationCompareResponse
) -> dict[str, Any]:
    per_type_evidence = []
    for pt in entity_res.per_type:
        # Use the canonical namespace key (e.g. "dd/CUSTFILE", "program/X") so the LLM's
        # evidence_refs resolve back to the Panel A rows keyed by the same value.
        undoc_keys = [x.get("key", "") for x in pt.undocumented]
        missing_keys = [x.get("key", "") for x in pt.missing]
        unknown_keys = [x.get("key", "") for x in pt.unknown]

        per_type_evidence.append({
            "type": pt.type,
            "doc_count": pt.doc_count,
            "code_count": pt.code_count,
            "matched": pt.matched,
            "undocumented_keys": undoc_keys[:50],
            "undocumented_truncated": max(0, len(undoc_keys) - 50),
            "missing_keys": missing_keys[:50],
            "missing_truncated": max(0, len(missing_keys) - 50),
            "unknown_keys": unknown_keys[:50],
            "unknown_truncated": max(0, len(unknown_keys) - 50),
        })

    per_pred_evidence = []
    for pr in relation_res.per_predicate:
        details_summary = []
        for d in pr.details:
            details_summary.append({
                "subject_key": d.subject_key,
                "object_key": d.object_key,
                "endpoint_verdict": d.endpoint_verdict,
                "multiplicity_verdict": d.multiplicity_verdict,
                "doc_count": d.doc_count,
                "code_count": d.code_count,
                "eligibility": d.eligibility,
                "reason": d.reason,
            })
        per_pred_evidence.append({
            "predicate": pr.predicate,
            "matched": pr.matched,
            "doc_only": pr.doc_only,
            "code_only": pr.code_only,
            "unknown": pr.unknown,
            "details": details_summary[:50],
            "details_truncated": max(0, len(details_summary) - 50),
        })

    return {
        "cluster_id": entity_res.cluster_id,
        "snapshot_id": entity_res.snapshot_id,
        "eligibility": {
            "authoritative": entity_res.eligibility.authoritative,
            "reason": entity_res.eligibility.reason,
        },
        "entity_summary": {
            "matched": entity_res.summary.matched,
            "undocumented": entity_res.summary.undocumented,
            "missing": entity_res.summary.missing,
            "unknown": entity_res.summary.unknown,
        },
        "per_type": per_type_evidence,
        "relation_summary": {
            "matched": relation_res.summary.matched,
            "doc_only": relation_res.summary.doc_only,
            "code_only": relation_res.summary.code_only,
            "unknown": relation_res.summary.unknown,
        },
        "per_predicate": per_pred_evidence,
        "not_assessed_entities": entity_res.not_assessed,
        "not_assessed_relations": relation_res.not_assessed,
    }


def _compute_evidence_hash(evidence_payload: dict[str, Any]) -> str:
    canonical = json.dumps(evidence_payload, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


AI_ASSESSMENT_SYSTEM_PROMPT = """You are an expert mainframes software documentation validation auditor.
Your job is to judge whether the technical documentation (BD/DD documents) is adequate, accurate, and complete with respect to the actual implementation code, using ONLY the provided deterministic comparison evidence.

STRICT AUDIT RULES:
1. Judge ONLY from the provided evidence payload. NEVER invent entities, relations, counts, or file paths not present in the evidence.
2. Every concern in the "concerns" list MUST cite at least one valid evidence reference in "evidence_refs" pointing to an entity key (e.g. "dd/CUSTFILE"), a relation key (e.g. "calls program/CBSTM03A -> program/CBSTM03B"), or a file path (e.g. "app/cbl/CBSTM03A.CBL:44").
3. If `eligibility.authoritative` is false, you MUST set confidence to at most "medium" and add a caveat explaining that the source parse was non-authoritative.
4. Provide structured output adhering strictly to the JSON schema below.

JSON OUTPUT SCHEMA:
{
  "overall_verdict": "ADEQUATE" | "GAPS_FOUND" | "INSUFFICIENT_EVIDENCE",
  "confidence": "low" | "medium" | "high",
  "completeness_note": "1-3 sentences grounded in Panel A numbers",
  "correctness_note": "1-3 sentences grounded in Panel B relation verdicts",
  "concerns": [
    {
      "severity": "error" | "warning" | "info",
      "title": "short concern title",
      "detail": "explanation of concern grounded in evidence",
      "evidence_refs": [
        {
          "kind": "entity" | "relation" | "file",
          "ref": "string key from evidence"
        }
      ],
      "recommendation": "suggested action to fix documentation or code link"
    }
  ],
  "caveats": ["string list of caveats, e.g. non-authoritative parse warnings"]
}
"""


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

        # BLOCKER 2 FIX: Resolve expected scope source files independently of fact emission
        expected_scope_files: set[str] = set()

        async with db.execute(
            "SELECT DISTINCT rel_path, name FROM source_facts WHERE snapshot_id=?",
            (snapshot_id,),
        ) as cur:
            sf_rows = [dict(r) for r in await cur.fetchall()]

        for r in sf_rows:
            pname = (r.get("name") or "").strip().upper()
            if pname in scope_programs or pname in scope_jobs:
                expected_scope_files.add(r["rel_path"])

        async with db.execute(
            "SELECT rel_path FROM manifest_files WHERE snapshot_id=?",
            (snapshot_id,),
        ) as cur:
            mf_rows = [dict(r) for r in await cur.fetchall()]

        for r in mf_rows:
            rp = r["rel_path"]
            base_name = rp.rsplit("/", 1)[-1].rsplit(".", 1)[0].upper()
            if base_name in scope_programs or base_name in scope_jobs:
                expected_scope_files.add(rp)

        all_expected_files = expected_scope_files | scope_rel_paths

        # Organize doc nodes by type
        doc_by_type: dict[str, dict[str, dict[str, Any]]] = {
            t: {} for t in ["program", "job", "step", "dd", "dataset"]
        }
        for node in doc_nodes:
            t = node["node_type"]
            if t in doc_by_type:
                doc_by_type[t][node["id"]] = node

        # 3. Check Eligibility Gate for ALL expected scope files
        async with db.execute(
            "SELECT rel_path, status FROM source_parse_diagnostics WHERE snapshot_id=?",
            (snapshot_id,),
        ) as cur:
            diag_rows = [dict(r) for r in await cur.fetchall()]

        diag_map = {r["rel_path"]: r["status"] for r in diag_rows}
        non_ok_scope_files = [p for p in all_expected_files if diag_map.get(p) != "ok"]

        authoritative = len(all_expected_files) > 0 and len(non_ok_scope_files) == 0
        if len(all_expected_files) == 0:
            eligibility_reason = "No source files found in snapshot matching cluster scope"
        elif authoritative:
            eligibility_reason = "All source files in cluster scope parsed with status ok"
        else:
            eligibility_reason = (
                f"Source parse not authoritative ({len(non_ok_scope_files)} expected scope file(s) "
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

    async def _compute_relation_compare(
        self, cluster_id: str, snapshot_id: str
    ) -> DocCodeRelationCompareResponse:
        """Pure relation comparison calculation without DB mutation."""
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
            SELECT id, side, predicate, subject, object, value,
                   qualifiers, doc_id, doc_span, source_span
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

        # Build subject_rel_path_map
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

        # Group doc assertions by (side, predicate, subject, object)
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

            if pred == "binds_dd" and subj.startswith("dd/") and obj.startswith("dataset/"):
                continue

            doc_items = doc_rel_map.get((side, pred, subj, obj), []) if side else []
            code_items = code_rel_map.get((pred, subj, obj), [])

            doc_count = len(doc_items)
            code_count = len(code_items)

            if code_items:
                sub_rel_path = code_items[0].rel_path
            else:
                sub_rel_path = subject_rel_path_map.get(subj)

            is_auth = (
                sub_rel_path is not None and diag_map.get(sub_rel_path) == "ok"
            )

            if doc_items and code_items:
                endpoint_verdict = "MATCH"
            elif doc_items and not code_items:
                endpoint_verdict = "DOC_ONLY" if is_auth else "UNKNOWN"
            elif code_items and not doc_items:
                endpoint_verdict = "UNKNOWN"
            else:
                endpoint_verdict = "UNKNOWN"

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
                                token = str(s).strip().split()[0]
                                m = re.search(r"L?0*(\d+)", token)
                                if m:
                                    doc_lines.append(int(m.group(1)))

                code_lines = [ci.line_start for ci in code_items if ci.line_start > 0]

                if doc_lines:
                    doc_count = len(doc_lines)
                    if code_lines:
                        if sorted(doc_lines) == sorted(code_lines):
                            multiplicity_verdict = "EXACT_SITE_MATCH"
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

        per_predicate_list: list[PredicateRelationResult] = []
        for p in ["calls", "copies", "runs", "binds_dd"]:
            details = pred_results_map[p]
            p_matched = sum(1 for d in details if d.endpoint_verdict == "MATCH")
            p_doc_only = sum(1 for d in details if d.endpoint_verdict == "DOC_ONLY")
            p_code_only = sum(1 for d in details if d.endpoint_verdict == "CODE_ONLY")
            p_unknown = sum(1 for d in details if d.endpoint_verdict == "UNKNOWN")

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

    async def compare_relations(
        self, cluster_id: str, snapshot_id: str
    ) -> DocCodeRelationCompareResponse:
        """Compare structural relationships (calls, copies, runs, binds_dd) between Doc and Code and persist."""
        db = get_db()
        response = await self._compute_relation_compare(cluster_id, snapshot_id)
        if response.status == "STALE_INPUT":
            return response

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

        for pr in response.per_predicate:
            for d in pr.details:
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
                        pr.predicate,
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

        await db.commit()
        return response

    async def assess(
        self, cluster_id: str, snapshot_id: str, provider_id: str | None = None
    ) -> AiAssessmentResponse:
        db = get_db()
        entity_res = await self.compare(cluster_id, snapshot_id)
        relation_res = await self.compare_relations(cluster_id, snapshot_id)

        evidence = _build_evidence_payload(entity_res, relation_res)
        ev_hash = _compute_evidence_hash(evidence)

        # Resolve provider
        from domain.model_connector.service import ProviderConfigService
        from domain.model_connector.types import ChatMessage, ChatRequest

        provider_svc = ProviderConfigService()
        prov_id: str | None = None
        model_name = "default-llm"

        if provider_id:
            try:
                cfg = await provider_svc.get_by_id(provider_id)
                prov_id = cfg.id
                model_name = cfg.model_id
            except Exception:
                pass
        else:
            configs = await provider_svc.list_all()
            chat_cfg = next((c for c in configs if not c.capabilities.embeddings), None)
            if chat_cfg:
                prov_id = chat_cfg.id
                model_name = chat_cfg.model_id

        # Check cache
        async with db.execute(
            """
            SELECT assessment_json, created_at, model
            FROM doc_code_assessments
            WHERE cluster_id=? AND snapshot_id=? AND evidence_hash=? AND model=?
            """,
            (cluster_id, snapshot_id, ev_hash, model_name),
        ) as cur:
            row = await cur.fetchone()

        if row is not None:
            data = json.loads(row["assessment_json"])
            return AiAssessmentResponse(
                cluster_id=cluster_id,
                snapshot_id=snapshot_id,
                overall_verdict=data.get("overall_verdict", "INSUFFICIENT_EVIDENCE"),
                confidence=data.get("confidence", "low"),
                completeness_note=data.get("completeness_note", ""),
                correctness_note=data.get("correctness_note", ""),
                concerns=[AiConcern(**c) for c in data.get("concerns", [])],
                caveats=data.get("caveats", []),
                model=row["model"],
                generated_at=row["created_at"],
                from_cache=True,
                stale=False,
            )

        # Call LLM
        user_prompt = f"EVIDENCE PAYLOAD:\n{json.dumps(evidence, indent=2)}"
        chat_req = ChatRequest(
            provider_id=prov_id or "default",
            messages=[
                ChatMessage(role="system", content=AI_ASSESSMENT_SYSTEM_PROMPT),
                ChatMessage(role="user", content=user_prompt),
            ],
            json_mode=True,
        )

        resp = await provider_svc.chat(chat_req)
        parsed = _normalize_llm_assessment(_parse_llm_json(resp.content))

        # Enforce non-authoritative caveat and confidence capping if applicable
        if not entity_res.eligibility.authoritative:
            if parsed.get("confidence") == "high":
                parsed["confidence"] = "medium"
            caveats = parsed.setdefault("caveats", [])
            caveat_msg = f"Non-authoritative parse: {entity_res.eligibility.reason}"
            if caveat_msg not in caveats:
                caveats.append(caveat_msg)

        now = utc_now_iso()
        assessment_json_str = json.dumps(parsed)

        await db.execute(
            """
            INSERT OR REPLACE INTO doc_code_assessments
            (cluster_id, snapshot_id, comparator_version, evidence_hash, model, assessment_json, created_at)
            VALUES (?, ?, '1.0.0', ?, ?, ?, ?)
            """,
            (cluster_id, snapshot_id, ev_hash, model_name, assessment_json_str, now),
        )
        await db.commit()

        return AiAssessmentResponse(
            cluster_id=cluster_id,
            snapshot_id=snapshot_id,
            overall_verdict=parsed.get("overall_verdict", "INSUFFICIENT_EVIDENCE"),
            confidence=parsed.get("confidence", "low"),
            completeness_note=parsed.get("completeness_note", ""),
            correctness_note=parsed.get("correctness_note", ""),
            concerns=[AiConcern(**c) for c in parsed.get("concerns", [])],
            caveats=parsed.get("caveats", []),
            model=model_name,
            generated_at=now,
            from_cache=False,
            stale=False,
        )

    async def get_latest_assessment(
        self, cluster_id: str, snapshot_id: str
    ) -> AiAssessmentResponse | None:
        db = get_db()
        async with db.execute(
            """
            SELECT evidence_hash, model, assessment_json, created_at
            FROM doc_code_assessments
            WHERE cluster_id=? AND snapshot_id=?
            ORDER BY id DESC LIMIT 1
            """,
            (cluster_id, snapshot_id),
        ) as cur:
            row = await cur.fetchone()

        if row is None:
            return None

        # Check if stale compared to live evidence
        entity_res = await self.compare(cluster_id, snapshot_id)
        relation_res = await self.compare_relations(cluster_id, snapshot_id)
        live_evidence = _build_evidence_payload(entity_res, relation_res)
        live_hash = _compute_evidence_hash(live_evidence)

        stale = row["evidence_hash"] != live_hash
        data = json.loads(row["assessment_json"])

        return AiAssessmentResponse(
            cluster_id=cluster_id,
            snapshot_id=snapshot_id,
            overall_verdict=data.get("overall_verdict", "INSUFFICIENT_EVIDENCE"),
            confidence=data.get("confidence", "low"),
            completeness_note=data.get("completeness_note", ""),
            correctness_note=data.get("correctness_note", ""),
            concerns=[AiConcern(**c) for c in data.get("concerns", [])],
            caveats=data.get("caveats", []),
            model=row["model"],
            generated_at=row["created_at"],
            from_cache=True,
            stale=stale,
        )

    async def linked_graph(
        self,
        cluster_id: str,
        snapshot_id: str,
        layers: str = "bd,dd,code",
        scope: str | None = None,
    ) -> LinkedGraphResponse:
        db = get_db()
        layer_set = {l.strip().lower() for l in layers.split(",") if l.strip()}

        # 1. Fetch doc graph nodes and edges
        async with db.execute(
            """
            SELECT id, node_type, display_name, attributes, provenance
            FROM doc_graph_nodes WHERE cluster_id=?
            """,
            (cluster_id,),
        ) as cur:
            doc_nodes = [dict(r) for r in await cur.fetchall()]

        async with db.execute(
            """
            SELECT id, src_node_id, dst_node_id, edge_type, attributes
            FROM doc_graph_edges WHERE cluster_id=?
            """,
            (cluster_id,),
        ) as cur:
            doc_edges = [dict(r) for r in await cur.fetchall()]

        # Fetch doc_graph_assertions to map sides, status, confidence
        async with db.execute(
            """
            SELECT id, side, predicate, subject, object, status, confidence, doc_id, doc_span, source_span
            FROM doc_graph_assertions WHERE cluster_id=?
            """,
            (cluster_id,),
        ) as cur:
            doc_assertions = [dict(r) for r in await cur.fetchall()]

        # Map doc_id -> doc_kind to derive in_bd / in_dd per node
        doc_kind_map: dict[str, str] = {}
        for n in doc_nodes:
            if n["node_type"] == "doc":
                attrs = n.get("attributes") or {}
                if isinstance(attrs, str):
                    try:
                        attrs = json.loads(attrs)
                    except Exception:
                        attrs = {}
                doc_kind_map[n["id"]] = attrs.get("doc_kind", "").lower()

        # Build node provenance map & side membership
        node_side_map: dict[str, set[str]] = {}
        for n in doc_nodes:
            nid = n["id"]
            prov = n.get("provenance") or []
            if isinstance(prov, str):
                try:
                    prov = json.loads(prov)
                except Exception:
                    prov = []
            sides: set[str] = set()
            for p in prov:
                if isinstance(p, dict) and "doc_id" in p:
                    dk = doc_kind_map.get(p["doc_id"], "")
                    if dk == "bd":
                        sides.add("bd")
                    elif dk in ("dd_cobol", "dd_jcl"):
                        sides.add("dd")
            node_side_map[nid] = sides

        # Map assertions by (predicate, subject, object) for edge side membership
        assertion_side_map: dict[tuple[str, str, str], set[str]] = {}
        assertion_status_map: dict[tuple[str, str, str], str] = {}
        assertion_conf_map: dict[tuple[str, str, str], str] = {}
        assertion_evidence_map: dict[tuple[str, str, str], list[dict[str, Any]]] = {}

        for a in doc_assertions:
            key = (a["predicate"], a["subject"], a["object"] or "")
            side = (a.get("side") or "").lower()
            assertion_side_map.setdefault(key, set()).add(side)
            if a.get("status"):
                assertion_status_map[key] = a["status"]
            if a.get("confidence"):
                assertion_conf_map[key] = a["confidence"]

            ev_item = {}
            if a.get("doc_id"):
                ev_item["doc_id"] = a["doc_id"]
            if a.get("doc_span"):
                ev_item["doc_span"] = a["doc_span"]
            if a.get("source_span"):
                ev_item["source_span"] = a["source_span"]
            if ev_item:
                assertion_evidence_map.setdefault(key, []).append(ev_item)

        # 2. Fetch code facts & diagnostics
        async with db.execute(
            """
            SELECT fact_type, semantic_key, parent_key, rel_path, line_start, line_end, name, value
            FROM source_facts WHERE snapshot_id=?
            """,
            (snapshot_id,),
        ) as cur:
            code_facts = [dict(r) for r in await cur.fetchall()]

        async with db.execute(
            "SELECT rel_path, status FROM source_parse_diagnostics WHERE snapshot_id=?",
            (snapshot_id,),
        ) as cur:
            diag_rows = [dict(r) for r in await cur.fetchall()]
        diag_map = {r["rel_path"]: r["status"] for r in diag_rows}

        async with db.execute(
            "SELECT rel_path FROM manifest_files WHERE snapshot_id=?",
            (snapshot_id,),
        ) as cur:
            mf_rows = [dict(r) for r in await cur.fetchall()]

        # Execute pure comparison cores
        entity_res = await self.compare(cluster_id, snapshot_id)
        relation_res = await self._compute_relation_compare(cluster_id, snapshot_id)

        # Build candidate lookup for cross-links (doc_id -> candidate code_rel_paths)
        code_file_by_name: dict[str, list[str]] = {}
        for sf in code_facts:
            name = (sf.get("name") or "").strip().upper()
            if name:
                code_file_by_name.setdefault(name, []).append(sf["rel_path"])
        for mf in mf_rows:
            rp = mf["rel_path"]
            bname = rp.rsplit("/", 1)[-1].rsplit(".", 1)[0].upper()
            if bname:
                code_file_by_name.setdefault(bname, []).append(rp)
        # Deduplicate candidate lists
        code_file_by_name = {k: sorted(list(set(v))) for k, v in code_file_by_name.items()}

        code_fact_by_skey = {sf["semantic_key"]: sf["rel_path"] for sf in code_facts}

        # Build Nodes
        nodes_out: list[LinkedGraphNode] = []
        cross_links_out: list[CrossLinkItem] = []
        assessed_node_types = {"program", "job", "step", "dd", "dataset"}

        for dn in doc_nodes:
            nid = dn["id"]
            ntype = dn["node_type"]
            dname = dn["display_name"]
            sides = node_side_map.get(nid, set())

            in_bd = "bd" in sides or ("dd" not in sides and len(sides) == 0)
            in_dd = "dd" in sides

            # Filter layers if requested
            if "bd" not in layer_set and in_bd and not in_dd:
                continue
            if "dd" not in layer_set and in_dd and not in_bd:
                continue

            # Cross-link resolution
            exact_path = code_fact_by_skey.get(nid)
            match_method = "exact_key"
            candidates: list[str] = []
            code_rel_path: str | None = None
            name_fallback_used = False

            if exact_path:
                code_rel_path = exact_path
                candidates = [exact_path]
            else:
                dname_upper = dname.strip().upper()
                cands = code_file_by_name.get(dname_upper, [])
                if cands:
                    match_method = "name_fallback"
                    name_fallback_used = True
                    code_rel_path = cands[0]
                    candidates = cands

            in_code = code_rel_path is not None
            entity_verdict = "matched" if in_code else ("missing" if entity_res.eligibility.authoritative else "unknown")

            prov_list = dn.get("provenance") or []
            if isinstance(prov_list, str):
                try:
                    prov_list = json.loads(prov_list)
                except Exception:
                    prov_list = []

            attrs = dn.get("attributes") or {}
            if isinstance(attrs, str):
                try:
                    attrs = json.loads(attrs)
                except Exception:
                    attrs = {}

            nodes_out.append(
                LinkedGraphNode(
                    id=nid,
                    node_type=ntype,
                    display_name=dname,
                    in_bd=in_bd,
                    in_dd=in_dd,
                    in_code=in_code,
                    code_rel_path=code_rel_path,
                    name_fallback_used=name_fallback_used,
                    entity_verdict=entity_verdict,
                    assessed=(ntype in assessed_node_types),
                    provenance=prov_list,
                    attributes=attrs if isinstance(attrs, dict) else {},
                )
            )

            if code_rel_path:
                verdict_val = "ambiguous" if len(candidates) > 1 else "matched"
                cross_links_out.append(
                    CrossLinkItem(
                        doc_id=nid,
                        code_rel_path=code_rel_path,
                        match_method=match_method,
                        candidates=candidates,
                        verdict=verdict_val,
                    )
                )

        # Surface code-only entities the docs never mention (undocumented) so a matcher
        # miss on the code side is visible — only when the code layer is requested.
        if "code" in layer_set:
            for tr in entity_res.per_type:
                for u in tr.undocumented:
                    nodes_out.append(
                        LinkedGraphNode(
                            id=u["key"],
                            node_type=tr.type,
                            display_name=u.get("name") or u["key"],
                            in_bd=False,
                            in_dd=False,
                            in_code=True,
                            code_rel_path=u.get("rel_path"),
                            name_fallback_used=False,
                            entity_verdict="undocumented",
                            assessed=True,
                            provenance=[],
                        )
                    )

        # Build Edges
        edges_out: list[LinkedGraphEdge] = []
        detail_map: dict[tuple[str, str, str], RelationComparisonDetail] = {}
        for pr in relation_res.per_predicate:
            for d in pr.details:
                detail_map[(pr.predicate, d.subject_key, d.object_key)] = d

        # Map a projected edge_type back to its assertion predicate (see the projection
        # in doc_graph/_graph_model.py). Several edge_types share one predicate.
        etype_to_pred = {
            "program_calls_program": "calls",
            "program_copies_copybook": "copies",
            "job_executes_step": "runs",
            "step_runs_program": "runs",
            "step_binds_dd": "binds_dd",
            "dd_binds_dataset": "binds_dd",
            "accesses_dataset": "accesses",
            "cites": "cites",
            "field_width_relation": "field_width_relation",
            "references": "references",
            "rule_about": "rule_about",
            "defines": "defines",
        }

        for de in doc_edges:
            ekey = f"{de['edge_type']}:{de['src_node_id']}->{de['dst_node_id']}"
            src = de["src_node_id"]
            dst = de["dst_node_id"]
            etype = de["edge_type"]

            pred = etype_to_pred.get(etype, etype)

            sides = assertion_side_map.get((pred, src, dst), set())
            if not sides:
                # Annotation/linking edges (defines, rule_about, ...) are not backed by a
                # single-side assertion — inherit layer membership from their endpoints.
                sides = node_side_map.get(src, set()) | node_side_map.get(dst, set())
            in_bd = "bd" in sides
            in_dd = "dd" in sides

            detail = detail_map.get((pred, src, dst))
            ep_verdict = detail.endpoint_verdict if detail else ("DOC_ONLY" if (in_bd or in_dd) else "UNKNOWN")
            mult_verdict = detail.multiplicity_verdict if detail else "NOT_APPLICABLE"
            doc_cnt = detail.doc_count if detail else 1
            code_cnt = detail.code_count if detail else 0
            cnt_differs = doc_cnt != code_cnt

            status_val = assertion_status_map.get((pred, src, dst), "asserted")
            conf_val = assertion_conf_map.get((pred, src, dst), "corroborating")
            ev_list = assertion_evidence_map.get((pred, src, dst), [])

            src_node = next((n for n in nodes_out if n.id == src), None)
            dst_node = next((n for n in nodes_out if n.id == dst), None)

            src_file = src_node.code_rel_path if src_node else None
            dst_file = dst_node.code_rel_path if dst_node else None

            subj_ok = (diag_map.get(src_file) == "ok") if src_file else True
            obj_ok = (diag_map.get(dst_file) == "ok") if dst_file else True

            doc_k = src if ep_verdict != "MATCH" else None
            code_k = dst if ep_verdict != "MATCH" else None

            edges_out.append(
                LinkedGraphEdge(
                    edge_key=ekey,
                    src=src,
                    dst=dst,
                    edge_type=etype,
                    layer="doc",
                    in_bd=in_bd,
                    in_dd=in_dd,
                    endpoint_verdict=ep_verdict,
                    multiplicity_verdict=mult_verdict,
                    doc_count=doc_cnt,
                    code_count=code_cnt,
                    count_differs=cnt_differs,
                    assessed=detail is not None,
                    status=status_val,
                    subject_ok=subj_ok,
                    object_ok=obj_ok,
                    doc_key=doc_k,
                    code_key=code_k,
                    confidence=conf_val,
                    evidence=ev_list,
                    symbol_edges=[],
                )
            )

        # Build Not Assessed Coverage Counts
        unassessed_entity_counts: dict[str, int] = {}
        for dn in doc_nodes:
            ntype = dn["node_type"]
            if ntype not in assessed_node_types:
                unassessed_entity_counts[ntype] = unassessed_entity_counts.get(ntype, 0) + 1

        not_assessed_entities = [
            NotAssessedEntityCoverage(node_type=k, count=v) for k, v in sorted(unassessed_entity_counts.items())
        ]

        unassessed_rel_count = 0
        for a in doc_assertions:
            if a["predicate"] == "binds_dd" and (a["subject"] or "").startswith("dd/") and (a["object"] or "").startswith("dataset/"):
                unassessed_rel_count += 1

        not_assessed_relations = [
            NotAssessedRelationCoverage(
                edge_type="dd_binds_dataset",
                exists=unassessed_rel_count,
                assessed=0,
            )
        ]

        # Build BD groups: group BD-side assertions by (doc_id, section_id). A group's
        # members are the DD nodes the BD section wraps — so the hull covers real DD flows,
        # not BD-only entities (Ticket 2 §3).
        dd_node_ids = {n.id for n in nodes_out if n.in_dd}

        bd_group_map: dict[tuple[str, str], set[str]] = {}
        bd_group_ev_map: dict[tuple[str, str], list[dict[str, Any]]] = {}

        for a in doc_assertions:
            side = (a.get("side") or "").lower()
            if side == "bd":
                doc_id = a.get("doc_id") or "bd_doc"
                sec_id = "general"
                doc_span = a.get("doc_span")
                if isinstance(doc_span, str):
                    try:
                        doc_span = json.loads(doc_span)
                    except Exception:
                        doc_span = {}
                if isinstance(doc_span, dict) and doc_span.get("section_id"):
                    sec_id = str(doc_span["section_id"])

                group_key = (doc_id, sec_id)
                subj = a.get("subject")
                obj = a.get("object")

                if subj and subj in dd_node_ids:
                    bd_group_map.setdefault(group_key, set()).add(subj)
                if obj and obj in dd_node_ids:
                    bd_group_map.setdefault(group_key, set()).add(obj)

                ev_entry = {"doc_id": doc_id, "doc_span": doc_span}
                bd_group_ev_map.setdefault(group_key, []).append(ev_entry)

        if not bd_group_map:
            for dn in doc_nodes:
                nid = dn["id"]
                sides = node_side_map.get(nid, set())
                if "bd" in sides:
                    bd_group_map.setdefault(("bd_main", "1.0"), set()).add(nid)

        bd_groups_out: list[BdGroupItem] = []
        for (doc_id, sec_id), members in bd_group_map.items():
            if not members:
                continue
            gid = f"bd-group:{doc_id}#{sec_id}"
            lbl = f"BD Section {sec_id}" if sec_id != "general" else f"BD Section ({doc_id})"
            bd_groups_out.append(
                BdGroupItem(
                    group_id=gid,
                    bd_doc_id=doc_id,
                    section_id=sec_id,
                    label=lbl,
                    member_dd_ids=sorted(list(members)),
                    derivation="bd_section",
                    evidence=bd_group_ev_map.get((doc_id, sec_id), []),
                )
            )

        # Scope: restrict to the seed node + its 1-hop neighborhood (perf for tickets 2/3).
        if scope:
            keep = {scope}
            for e in edges_out:
                if e.src == scope:
                    keep.add(e.dst)
                if e.dst == scope:
                    keep.add(e.src)
            nodes_out = [n for n in nodes_out if n.id in keep]
            edges_out = [e for e in edges_out if e.src in keep and e.dst in keep]
            cross_links_out = [c for c in cross_links_out if c.doc_id in keep]

        return LinkedGraphResponse(
            cluster_id=cluster_id,
            snapshot_id=snapshot_id,
            eligibility=entity_res.eligibility,
            nodes=nodes_out,
            edges=edges_out,
            cross_links=cross_links_out,
            bd_groups=bd_groups_out,
            not_assessed=NotAssessedCoverage(
                entities=not_assessed_entities,
                relations=not_assessed_relations,
            ),
        )
