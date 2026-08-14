"""LLM step/branch-to-code route segment mapping with warrant computation (Phase 4)."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from shared.logger import logger
from shared.utils import utc_now_iso

from ..doc_graph._llm_citation import _parse_llm_json
from ..model_connector.types import ChatMessage, ChatRequest
from ._llm import call_with_reasoning_ladder
from ._segments import Segment, build_route_segments

MappingStatus = Literal["MAPPED", "NO_SAFE_MATCH", "AMBIGUOUS"]
MappingMethod = Literal["llm", "exact_binding", "offline"]
ConfidenceLevel = Literal["LOW", "MEDIUM", "HIGH"]


@dataclass
class MappingRecord:
    unit_id: str
    unit_kind: Literal["step", "branch"]
    mapping_status: MappingStatus
    mapping_method: MappingMethod
    mapped_segment: Segment | None
    confidence: ConfidenceLevel
    reason: str
    corroboration_ratio: float = 0.0
    corroborated_bindings: list[str] = field(default_factory=list)
    contradicted_nodes: list[dict[str, Any]] = field(default_factory=list)


class LLMUnitMappingItem(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    unit_id: str
    segment_id: str | None = None
    mapping_status: MappingStatus
    confidence: ConfidenceLevel = "MEDIUM"
    reason: str


class LLMBatchMappingResponse(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    results: list[LLMUnitMappingItem]


_MAP_SYSTEM_PROMPT = (
    "You are a deterministic business flow mapper. Your job is to map each business unit (step or branch prose) "
    "to the candidate code route segment that realizes it.\n"
    "INTEGRITY ONLY: map based on program identity and execution flow. Do NOT evaluate values or completeness.\n"
    "You MUST choose segment_id ONLY from the offered candidate_segments list for that unit. "
    "If no offered candidate realizes the unit, set segment_id to null and mapping_status to NO_SAFE_MATCH.\n"
    "Output ONLY valid JSON matching: {\"results\": [ {\"unit_id\": \"...\", \"segment_id\": \"...\"|null, \"mapping_status\": \"MAPPED\"|\"NO_SAFE_MATCH\"|\"AMBIGUOUS\", \"confidence\": \"LOW\"|\"MEDIUM\"|\"HIGH\", \"reason\": \"...\"} ]}.\n"
    "Keep each reason under 20 words. Respond strictly in English."
)


async def _evaluate_llm_mapping_batch(
    batch_units: list[dict[str, Any]], provider_id: str, semaphore: asyncio.Semaphore
) -> dict[str, LLMUnitMappingItem]:
    """Execute a batched LLM step↔segment mapping pass (max ≤5 units/call)."""
    async with semaphore:
        prompt_payload = [
            {
                "unit_id": u["unit_id"],
                "unit_kind": u["unit_kind"],
                "name": u["name"],
                "prose": u["prose"],
                "branch_kind": u.get("branch_kind"),
                "candidate_segments": [
                    {
                        "segment_id": s.segment_id,
                        "bindings": s.bindings,
                        "edge_kinds": s.edge_kinds,
                    }
                    for s in u["candidate_segments"]
                ],
            }
            for u in batch_units
        ]

        def _build_req(effort: str) -> ChatRequest:
            return ChatRequest(
                provider_id=provider_id,
                messages=[
                    ChatMessage(role="system", content=_MAP_SYSTEM_PROMPT),
                    ChatMessage(role="user", content=json.dumps(prompt_payload, indent=2)),
                ],
                stream=True,
                max_completion_tokens=50000,
                temperature=0.0,
                json_mode=True,
                reasoning_effort=effort,
            )

        def _parse(text: str) -> dict[str, LLMUnitMappingItem]:
            parsed = _parse_llm_json(text)
            validated = LLMBatchMappingResponse.model_validate(parsed)
            return {r.unit_id: r for r in validated.results}

        # Judgment task (mapping decision) — open at high reasoning, one low-effort retry on failure.
        ladder_res = await call_with_reasoning_ladder(_build_req, _parse, first_effort="high", label="BFI mapping")
        return ladder_res[0] if ladder_res is not None else {}


def _compute_unit_warrant(
    source_node_ids: list[str],
    mapped_segment: Segment | None,
    bd_nodes_map: dict[str, dict[str, Any]],
    align_by_bd_node: dict[str, dict[str, Any]],
    code_nodes_map: dict[str, dict[str, Any]],
) -> tuple[float, list[str], list[dict[str, Any]]]:
    """Compute warrant corroboration ratio and contradicted evidence for a unit."""
    if not source_node_ids:
        return 0.0, [], []

    resolvable_count = 0
    corroborated_count = 0
    corroborated_bindings: list[str] = []
    contradicted_nodes: list[dict[str, Any]] = []

    seg_node_set = set(mapped_segment.node_ids) if mapped_segment else set()

    for bd_nid in source_node_ids:
        align = align_by_bd_node.get(bd_nid, {})
        tag = align.get("tag")
        code_nid = align.get("code_node_id")

        if tag in ("DOC_MATCHED", "DOC_CONTRADICTED") or code_nid:
            resolvable_count += 1

        if tag == "DOC_CONTRADICTED":
            c_node = code_nodes_map.get(code_nid, {}) if code_nid else {}
            contradicted_nodes.append({
                "bd_node_id": bd_nid,
                "code_node_id": code_nid,
                "rel_path": c_node.get("rel_path", ""),
                "binding": bd_nodes_map.get(bd_nid, {}).get("binding"),
            })

        if tag == "DOC_MATCHED" and code_nid and code_nid in seg_node_set:
            corroborated_count += 1
            b = bd_nodes_map.get(bd_nid, {}).get("binding")
            if b and b not in corroborated_bindings:
                corroborated_bindings.append(b)

    ratio = (corroborated_count / resolvable_count) if resolvable_count > 0 else 0.0
    return round(ratio, 4), corroborated_bindings, contradicted_nodes


async def run_unit_mapping(
    db: Any, cluster_id: str, snapshot_id: str, provider_id: str | None = None
) -> list[MappingRecord]:
    """Map business steps and branches to candidate code route segments."""
    # 1. Build deterministic route segments
    segments = await build_route_segments(db, snapshot_id)
    seg_by_id = {s.segment_id: s for s in segments}

    # Map code_node_id -> segments containing it
    node_to_segs: dict[str, list[Segment]] = {}
    for seg in segments:
        for nid in seg.node_ids:
            node_to_segs.setdefault(nid, []).append(seg)

    # 2. Fetch business flows, steps, branches
    async with db.execute(
        "SELECT id FROM bd_business_flows WHERE cluster_id=?", (cluster_id,)
    ) as cur:
        flow_ids = [r["id"] for r in await cur.fetchall()]

    if not flow_ids:
        return []

    flow_ids_str = ",".join("?" for _ in flow_ids)

    async with db.execute(
        f"SELECT id, flow_id, name, functionality, ordinal, source_node_ids FROM bd_business_steps WHERE flow_id IN ({flow_ids_str}) ORDER BY ordinal ASC",
        flow_ids,
    ) as cur:
        step_rows = [dict(r) for r in await cur.fetchall()]

    async with db.execute(
        f"SELECT id, flow_id, source_step_id, target_step_id, branch_kind, guard_description, source_edge_ids FROM bd_business_branches WHERE flow_id IN ({flow_ids_str})",
        flow_ids,
    ) as cur:
        branch_rows = [dict(r) for r in await cur.fetchall()]

    step_map = {s["id"]: s for s in step_rows}

    # 3. Fetch bd_flow_nodes, code_flow_nodes, flow_alignment
    async with db.execute(
        "SELECT id, node_kind, binding, binding_type, label FROM bd_flow_nodes WHERE cluster_id=?",
        (cluster_id,),
    ) as cur:
        bd_nodes_map = {r["id"]: dict(r) for r in await cur.fetchall()}

    async with db.execute(
        "SELECT id, rel_path, binding, node_kind FROM code_flow_nodes WHERE snapshot_id=?",
        (snapshot_id,),
    ) as cur:
        code_nodes_map = {r["id"]: dict(r) for r in await cur.fetchall()}

    async with db.execute(
        "SELECT id, bd_node_id, code_node_id, match_method, tag FROM flow_alignment WHERE cluster_id=? AND snapshot_id=?",
        (cluster_id, snapshot_id),
    ) as cur:
        align_rows = [dict(r) for r in await cur.fetchall()]

    align_by_bd_node = {a["bd_node_id"]: dict(a) for a in align_rows if a.get("bd_node_id")}

    records: list[MappingRecord] = []
    llm_candidate_units: list[dict[str, Any]] = []

    # 4. Process Step units
    for s_row in step_rows:
        unit_id = s_row["id"]
        source_nids = json.loads(s_row["source_node_ids"]) if s_row.get("source_node_ids") else []

        # Find resolved code nodes
        resolved_cids = [
            align_by_bd_node[nid]["code_node_id"]
            for nid in source_nids
            if nid in align_by_bd_node and align_by_bd_node[nid].get("code_node_id")
        ]

        # Shortlist candidate segments
        cand_segs_set: set[str] = set()
        for cid in resolved_cids:
            for seg in node_to_segs.get(cid, []):
                cand_segs_set.add(seg.segment_id)

        # Cap candidates ~6 max
        candidate_segments = [seg_by_id[sid] for sid in sorted(cand_segs_set)[:6]]

        # Offline resolution check
        resolved_bindings = [
            bd_nodes_map[nid]["binding"] for nid in source_nids if nid in bd_nodes_map and bd_nodes_map[nid].get("binding")
        ]

        offline_mapped_seg = None
        if len(candidate_segments) == 1:
            seg = candidate_segments[0]
            if resolved_bindings and all(b in seg.bindings for b in resolved_bindings):
                offline_mapped_seg = seg

        if provider_id and candidate_segments:
            llm_candidate_units.append({
                "unit_id": unit_id,
                "unit_kind": "step",
                "name": s_row["name"],
                "prose": s_row["functionality"],
                "source_nids": source_nids,
                "candidate_segments": candidate_segments,
                "offline_fallback": offline_mapped_seg,
            })
        else:
            status: MappingStatus = "MAPPED" if offline_mapped_seg else "NO_SAFE_MATCH"
            method: MappingMethod = "exact_binding" if offline_mapped_seg else "offline"
            reason = (
                f"Exact binding match to candidate segment {offline_mapped_seg.segment_id}"
                if offline_mapped_seg
                else "No code route can be safely associated with this step (external boundary or prose-only)."
            )

            ratio, corr_b, contr_n = _compute_unit_warrant(
                source_nids, offline_mapped_seg, bd_nodes_map, align_by_bd_node, code_nodes_map
            )
            records.append(MappingRecord(
                unit_id=unit_id,
                unit_kind="step",
                mapping_status=status,
                mapping_method=method,
                mapped_segment=offline_mapped_seg,
                confidence="HIGH" if offline_mapped_seg else "LOW",
                reason=reason,
                corroboration_ratio=ratio,
                corroborated_bindings=corr_b,
                contradicted_nodes=contr_n,
            ))

    # 5. Process Branch units
    for b_row in branch_rows:
        unit_id = b_row["id"]
        src_step = step_map.get(b_row["source_step_id"], {})
        dst_step = step_map.get(b_row.get("target_step_id", ""), {})

        src_nids = json.loads(src_step.get("source_node_ids", "[]")) if src_step else []
        dst_nids = json.loads(dst_step.get("source_node_ids", "[]")) if dst_step else []
        source_nids = list(set(src_nids + dst_nids))

        resolved_cids = [
            align_by_bd_node[nid]["code_node_id"]
            for nid in source_nids
            if nid in align_by_bd_node and align_by_bd_node[nid].get("code_node_id")
        ]

        cand_segs_set: set[str] = set()
        for cid in resolved_cids:
            for seg in node_to_segs.get(cid, []):
                cand_segs_set.add(seg.segment_id)

        candidate_segments = [seg_by_id[sid] for sid in sorted(cand_segs_set)[:6]]

        offline_mapped_seg = candidate_segments[0] if len(candidate_segments) == 1 else None

        if provider_id and candidate_segments:
            llm_candidate_units.append({
                "unit_id": unit_id,
                "unit_kind": "branch",
                "name": f"Branch: {b_row['branch_kind']}",
                "prose": b_row["guard_description"],
                "branch_kind": b_row["branch_kind"],
                "source_nids": source_nids,
                "candidate_segments": candidate_segments,
                "offline_fallback": offline_mapped_seg,
            })
        else:
            status: MappingStatus = "MAPPED" if offline_mapped_seg else "NO_SAFE_MATCH"
            method: MappingMethod = "exact_binding" if offline_mapped_seg else "offline"
            reason = (
                f"Branch associated with segment {offline_mapped_seg.segment_id}"
                if offline_mapped_seg
                else "No code route can be safely associated with this branch."
            )

            ratio, corr_b, contr_n = _compute_unit_warrant(
                source_nids, offline_mapped_seg, bd_nodes_map, align_by_bd_node, code_nodes_map
            )
            records.append(MappingRecord(
                unit_id=unit_id,
                unit_kind="branch",
                mapping_status=status,
                mapping_method=method,
                mapped_segment=offline_mapped_seg,
                confidence="MEDIUM" if offline_mapped_seg else "LOW",
                reason=reason,
                corroboration_ratio=ratio,
                corroborated_bindings=corr_b,
                contradicted_nodes=contr_n,
            ))

    # 6. Execute LLM mapping if candidate units exist and provider_id is passed
    if provider_id and llm_candidate_units:
        batches = [
            llm_candidate_units[i : i + 5]
            for i in range(0, len(llm_candidate_units), 5)
        ]
        semaphore = asyncio.Semaphore(5)
        tasks = [_evaluate_llm_mapping_batch(b, provider_id, semaphore) for b in batches]
        batch_results_list = await asyncio.gather(*tasks, return_exceptions=True)

        llm_map_res: dict[str, LLMUnitMappingItem] = {}
        for res in batch_results_list:
            if isinstance(res, dict):
                llm_map_res.update(res)
            elif isinstance(res, Exception):
                logger.warning(f"BFI mapping LLM batch failed: {res}")

        unit_by_id = {u["unit_id"]: u for u in llm_candidate_units}

        for u_dict in llm_candidate_units:
            uid = u_dict["unit_id"]
            cand_segs = u_dict["candidate_segments"]
            cand_seg_ids = {s.segment_id for s in cand_segs}
            source_nids = u_dict["source_nids"]

            item = llm_map_res.get(uid)
            mapped_seg = None
            status: MappingStatus = "NO_SAFE_MATCH"
            method: MappingMethod = "offline"
            confidence: ConfidenceLevel = "LOW"
            reason = ""

            if item and item.segment_id and item.segment_id in cand_seg_ids:
                mapped_seg = seg_by_id.get(item.segment_id)
                status = item.mapping_status
                method = "llm"
                confidence = item.confidence
                reason = item.reason
            elif u_dict["offline_fallback"]:
                mapped_seg = u_dict["offline_fallback"]
                status = "MAPPED"
                method = "exact_binding"
                confidence = "HIGH"
                reason = f"Exact binding match to candidate segment {mapped_seg.segment_id}"
            else:
                status = "NO_SAFE_MATCH"
                method = "offline"
                confidence = "LOW"
                reason = "No candidate segment was safely selected by LLM or offline fallback."

            ratio, corr_b, contr_n = _compute_unit_warrant(
                source_nids, mapped_seg, bd_nodes_map, align_by_bd_node, code_nodes_map
            )

            records.append(MappingRecord(
                unit_id=uid,
                unit_kind=u_dict["unit_kind"],
                mapping_status=status,
                mapping_method=method,
                mapped_segment=mapped_seg,
                confidence=confidence,
                reason=reason,
                corroboration_ratio=ratio,
                corroborated_bindings=corr_b,
                contradicted_nodes=contr_n,
            ))

    return records
