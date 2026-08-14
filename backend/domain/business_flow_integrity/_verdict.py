"""LLM verdict engine and calibration gate for Phase 3 Business Flow Integrity."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from shared.logger import logger
from shared.utils import utc_now_iso

from ..doc_graph._llm_citation import _parse_llm_json
from ..model_connector.types import ChatMessage, ChatRequest
from ._llm import call_with_reasoning_ladder

LLM_BATCH_SIZE = 5

FlowVerdict = Literal[
    "MATCH", "PARTIAL", "BROKEN", "DOC_ONLY", "CODE_ONLY", "UNKNOWN", "RECOVERY_GAP", "GRAPH_GAP"
]
AiBucket = Literal["fabricated", "silent_omission", "over_generalized", "stale_missing"]

_VALUE_COMPARISON_RE = re.compile(
    r"\b(numeric|value|\d+)\s*(equality|inequality|mismatch|is\s+different|!=|==)", re.IGNORECASE
)

_BATCH_SYSTEM_PROMPT = (
    "You are a deterministic business flow integrity evaluator. Your job is to judge whether "
    "each Basic Design (BD) flow unit is a FAITHFUL ABSTRACTION of the code route at ROUTE altitude — "
    "evaluating execution order, branch guard class (e.g. success vs error), and hand-off consistency. "
    "Do NOT evaluate numeric values, record counts, or coverage completeness. "
    "Output ONLY a valid JSON object matching the required schema: {\"results\": [ {\"unit_id\": \"...\", \"verdict\": \"...\", \"guard_verdict\": \"CLASS_MATCH\"|\"CLASS_MISMATCH\"|null, \"ai_bucket\": \"...\"|null, \"reason\": \"...\", \"evidence\": {\"bd_line_start\": null, \"bd_line_end\": null, \"code_rel_path\": null, \"code_line\": null}} ]}.\n\n"
    "Allowed verdicts: MATCH, PARTIAL, BROKEN, DOC_ONLY, CODE_ONLY, UNKNOWN, RECOVERY_GAP, GRAPH_GAP\n"
    "Allowed AI buckets: fabricated, silent_omission, over_generalized, stale_missing\n"
    "Keep each reason under 20 words. Return one result object per input unit_id.\n"
)


class LLMVerdictEvidence(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    bd_line_start: int | None = None
    bd_line_end: int | None = None
    code_rel_path: str | None = None
    code_line: int | None = None


class LLMUnitVerdictResponse(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    unit_id: str
    verdict: FlowVerdict
    guard_verdict: str | None = None
    ai_bucket: AiBucket | None = None
    reason: str
    evidence: LLMVerdictEvidence | None = None


class LLMBatchVerdictResponse(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    results: list[LLMUnitVerdictResponse]


@dataclass(frozen=True)
class VerdictRecord:
    id: str
    cluster_id: str
    snapshot_id: str
    bd_edge_id: str | None
    code_subpath_json: str
    verdict: FlowVerdict
    guard_verdict: str | None
    ai_bucket: AiBucket | None
    reason: str
    evidence_json: str
    comparator_version: int = 1


@dataclass(frozen=True)
class VerdictResult:
    cluster_id: str
    snapshot_id: str
    verdicts: list[VerdictRecord]
    match_percentage: float
    total_units: int
    resolved_units: int
    match_count: int
    broken_count: int
    code_only_count: int
    unknown_count: int


def _is_value_comparison_reason(reason: str) -> bool:
    """Validator gate: rejects any reason asserting numeric value equality or mismatch."""
    return bool(_VALUE_COMPARISON_RE.search(reason))


def _enforce_unknown_dominance(verdict: FlowVerdict, is_external_or_unresolved: bool) -> FlowVerdict:
    """SPEC §10 rule: UNKNOWN dominates BROKEN when any endpoint is unresolved/external."""
    if is_external_or_unresolved and verdict == "BROKEN":
        return "UNKNOWN"
    return verdict


def _normalize_guard_class(guard_text: str | None) -> str:
    """Normalize guard condition text to a class level (SPEC §0, §5)."""
    if not guard_text:
        return "UNCONDITIONAL"
    gt = guard_text.lower()
    if any(k in gt for k in ("error", "fail", "invalid", "except", "not in")):
        return "ON_ERROR"
    if any(k in gt for k in ("success", "ok", "valid", "option =", "pf")):
        return "ON_SUCCESS"
    if any(k in gt for k in ("menu", "dispatch", "route", "select")):
        return "MENU_DISPATCH"
    return "UNCONDITIONAL"


async def _evaluate_llm_batch(
    batch_units: list[dict[str, Any]], provider_id: str, semaphore: asyncio.Semaphore
) -> dict[str, LLMUnitVerdictResponse]:
    """Execute a batched LLM pass over up to 10 flow units concurrently."""
    async with semaphore:
        prompt_payload = [
            {
                "unit_id": u["unit_id"],
                "bd_claim": u["bd_claim"],
                "code_fact": u["code_fact"],
                "base_verdict": u["base_verdict"],
            }
            for u in batch_units
        ]

        def _build_req(effort: str) -> ChatRequest:
            return ChatRequest(
                provider_id=provider_id,
                messages=[
                    ChatMessage(role="system", content=_BATCH_SYSTEM_PROMPT),
                    ChatMessage(role="user", content=json.dumps(prompt_payload, indent=2)),
                ],
                stream=True,
                max_completion_tokens=50000,
                temperature=0.0,
                json_mode=True,
                reasoning_effort=effort,
            )

        def _parse(text: str) -> dict[str, LLMUnitVerdictResponse]:
            parsed_json = _parse_llm_json(text)
            validated = LLMBatchVerdictResponse.model_validate(parsed_json)
            return {r.unit_id: r for r in validated.results}

        # Judgment task (verdict decision) — open at high reasoning, one low-effort retry on failure.
        ladder_res = await call_with_reasoning_ladder(_build_req, _parse, first_effort="high", label="BFI verdict batch")
        return ladder_res[0] if ladder_res is not None else {}


async def run_flow_verdicts(
    db: Any, cluster_id: str, snapshot_id: str, provider_id: str | None = None
) -> VerdictResult:
    """Run flow verdict comparison over BD flow edges and code routes for cluster and snapshot."""
    # 1. Fetch BD flow nodes & edges
    async with db.execute(
        "SELECT id, node_kind, local_id, binding, binding_type, label, guard_text, doc_line_start, doc_line_end FROM bd_flow_nodes WHERE cluster_id=?",
        (cluster_id,),
    ) as cur:
        bd_nodes = {r["id"]: dict(r) for r in await cur.fetchall()}

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
        code_nodes_by_id = {r["id"]: dict(r) for r in await cur.fetchall()}

    async with db.execute(
        "SELECT id, src_node_id, dst_node_id, edge_kind, label, guard_text, rel_path FROM code_flow_edges WHERE snapshot_id=?",
        (snapshot_id,),
    ) as cur:
        code_edges = [dict(r) for r in await cur.fetchall()]

    # Build adjacency map for reachability checks
    code_adj: dict[str, list[dict[str, Any]]] = {}
    for ce in code_edges:
        code_adj.setdefault(ce["src_node_id"], []).append(ce)

    # 3. Fetch flow alignment
    async with db.execute(
        "SELECT id, bd_node_id, code_node_id, match_method, tag FROM flow_alignment WHERE cluster_id=? AND snapshot_id=?",
        (cluster_id, snapshot_id),
    ) as cur:
        align_rows = [dict(r) for r in await cur.fetchall()]

    bd_align_map: dict[str, dict[str, Any]] = {
        r["bd_node_id"]: dict(r) for r in align_rows if r.get("bd_node_id")
    }

    verdict_records: list[VerdictRecord] = []
    llm_candidate_units: list[dict[str, Any]] = []

    # 4. Evaluate BD edges
    for bd_edge in bd_edges:
        bd_edge_id = bd_edge["id"]
        src_bd = bd_nodes.get(bd_edge["src_node_id"], {})
        dst_bd = bd_nodes.get(bd_edge["dst_node_id"], {})

        src_align = bd_align_map.get(bd_edge["src_node_id"], {})
        dst_align = bd_align_map.get(bd_edge["dst_node_id"], {})

        src_code_id = src_align.get("code_node_id")
        dst_code_id = dst_align.get("code_node_id")

        is_ext_or_unresolved = (
            src_align.get("tag") == "UNKNOWN"
            or dst_align.get("tag") == "UNKNOWN"
            or src_align.get("match_method") in ("external_target", "unresolved")
            or dst_align.get("match_method") in ("external_target", "unresolved")
        )

        base_verdict: FlowVerdict = "UNKNOWN"
        guard_verdict: str | None = None
        ai_bucket: AiBucket | None = None
        reason = ""
        subpath_json = "[]"

        # Check alignment state
        if src_align.get("tag") == "DOC_CONTRADICTED" or dst_align.get("tag") == "DOC_CONTRADICTED":
            base_verdict = "BROKEN"
            ai_bucket = "stale_missing"
            contradicted_binding = (
                src_bd.get("binding") if src_align.get("tag") == "DOC_CONTRADICTED" else dst_bd.get("binding")
            )
            reason = f"BD step claims missing/unresolved asset ({contradicted_binding}), contradicted by code graph"
        elif src_code_id and dst_code_id:
            # Check code graph reachability between aligned nodes
            reachable_edges: list[dict[str, Any]] = []
            visited: set[str] = set()
            queue = [src_code_id]
            found = False

            while queue:
                curr = queue.pop(0)
                if curr == dst_code_id:
                    found = True
                    break
                if curr in visited:
                    continue
                visited.add(curr)
                for out_edge in code_adj.get(curr, []):
                    reachable_edges.append(out_edge)
                    queue.append(out_edge["dst_node_id"])

            if found:
                base_verdict = "MATCH"
                subpath_json = json.dumps([e["id"] for e in reachable_edges])
                # Check guard class match
                bd_guard_cls = _normalize_guard_class(bd_edge.get("guard_text"))
                code_guard_cls = _normalize_guard_class(
                    reachable_edges[0].get("guard_text") if reachable_edges else None
                )
                if bd_guard_cls == code_guard_cls:
                    guard_verdict = "CLASS_MATCH"
                    reason = f"Execution path and guard class ({bd_guard_cls}) preserved end-to-end"
                else:
                    guard_verdict = "CLASS_MISMATCH"
                    base_verdict = "PARTIAL"
                    reason = f"Execution path preserved but guard class mismatch: BD ({bd_guard_cls}) vs Code ({code_guard_cls})"
            else:
                base_verdict = "BROKEN"
                ai_bucket = "silent_omission"
                reason = f"No reachable code route from {src_code_id} to {dst_code_id}"
        elif is_ext_or_unresolved:
            base_verdict = "UNKNOWN"
            reason = "External target boundary prevents end-to-end code reachability check"
        else:
            base_verdict = "UNKNOWN"
            reason = "Intermediate prose step below route altitude"

        # Apply UNKNOWN dominance rule (SPEC §10)
        base_verdict = _enforce_unknown_dominance(base_verdict, is_ext_or_unresolved)

        unit_id = f"verdict:{cluster_id}:{bd_edge_id}"

        verdict_records.append(VerdictRecord(
            id=unit_id,
            cluster_id=cluster_id,
            snapshot_id=snapshot_id,
            bd_edge_id=bd_edge_id,
            code_subpath_json=subpath_json,
            verdict=base_verdict,
            guard_verdict=guard_verdict,
            ai_bucket=ai_bucket,
            reason=reason,
            evidence_json=json.dumps({
                "bd_line_start": bd_edge.get("doc_line"),
                "bd_line_end": bd_edge.get("doc_line"),
            }),
        ))

        # Filter units to send to LLM (resolved/uncertain only, skip UNKNOWN micro-steps).
        # stale_missing (DOC_CONTRADICTED) is deterministic ground truth — code demonstrably
        # resolves what BD called missing; keep it authoritative, never let the LLM reword it.
        if provider_id and base_verdict in ("MATCH", "PARTIAL", "BROKEN") and ai_bucket != "stale_missing":
            code_fact = None
            if src_code_id:
                c_node = code_nodes_by_id.get(src_code_id, {})
                code_fact = {
                    "edge_type": bd_edge.get("edge_kind"),
                    "guard_class": _normalize_guard_class(bd_edge.get("guard_text")),
                    "rel_path": c_node.get("rel_path", ""),
                }

            llm_candidate_units.append({
                "unit_id": unit_id,
                "bd_edge_id": bd_edge_id,
                "bd_claim": {
                    "label": bd_edge.get("label") or bd_edge.get("guard_text") or "Transition",
                    "guard_text": bd_edge.get("guard_text"),
                    "doc_line": bd_edge.get("doc_line"),
                },
                "code_fact": code_fact,
                "base_verdict": base_verdict,
                "base_reason": reason,
                "base_ai_bucket": ai_bucket,
            })

    # 5. Evaluate CODE_ONLY nodes/routes
    for align in align_rows:
        if align.get("tag") == "CODE_ONLY" and align.get("code_node_id"):
            cid = align["code_node_id"]
            c_node = code_nodes_by_id.get(cid, {})
            v_id = f"verdict:{cluster_id}:code_only:{cid}"
            verdict_records.append(VerdictRecord(
                id=v_id,
                cluster_id=cluster_id,
                snapshot_id=snapshot_id,
                bd_edge_id=None,
                code_subpath_json=json.dumps([cid]),
                verdict="CODE_ONLY",
                guard_verdict=None,
                ai_bucket="silent_omission",
                reason=f"Reachable code route {c_node.get('rel_path', '')} absent from BD documentation",
                evidence_json=json.dumps({"code_rel_path": c_node.get("rel_path", "")}),
            ))

    # 6. Execute Batched LLM pass if provider_id is provided and candidate units exist
    if provider_id and llm_candidate_units:
        batches = [
            llm_candidate_units[i : i + LLM_BATCH_SIZE]
            for i in range(0, len(llm_candidate_units), LLM_BATCH_SIZE)
        ]
        semaphore = asyncio.Semaphore(10)
        tasks = [_evaluate_llm_batch(b, provider_id, semaphore) for b in batches]
        batch_results_list = await asyncio.gather(*tasks, return_exceptions=True)

        llm_responses: dict[str, LLMUnitVerdictResponse] = {}
        for res in batch_results_list:
            if isinstance(res, dict):
                llm_responses.update(res)
            elif isinstance(res, Exception):
                logger.warning("BFI LLM batch task failed: %r", res)

        # Merge LLM results with override floor & value comparison safeguards
        final_verdicts: list[VerdictRecord] = []
        cand_by_id = {u["unit_id"]: u for u in llm_candidate_units}

        for rec in verdict_records:
            if rec.id in cand_by_id:
                cand = cand_by_id[rec.id]
                llm_res = llm_responses.get(rec.id)

                if llm_res:
                    # Check Validator Gate: Reject LLM output asserting numeric value comparison
                    if _is_value_comparison_reason(llm_res.reason):
                        logger.warning(f"Rejected LLM verdict for {rec.id}: asserts numeric comparison ('{llm_res.reason}')")
                        final_verdicts.append(rec)
                        continue

                    final_verdict = llm_res.verdict
                    final_bucket = llm_res.ai_bucket or cand["base_ai_bucket"]

                    # Check Override Floor: LLM MATCH cannot override a deterministic BROKEN/stale_missing
                    if cand["base_verdict"] == "BROKEN" and final_verdict == "MATCH":
                        logger.warning(f"Override floor clamped LLM MATCH to BROKEN for stale_missing unit {rec.id}")
                        final_verdict = "BROKEN"
                        final_bucket = "stale_missing"

                    final_verdicts.append(VerdictRecord(
                        id=rec.id,
                        cluster_id=rec.cluster_id,
                        snapshot_id=rec.snapshot_id,
                        bd_edge_id=rec.bd_edge_id,
                        code_subpath_json=rec.code_subpath_json,
                        verdict=final_verdict,
                        guard_verdict=llm_res.guard_verdict or rec.guard_verdict,
                        ai_bucket=final_bucket,
                        reason=llm_res.reason,
                        evidence_json=json.dumps(
                            llm_res.evidence.model_dump()
                            if llm_res.evidence
                            else {"bd_line_start": cand["bd_claim"].get("doc_line")}
                        ),
                    ))
                else:
                    # Omitted or unparseable unit: retain deterministic base verdict
                    final_verdicts.append(rec)
            else:
                final_verdicts.append(rec)

        verdict_records = final_verdicts

    # 7. Persist verdicts to database
    await db.execute(
        "DELETE FROM flow_verdicts WHERE cluster_id=? AND snapshot_id=?",
        (cluster_id, snapshot_id),
    )

    db_tuples = [
        (
            vr.id, vr.cluster_id, vr.snapshot_id, vr.bd_edge_id,
            vr.code_subpath_json, vr.verdict, vr.guard_verdict,
            vr.ai_bucket, vr.reason, vr.evidence_json, vr.comparator_version, utc_now_iso(),
        )
        for vr in verdict_records
    ]

    await db.executemany(
        "INSERT INTO flow_verdicts (id, cluster_id, snapshot_id, bd_edge_id, code_subpath_json, verdict, guard_verdict, ai_bucket, reason, evidence_json, comparator_version, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        db_tuples,
    )
    await db.commit()

    # 8. Compute Calibration Metrics
    total_units = len(verdict_records)
    match_cnt = sum(1 for v in verdict_records if v.verdict == "MATCH")
    broken_cnt = sum(1 for v in verdict_records if v.verdict == "BROKEN")
    code_only_cnt = sum(1 for v in verdict_records if v.verdict == "CODE_ONLY")
    unknown_cnt = sum(1 for v in verdict_records if v.verdict == "UNKNOWN")

    resolved_units = total_units - unknown_cnt - code_only_cnt
    match_percentage = (match_cnt / resolved_units * 100.0) if resolved_units > 0 else 100.0

    return VerdictResult(
        cluster_id=cluster_id,
        snapshot_id=snapshot_id,
        verdicts=verdict_records,
        match_percentage=round(match_percentage, 2),
        total_units=total_units,
        resolved_units=resolved_units,
        match_count=match_cnt,
        broken_count=broken_cnt,
        code_only_count=code_only_cnt,
        unknown_count=unknown_cnt,
    )


@dataclass(frozen=True)
class UnitVerdictRecord:
    id: str
    cluster_id: str
    snapshot_id: str
    unit_id: str
    unit_kind: Literal["step", "branch"]
    mapping_status: str
    mapping_method: str
    route_segment_json: str | None
    verdict: FlowVerdict
    guard_verdict: str | None
    ai_bucket: AiBucket | None
    reason: str
    evidence_json: str
    comparator_version: int = 2


@dataclass(frozen=True)
class UnitVerdictResult:
    cluster_id: str
    snapshot_id: str
    verdicts: list[UnitVerdictRecord]
    match_percentage: float
    total_units: int
    resolved_units: int
    match_count: int
    partial_count: int
    broken_count: int
    unknown_count: int


async def run_unit_verdicts(
    db: Any, cluster_id: str, snapshot_id: str, provider_id: str | None = None
) -> UnitVerdictResult:
    """Run Phase 4 unit-level verdicts (step & branch) with warrant gating and LLM refine."""
    from ._map import run_unit_mapping

    mappings = await run_unit_mapping(db, cluster_id, snapshot_id, provider_id=provider_id)
    unit_records: list[UnitVerdictRecord] = []
    llm_candidate_units: list[dict[str, Any]] = []

    for m in mappings:
        record_id = f"unit_verdict:{cluster_id}:{m.unit_id}"
        base_verdict: FlowVerdict = "UNKNOWN"
        guard_verdict: str | None = None
        ai_bucket: AiBucket | None = None
        reason = m.reason
        seg_json = json.dumps(m.mapped_segment.to_dict()) if m.mapped_segment else None

        evidence_dict = {
            "corroboration_ratio": m.corroboration_ratio,
            "corroborated_bindings": m.corroborated_bindings,
            "contradicted_nodes": m.contradicted_nodes,
            "mapping_status": m.mapping_status,
            "mapping_method": m.mapping_method,
            "confidence": m.confidence,
        }

        if m.contradicted_nodes:
            # A deterministic contradiction is a real integrity failure and outranks "couldn't map".
            base_verdict = "BROKEN"
            ai_bucket = "stale_missing"
            c_file = m.contradicted_nodes[0].get("rel_path") or m.contradicted_nodes[0].get("binding")
            reason = f"BD step claims missing/unresolved asset ({c_file}), contradicted by code graph"
        elif m.mapping_status == "NO_SAFE_MATCH":
            base_verdict = "UNKNOWN"
            reason = "No code route can be safely associated with this step (external boundary or prose-only)."
        elif m.mapping_status == "MAPPED" and m.unit_kind == "step":
            if m.corroboration_ratio >= 0.8:
                base_verdict = "MATCH"
                reason = f"Step mapped to segment {m.mapped_segment.segment_id} with deterministic warrant (ratio {m.corroboration_ratio:.2f})"
            elif m.corroboration_ratio > 0.0:
                base_verdict = "PARTIAL"
                reason = f"Step partially corroborated into segment {m.mapped_segment.segment_id} (ratio {m.corroboration_ratio:.2f})"
            else:
                # corroboration_ratio == 0.0 -> cannot be clean MATCH (anti-hallucination floor)
                base_verdict = "PARTIAL"
                reason = "Mapping asserted without deterministic warrant."
        elif m.mapping_status == "MAPPED" and m.unit_kind == "branch":
            # Branch unit: code-side guard_text is currently NULL -> stays UNKNOWN
            base_verdict = "UNKNOWN"
            reason = "Route exists but the code side carries no guard evidence for this branch outcome."
        else:
            base_verdict = "UNKNOWN"

        rec = UnitVerdictRecord(
            id=record_id,
            cluster_id=cluster_id,
            snapshot_id=snapshot_id,
            unit_id=m.unit_id,
            unit_kind=m.unit_kind,
            mapping_status=m.mapping_status,
            mapping_method=m.mapping_method,
            route_segment_json=seg_json,
            verdict=base_verdict,
            guard_verdict=guard_verdict,
            ai_bucket=ai_bucket,
            reason=reason,
            evidence_json=json.dumps(evidence_dict),
        )
        unit_records.append(rec)

        if provider_id and base_verdict in ("MATCH", "PARTIAL", "BROKEN") and ai_bucket != "stale_missing":
            llm_candidate_units.append({
                "unit_id": record_id,
                "bd_claim": {"unit_id": m.unit_id, "kind": m.unit_kind, "reason": m.reason},
                "code_fact": {"segment_id": m.mapped_segment.segment_id if m.mapped_segment else None, "bindings": m.mapped_segment.bindings if m.mapped_segment else []},
                "warrant": {"corroboration_ratio": m.corroboration_ratio, "corroborated_bindings": m.corroborated_bindings},
                "base_verdict": base_verdict,
                "base_ai_bucket": ai_bucket,
                "corroboration_ratio": m.corroboration_ratio,
                "unit_kind": m.unit_kind,
            })

    # Execute LLM refine pass if candidate units exist and provider_id is passed
    if provider_id and llm_candidate_units:
        batches = [
            llm_candidate_units[i : i + LLM_BATCH_SIZE]
            for i in range(0, len(llm_candidate_units), LLM_BATCH_SIZE)
        ]
        semaphore = asyncio.Semaphore(5)
        tasks = [_evaluate_llm_batch(b, provider_id, semaphore) for b in batches]
        batch_results_list = await asyncio.gather(*tasks, return_exceptions=True)

        llm_responses: dict[str, LLMUnitVerdictResponse] = {}
        for res in batch_results_list:
            if isinstance(res, dict):
                llm_responses.update(res)
            elif isinstance(res, Exception):
                logger.warning(f"BFI unit verdict LLM batch failed: {res}")

        final_records: list[UnitVerdictRecord] = []
        cand_by_id = {u["unit_id"]: u for u in llm_candidate_units}

        for rec in unit_records:
            if rec.id in cand_by_id:
                cand = cand_by_id[rec.id]
                llm_res = llm_responses.get(rec.id)

                if llm_res:
                    if _is_value_comparison_reason(llm_res.reason):
                        logger.warning(f"Rejected LLM unit verdict for {rec.id}: asserts numeric comparison ('{llm_res.reason}')")
                        final_records.append(rec)
                        continue

                    final_verdict = llm_res.verdict
                    final_bucket = llm_res.ai_bucket or cand["base_ai_bucket"]

                    # Override Floor 1: LLM MATCH cannot override deterministic BROKEN
                    if cand["base_verdict"] == "BROKEN" and final_verdict == "MATCH":
                        logger.warning(f"Override floor clamped LLM MATCH to BROKEN for {rec.id}")
                        final_verdict = "BROKEN"
                        final_bucket = "stale_missing"

                    # Override Floor 2: corroboration_ratio == 0 cannot be a clean MATCH
                    if cand["corroboration_ratio"] == 0.0 and final_verdict == "MATCH":
                        logger.warning(f"Warrant floor clamped LLM MATCH to PARTIAL for un-warranted unit {rec.id}")
                        final_verdict = "PARTIAL"

                    # Override Floor 3: Branch without code guard evidence stays UNKNOWN
                    if cand["unit_kind"] == "branch" and final_verdict == "MATCH":
                        logger.warning(f"Branch guard floor clamped LLM MATCH to UNKNOWN for branch unit {rec.id}")
                        final_verdict = "UNKNOWN"

                    final_records.append(UnitVerdictRecord(
                        id=rec.id,
                        cluster_id=rec.cluster_id,
                        snapshot_id=rec.snapshot_id,
                        unit_id=rec.unit_id,
                        unit_kind=rec.unit_kind,
                        mapping_status=rec.mapping_status,
                        mapping_method=rec.mapping_method,
                        route_segment_json=rec.route_segment_json,
                        verdict=final_verdict,
                        guard_verdict=llm_res.guard_verdict or rec.guard_verdict,
                        ai_bucket=final_bucket,
                        reason=llm_res.reason,
                        evidence_json=rec.evidence_json,
                    ))
                else:
                    final_records.append(rec)
            else:
                final_records.append(rec)

        unit_records = final_records

    # Persist unit verdicts
    await db.execute(
        "DELETE FROM business_unit_verdicts WHERE cluster_id=? AND snapshot_id=?",
        (cluster_id, snapshot_id),
    )

    db_tuples = [
        (
            ur.id, ur.cluster_id, ur.snapshot_id, ur.unit_id,
            ur.unit_kind, ur.mapping_status, ur.mapping_method,
            ur.route_segment_json, ur.verdict, ur.guard_verdict,
            ur.ai_bucket, ur.reason, ur.evidence_json, ur.comparator_version, utc_now_iso(),
        )
        for ur in unit_records
    ]

    if db_tuples:
        await db.executemany(
            "INSERT INTO business_unit_verdicts (id, cluster_id, snapshot_id, unit_id, unit_kind, mapping_status, mapping_method, route_segment_json, verdict, guard_verdict, ai_bucket, reason, evidence_json, comparator_version, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            db_tuples,
        )
    await db.commit()

    # Compute Unit Calibration Metrics
    total_units = len(unit_records)
    match_cnt = sum(1 for u in unit_records if u.verdict == "MATCH")
    partial_cnt = sum(1 for u in unit_records if u.verdict == "PARTIAL")
    broken_cnt = sum(1 for u in unit_records if u.verdict == "BROKEN")
    unknown_cnt = sum(1 for u in unit_records if u.verdict == "UNKNOWN")

    resolved_units = match_cnt + partial_cnt + broken_cnt
    match_percentage = (match_cnt / resolved_units * 100.0) if resolved_units > 0 else 100.0

    return UnitVerdictResult(
        cluster_id=cluster_id,
        snapshot_id=snapshot_id,
        verdicts=unit_records,
        match_percentage=round(match_percentage, 2),
        total_units=total_units,
        resolved_units=resolved_units,
        match_count=match_cnt,
        partial_count=partial_cnt,
        broken_count=broken_cnt,
        unknown_count=unknown_cnt,
    )

