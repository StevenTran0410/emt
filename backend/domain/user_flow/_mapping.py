"""Stage U3: User-step to BD Flows/Steps/Branches Mapping (n:m).

- Pure-LLM semantic mapping between Japanese operator-level user steps and English technical BD units.
- Compact BD context catalog with aliases bd1..bdM and code-verdict summaries.
- Prompt cache discipline: BD catalog invariant in SYSTEM prompt, user steps in USER prompt.
- Strict Pydantic parsing with alias validation and unknown alias dropping (MAPPER_UNKNOWN_BD).
- Reverse index computation identifying BD_EXTRA (unmapped BD units) and BD_MISSING (unmapped user steps) candidates.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from domain.business_flow_integrity._llm import (
    LLM_CONCURRENCY,
    assert_exact_id_coverage,
    call_with_reasoning_ladder,
    coerce_results_wrapper,
)
from domain.doc_graph._llm_citation import _parse_llm_json
from domain.model_connector.types import ChatMessage, ChatRequest
from shared.logger import logger
from shared.utils import new_id, utc_now_iso


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BDUnit:
    alias: str  # "bd1", "bd2", ...
    unit_id: str  # real id (e.g. "bf:...", "bs:...", "bb:...")
    bd_kind: str  # "flow" | "step" | "branch"
    flow_id: str  # flow id it belongs to (or self for flow)
    name: str  # name / branch kind
    description: str  # description / functionality / guard_description
    flow_name: str = ""
    source_step_name: str = ""
    target_step_name: str = ""
    verdict: str | None = None
    verdict_reason: str | None = None


@dataclass
class BDContext:
    cluster_id: str
    units: list[BDUnit]
    alias_to_unit: dict[str, BDUnit]  # "bd1" -> BDUnit
    id_to_alias: dict[str, str]  # real_id -> "bd1"
    catalog_text: str  # compact formatted catalog for system prompt


class MappingItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bd_id: str  # alias like "bd12"
    relation: Literal["realizes", "partial", "related"]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


class StepMappingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unit_id: str  # alias like "u1"
    mappings: list[MappingItem] = Field(default_factory=list)


class MappingBatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    results: list[StepMappingResult]


# ---------------------------------------------------------------------------
# 1. BD-side Context Assembly (Deterministic)
# ---------------------------------------------------------------------------

def _verdict_tag(verdict: str | None, reason: str | None, max_reason: int = 90) -> str:
    """Compact code-verdict tag for a catalog line — truncate the verbose reason so the (cached)
    system-message catalog stays lean without losing the verdict signal the mapper needs."""
    if not verdict:
        return ""
    r = (reason or "").strip().replace("\n", " ")
    if len(r) > max_reason:
        r = r[:max_reason].rstrip() + "…"
    return f" [Code Verdict: {verdict}" + (f" - {r}" if r else "") + "]"


async def load_bd_context(
    db: Any,
    cluster_id: str,
    snapshot_id: str | None = None,
    bd_flow_ids: list[str] | None = None,
) -> BDContext:
    """Assemble all (or scoped) BD flows, steps, and branches with compact aliases bd1..bdM and code verdicts."""
    # 1. Query flows
    if bd_flow_ids:
        flow_placeholders = ",".join("?" for _ in bd_flow_ids)
        async with db.execute(
            f"SELECT id, name, description, ordinal FROM bd_business_flows WHERE cluster_id = ? AND id IN ({flow_placeholders}) ORDER BY ordinal, id",
            [cluster_id, *bd_flow_ids],
        ) as cur:
            flow_rows = await cur.fetchall()
    else:
        async with db.execute(
            "SELECT id, name, description, ordinal FROM bd_business_flows WHERE cluster_id = ? ORDER BY ordinal, id",
            (cluster_id,),
        ) as cur:
            flow_rows = await cur.fetchall()

    flow_ids = [r[0] if isinstance(r, (tuple, list)) else r["id"] for r in flow_rows]
    flow_ids_str = ",".join("?" for _ in flow_ids) if flow_ids else "''"

    # 2. Query steps
    step_rows = []
    if flow_ids:
        async with db.execute(
            f"SELECT id, flow_id, name, functionality, ordinal FROM bd_business_steps WHERE flow_id IN ({flow_ids_str}) ORDER BY ordinal, id",
            flow_ids,
        ) as cur:
            step_rows = await cur.fetchall()

    # 3. Query branches
    branch_rows = []
    if flow_ids:
        async with db.execute(
            f"SELECT id, flow_id, branch_kind, guard_description, source_step_id, target_step_id FROM bd_business_branches WHERE flow_id IN ({flow_ids_str}) ORDER BY id",
            flow_ids,
        ) as cur:
            branch_rows = await cur.fetchall()

    # 4. Query code-verdicts from business_unit_verdicts
    verdicts_map: dict[str, tuple[str, str]] = {}
    if snapshot_id:
        async with db.execute(
            "SELECT unit_id, verdict, reason FROM business_unit_verdicts WHERE cluster_id = ? AND snapshot_id = ?",
            (cluster_id, snapshot_id),
        ) as cur:
            v_rows = await cur.fetchall()
    else:
        async with db.execute(
            "SELECT unit_id, verdict, reason FROM business_unit_verdicts WHERE cluster_id = ?",
            (cluster_id,),
        ) as cur:
            v_rows = await cur.fetchall()

    for vr in v_rows:
        u_id = vr[0] if isinstance(vr, (tuple, list)) else vr["unit_id"]
        v_val = vr[1] if isinstance(vr, (tuple, list)) else vr["verdict"]
        v_rsn = vr[2] if isinstance(vr, (tuple, list)) else vr["reason"]
        verdicts_map[u_id] = (v_val, v_rsn or "")

    # 5. Build BD units with stable aliases bd1..bdM
    step_names: dict[str, str] = {}
    steps_by_flow: dict[str, list[dict[str, Any]]] = {}
    for r in step_rows:
        s_dict = dict(r) if hasattr(r, "keys") else {
            "id": r[0], "flow_id": r[1], "name": r[2], "functionality": r[3], "ordinal": r[4]
        }
        step_names[s_dict["id"]] = s_dict.get("name") or ""
        steps_by_flow.setdefault(s_dict["flow_id"], []).append(s_dict)

    branches_by_flow: dict[str, list[dict[str, Any]]] = {}
    for r in branch_rows:
        b_dict = dict(r) if hasattr(r, "keys") else {
            "id": r[0], "flow_id": r[1], "branch_kind": r[2], "guard_description": r[3],
            "source_step_id": r[4], "target_step_id": r[5]
        }
        branches_by_flow.setdefault(b_dict["flow_id"], []).append(b_dict)

    units: list[BDUnit] = []
    alias_to_unit: dict[str, BDUnit] = {}
    id_to_alias: dict[str, str] = {}
    catalog_lines: list[str] = [
        "TECHNICAL BD FLOWS AND UNITS CATALOG:",
        "================================================================================",
    ]

    counter = 1
    for fr in flow_rows:
        f_dict = dict(fr) if hasattr(fr, "keys") else {
            "id": fr[0], "name": fr[1], "description": fr[2], "ordinal": fr[3]
        }
        f_id = f_dict["id"]
        f_name = f_dict.get("name") or ""
        f_alias = f"bd{counter}"
        counter += 1

        f_verdict_info = verdicts_map.get(f_id)
        f_verdict = f_verdict_info[0] if f_verdict_info else None
        f_verdict_rsn = f_verdict_info[1] if f_verdict_info else None

        f_unit = BDUnit(
            alias=f_alias,
            unit_id=f_id,
            bd_kind="flow",
            flow_id=f_id,
            name=f_name,
            description=f_dict.get("description") or "",
            flow_name=f_name,
            verdict=f_verdict,
            verdict_reason=f_verdict_rsn,
        )
        units.append(f_unit)
        alias_to_unit[f_alias] = f_unit
        id_to_alias[f_id] = f_alias

        v_summary = _verdict_tag(f_verdict, f_verdict_rsn)
        catalog_lines.append(f'Flow [{f_alias}]: "{f_unit.name}"{v_summary}')
        if f_unit.description:
            catalog_lines.append(f"  Description: {f_unit.description}")

        # Steps in this flow
        for s in steps_by_flow.get(f_id, []):
            s_id = s["id"]
            s_alias = f"bd{counter}"
            counter += 1

            s_verdict_info = verdicts_map.get(s_id)
            s_verdict = s_verdict_info[0] if s_verdict_info else None
            s_verdict_rsn = s_verdict_info[1] if s_verdict_info else None

            s_unit = BDUnit(
                alias=s_alias,
                unit_id=s_id,
                bd_kind="step",
                flow_id=f_id,
                name=s.get("name") or "",
                description=s.get("functionality") or "",
                flow_name=f_name,
                verdict=s_verdict,
                verdict_reason=s_verdict_rsn,
            )
            units.append(s_unit)
            alias_to_unit[s_alias] = s_unit
            id_to_alias[s_id] = s_alias

            sv_summary = _verdict_tag(s_verdict, s_verdict_rsn)
            catalog_lines.append(f'  Step [{s_alias}]: "{s_unit.name}"{sv_summary}')
            if s_unit.description:
                catalog_lines.append(f"    Functionality: {s_unit.description}")

        # Branches in this flow
        for b in branches_by_flow.get(f_id, []):
            b_id = b["id"]
            b_alias = f"bd{counter}"
            counter += 1

            b_verdict_info = verdicts_map.get(b_id)
            b_verdict = b_verdict_info[0] if b_verdict_info else None
            b_verdict_rsn = b_verdict_info[1] if b_verdict_info else None

            src_sname = step_names.get(b.get("source_step_id") or "", "")
            tgt_sname = step_names.get(b.get("target_step_id") or "", "")

            b_unit = BDUnit(
                alias=b_alias,
                unit_id=b_id,
                bd_kind="branch",
                flow_id=f_id,
                name=b.get("branch_kind") or "branch",
                description=b.get("guard_description") or "",
                flow_name=f_name,
                source_step_name=src_sname,
                target_step_name=tgt_sname,
                verdict=b_verdict,
                verdict_reason=b_verdict_rsn,
            )
            units.append(b_unit)
            alias_to_unit[b_alias] = b_unit
            id_to_alias[b_id] = b_alias

            bv_summary = _verdict_tag(b_verdict, b_verdict_rsn)
            catalog_lines.append(f'  Branch [{b_alias}] (Kind: {b_unit.name}): "{b_unit.description}"{bv_summary}')

        catalog_lines.append("")

    catalog_lines.append("================================================================================")
    catalog_text = "\n".join(catalog_lines)

    return BDContext(
        cluster_id=cluster_id,
        units=units,
        alias_to_unit=alias_to_unit,
        id_to_alias=id_to_alias,
        catalog_text=catalog_text,
    )


# ---------------------------------------------------------------------------
# 2. LLM#3 Mapper Prompts & Execution
# ---------------------------------------------------------------------------

_MAPPER_SYSTEM_PROMPT_TEMPLATE = """You are an expert legacy software modernization analyst specializing in semantic alignment between operator-level user flows and technical business design (BD) documents.

Your task is to map OPERATOR-LEVEL Japanese user steps to TECHNICAL BD units (flows, steps, branches) that describe the same or related behavior.

CONTEXT & VOCABULARY ALIGNMENT:
- User steps are extracted from customer test scenarios / narrative tables in Japanese (describing operator actions, screens, triggers, inputs, error messages, and expected outcomes).
- BD units are technical Business Design specifications written in English (describing programmatic steps, validation functions, error branches, and screen transitions).
- Vocabulary and altitude differ significantly:
  * A user action (e.g. "press Enter -> validate lot -> submit batch job -> display confirmation message") may span a BD step and multiple BD branches.
  * A broad user step (e.g. "PF1, PF3, PF7, PF8, PF24 all work normally") may map to several distinct BD flows (one per function key).
  * Multiple user steps may map to the same BD step or branch.

RELATION TYPES:
- `realizes`: The BD unit describes the exact same business behavior, outcome, or rule as the user step.
- `partial`: The BD unit describes a part of the behavior in the user step (or the user step describes only a subset of the BD unit).
- `related`: The BD unit touches the same screen, program, or functional area, but asserts a different rule or contradictory claim (critical for downstream contradiction detection).

MAPPING PRINCIPLES:
1. For each user step, output `mappings`: a list of zero, one, or more matching BD units using their `bdN` alias (e.g. "bd1", "bd12").
2. n:m mapping is expected: a user step can map to 0, 1, or multiple BD units; multiple user steps can map to the same BD unit.
3. If NO BD unit describes the behavior in the user step, return an EMPTY list `mappings: []`. DO NOT FORCE-MAP. An empty mapping is a crucial signal indicating the BD specification missed this user behavior (candidate BD_MISSING).
4. Code verdicts attached to BD units are provided as historical context only, not absolute truth.
5. Consider the step's `text_ja`, `text_en`, `trigger_ja`, `expected_ja`, `screen_name_ja`, and `section_id`; compare with the BD unit's name, functionality, and guard description.
6. Every `unit_id` from the input batch (`u1`..`uN`) MUST appear in `results` exactly once.

OUTPUT FORMAT:
Respond with a JSON object strictly matching this schema:
{
  "results": [
    {
      "unit_id": "u1",
      "mappings": [
        {
          "bd_id": "bd2",
          "relation": "realizes",
          "confidence": 0.95,
          "reason": "BD step bd2 validates coil test parameters matching user step 2.1① coil input validation."
        },
        {
          "bd_id": "bd4",
          "relation": "partial",
          "confidence": 0.8,
          "reason": "BD branch bd4 handles invalid condition code error, which partially realizes step error handling."
        }
      ]
    },
    {
      "unit_id": "u2",
      "mappings": []
    }
  ]
}

{catalog_text}
"""


async def evaluate_mapping_batch(
    batch_steps: list[dict[str, Any]],
    bd_context: BDContext,
    provider_id: str,
) -> tuple[dict[str, StepMappingResult], str, int, float, bool, str | None]:
    """Run LLM#3 Mapper on a batch of user steps (<=5 steps)."""
    step_alias_map: dict[str, str] = {}
    prompt_steps: list[dict[str, Any]] = []

    for idx, step in enumerate(batch_steps, start=1):
        u_alias = f"u{idx}"
        step_alias_map[u_alias] = step["id"]

        prompt_steps.append({
            "unit_id": u_alias,
            "kind": step.get("kind"),
            "section_id": step.get("section_id"),
            "text_ja": step.get("text_ja"),
            "text_en": step.get("text_en"),
            "trigger_ja": step.get("trigger_ja"),
            "expected_ja": step.get("expected_ja"),
            "screen_name_ja": step.get("screen_name_ja"),
        })

    expected_aliases = set(step_alias_map.keys())
    system_prompt = _MAPPER_SYSTEM_PROMPT_TEMPLATE.replace("{catalog_text}", bd_context.catalog_text)
    user_prompt = json.dumps({"steps_to_map": prompt_steps}, ensure_ascii=False, indent=2)

    attempts = {"n": 0}

    def _build_req(effort: str) -> ChatRequest:
        attempts["n"] += 1
        return ChatRequest(
            provider_id=provider_id,
            messages=[
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(role="user", content=user_prompt),
            ],
            stream=True,
            max_completion_tokens=50000,
            temperature=0.0,
            json_mode=True,
            reasoning_effort=effort,
        )

    def _parse(text: str) -> dict[str, StepMappingResult]:
        parsed = coerce_results_wrapper(_parse_llm_json(text))
        validated = MappingBatchResponse.model_validate(parsed)
        assert_exact_id_coverage([r.unit_id for r in validated.results], expected_aliases, "BDMapper")
        return {r.unit_id: r for r in validated.results}

    start = time.monotonic()
    ladder_res = await call_with_reasoning_ladder(
        _build_req, _parse, first_effort="low", label="UserFlow BD Mapper (LLM#3)",
        provider_id=provider_id,
    )
    latency_ms = round((time.monotonic() - start) * 1000, 1)
    retry_count = max(attempts["n"] - 1, 0)

    if ladder_res is None:
        fallback_results = {
            u_alias: StepMappingResult(unit_id=u_alias, mappings=[])
            for u_alias in expected_aliases
        }
        return fallback_results, "", retry_count, latency_ms, False, "LADDER_FAILED"

    res_dict, raw_text = ladder_res
    return res_dict, raw_text, retry_count, latency_ms, True, None


# ---------------------------------------------------------------------------
# 3. Stage U3 Coordinator
# ---------------------------------------------------------------------------

async def map_user_steps_to_bd(
    db: Any,
    doc_id: str,
    cluster_id: str,
    snapshot_id: str,
    run_id: str,
    in_scope_steps: list[dict[str, Any]],
    provider_id: str | None,
) -> tuple[int, int]:
    """Execute Stage U3 user-step to BD-unit mapping.

    Returns (mapped_steps_count, batches_issued).
    """
    now = utc_now_iso()

    # 1. Load compact BD catalog context
    bd_context = await load_bd_context(db, cluster_id, snapshot_id)

    # If offline or no BD units exist
    if provider_id is None or not bd_context.units:
        for s in in_scope_steps:
            s_id = s["id"]
            audit_payload = {
                "step_id": s_id,
                "offline": provider_id is None,
                "bd_units_available": len(bd_context.units),
                "mappings": [],
                "dropped_unknown_bd_count": 0,
                "dropped_mappings": [],
                "agent_raw": "",
                "retry_count": 0,
                "latency_ms": 0.0,
                "success": True,
                "error": None if provider_id is None else "NO_BD_UNITS",
            }
            await db.execute(
                "INSERT INTO user_run_artifacts (id, run_id, doc_id, ref_id, payload, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    f"ufart:{new_id()}",
                    run_id,
                    doc_id,
                    f"mapping:{s_id}",
                    json.dumps(audit_payload, ensure_ascii=False),
                    now,
                ),
            )

        # Summary artifact for offline / empty run
        summary_payload = {
            "stage": "user_bd_mapping",
            "run_id": run_id,
            "doc_id": doc_id,
            "cluster_id": cluster_id,
            "total_user_steps": len(in_scope_steps),
            "total_bd_units": len(bd_context.units),
            "total_mappings": 0,
            "unmapped_user_step_ids": [s["id"] for s in in_scope_steps],
            "bd_unit_counts": [
                {
                    "bd_id": u.unit_id,
                    "alias": u.alias,
                    "bd_kind": u.bd_kind,
                    "name": u.name,
                    "incoming_count": 0,
                    "is_bd_extra_candidate": True,
                }
                for u in bd_context.units
            ],
            "unmapped_bd_unit_ids": [u.unit_id for u in bd_context.units],
        }
        await db.execute(
            "INSERT INTO user_run_artifacts (id, run_id, doc_id, ref_id, payload, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                f"ufart:{new_id()}",
                run_id,
                doc_id,
                "__run_summary__",
                json.dumps(summary_payload, ensure_ascii=False),
                now,
            ),
        )
        return 0, 0

    # 2. Batch user steps (<=5 steps per batch)
    batches: list[list[dict[str, Any]]] = []
    for i in range(0, len(in_scope_steps), 5):
        batches.append(in_scope_steps[i:i + 5])

    batches_issued = len(batches)
    semaphore = asyncio.Semaphore(LLM_CONCURRENCY)

    steps_with_valid_mappings: set[str] = set()
    all_valid_mappings_count = 0
    unit_incoming_counts: dict[str, int] = {u.unit_id: 0 for u in bd_context.units}

    async def _process_batch(b: list[dict[str, Any]]):
        nonlocal all_valid_mappings_count
        async with semaphore:
            res_dict, raw_text, retries, lat, ok, err = await evaluate_mapping_batch(
                b, bd_context, provider_id
            )

            for idx, step in enumerate(b, start=1):
                s_id = step["id"]
                u_alias = f"u{idx}"
                step_res = res_dict.get(u_alias)

                valid_mappings_for_step = []
                dropped_mappings = []
                dropped_unknown_bd_count = 0

                if step_res and step_res.mappings:
                    for m in step_res.mappings:
                        # Validate bd_id alias in catalog
                        if m.bd_id in bd_context.alias_to_unit:
                            bd_unit = bd_context.alias_to_unit[m.bd_id]
                            real_bd_id = bd_unit.unit_id
                            bd_kind = bd_unit.bd_kind

                            await db.execute(
                                "INSERT INTO user_bd_mappings (id, run_id, user_step_id, bd_kind, bd_id, relation, confidence, reason, created_at) "
                                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                (
                                    f"ubdm:{new_id()}",
                                    run_id,
                                    s_id,
                                    bd_kind,
                                    real_bd_id,
                                    m.relation,
                                    m.confidence,
                                    m.reason,
                                    now,
                                ),
                            )

                            valid_mappings_for_step.append({
                                "bd_id": real_bd_id,
                                "bd_alias": m.bd_id,
                                "bd_kind": bd_kind,
                                "relation": m.relation,
                                "confidence": m.confidence,
                                "reason": m.reason,
                            })
                            unit_incoming_counts[real_bd_id] = unit_incoming_counts.get(real_bd_id, 0) + 1
                            all_valid_mappings_count += 1
                        else:
                            # Drop unknown BD alias
                            dropped_unknown_bd_count += 1
                            dropped_mappings.append({
                                "bd_id": m.bd_id,
                                "relation": m.relation,
                                "confidence": m.confidence,
                                "reason": m.reason,
                                "error": "MAPPER_UNKNOWN_BD",
                            })

                if valid_mappings_for_step:
                    steps_with_valid_mappings.add(s_id)

                # Record per-step audit artifact
                audit_payload = {
                    "step_id": s_id,
                    "mappings": valid_mappings_for_step,
                    "dropped_unknown_bd_count": dropped_unknown_bd_count,
                    "dropped_mappings": dropped_mappings,
                    "agent_raw": raw_text,
                    "retry_count": retries,
                    "latency_ms": lat,
                    "success": ok,
                    "error": err,
                }
                await db.execute(
                    "INSERT INTO user_run_artifacts (id, run_id, doc_id, ref_id, payload, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        f"ufart:{new_id()}",
                        run_id,
                        doc_id,
                        f"mapping:{s_id}",
                        json.dumps(audit_payload, ensure_ascii=False),
                        now,
                    ),
                )

    await asyncio.gather(*[_process_batch(b) for b in batches])

    # 3. Compute Reverse Index & Run Summary Artifact
    unmapped_user_step_ids = [
        s["id"] for s in in_scope_steps if s["id"] not in steps_with_valid_mappings
    ]
    unmapped_bd_unit_ids = [
        u.unit_id for u in bd_context.units if unit_incoming_counts.get(u.unit_id, 0) == 0
    ]

    bd_unit_counts = [
        {
            "bd_id": u.unit_id,
            "alias": u.alias,
            "bd_kind": u.bd_kind,
            "name": u.name,
            "incoming_count": unit_incoming_counts.get(u.unit_id, 0),
            "is_bd_extra_candidate": unit_incoming_counts.get(u.unit_id, 0) == 0,
        }
        for u in bd_context.units
    ]

    summary_payload = {
        "stage": "user_bd_mapping",
        "run_id": run_id,
        "doc_id": doc_id,
        "cluster_id": cluster_id,
        "total_user_steps": len(in_scope_steps),
        "total_bd_units": len(bd_context.units),
        "total_mappings": all_valid_mappings_count,
        "unmapped_user_step_ids": unmapped_user_step_ids,
        "bd_unit_counts": bd_unit_counts,
        "unmapped_bd_unit_ids": unmapped_bd_unit_ids,
    }

    await db.execute(
        "INSERT INTO user_run_artifacts (id, run_id, doc_id, ref_id, payload, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            f"ufart:{new_id()}",
            run_id,
            doc_id,
            "__run_summary__",
            json.dumps(summary_payload, ensure_ascii=False),
            now,
        ),
    )

    return len(steps_with_valid_mappings), batches_issued
