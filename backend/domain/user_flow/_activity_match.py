"""Phase U2: Tier-1 Activity Matcher Stage (LLM#6).

Matches high-level user activities against the cluster's BD business flows
(batch <= 5 activities per call) with FULLY | PARTIAL | NONE verdicts.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from domain.business_flow_integrity._llm import assert_exact_id_coverage, call_with_reasoning_ladder
from domain.doc_graph._llm_citation import _parse_llm_json
from domain.model_connector.types import ChatMessage, ChatRequest
from shared.utils import new_id, utc_now_iso

logger = logging.getLogger("codespectra.user_flow.activity_match")

TIER1_PROMPT_VERSION = "u2_match_v1"
TIER1_SCHEMA_VERSION = "u2_match_v1"
_MAX_CONCURRENT_MATCHING = 4
_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_MATCHING)
BATCH_SIZE = 5

MatchStatus = Literal["FULLY", "PARTIAL", "NONE"]


# Schema tolerance (U-1 lesson): extra="ignore" + defaults — strict/forbid rejected real model
# output wholesale (missing confidence/reason → every batch fell back to NONE).
class LLMActivityMatchItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    activity_alias: str
    match_status: MatchStatus
    bd_flow_aliases: list[str] = []  # e.g. ["bf1", "bf3"] or []
    confidence: float = 0.5
    reason: str = ""


class LLMTier1MatchResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    matches: list[LLMActivityMatchItem]


_TIER1_SYSTEM_PROMPT = """You are an expert systems auditor performing Tier-1 high-level reconciliation between customer user activities and specification Business Flows (BD Flows).

For each user activity in the batch, determine whether it corresponds to one or more specification BD Flows in the catalog:
- FULLY: The activity's business purpose is fully covered by the matched BD Flow(s).
- PARTIAL: The activity has substantial overlap with the matched BD Flow(s), but some customer requirements are not covered in the BD specification.
- NONE: The customer performs this activity, but the system specification (BD) has NO corresponding business flow for it.

CRITICAL RULES:
1. NONE is a legitimate, expected answer. If an activity describes functionality absent from the BD catalog, choose NONE. NEVER force a weak or vague match.
2. If match_status is "NONE", bd_flow_aliases MUST be empty [].
3. If match_status is "FULLY" or "PARTIAL", bd_flow_aliases MUST list 1 or more matching BD flow aliases (e.g. ["bf1"]).
4. Every activity alias (e.g. a1, a2, ...) in the batch MUST be included in the matches list exactly once.
5. Output pure valid JSON.

OUTPUT JSON SCHEMA (every field is required for every match):
{
  "matches": [
    {
      "activity_alias": "a1",
      "match_status": "FULLY",
      "bd_flow_aliases": ["bf3"],
      "confidence": 0.85,
      "reason": "1 short sentence explaining the match or why NONE."
    }
  ]
}
"""


async def _load_cluster_bd_flows(db: Any, cluster_id: str) -> list[dict[str, Any]]:
    """Load all BD business flows for the cluster."""
    async with db.execute(
        "SELECT id, ordinal, name, description, block_key FROM bd_business_flows WHERE cluster_id = ? ORDER BY ordinal",
        (cluster_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [
        dict(r) if hasattr(r, "keys") else {
            "id": r[0], "ordinal": r[1], "name": r[2], "description": r[3], "block_key": r[4]
        }
        for r in rows
    ]


async def _evaluate_activity_batch(
    batch_activities: list[dict[str, Any]],
    bd_flows: list[dict[str, Any]],
    steps_by_id: dict[str, dict[str, Any]],
    provider_id: str | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Match a single batch of <= 5 activities against BD flows."""
    # Build BD Catalog Registry
    bd_alias_to_id: dict[str, str] = {}
    bd_catalog_lines: list[str] = []
    for i, bf in enumerate(bd_flows, 1):
        alias = f"bf{i}"
        bd_alias_to_id[alias] = bf["id"]
        desc = bf.get("description") or "No description provided."
        bd_catalog_lines.append(f"{alias} | {bf['name']} | {desc}")

    # Build Activity Batch Registry
    act_alias_to_id: dict[str, str] = {}
    act_registry_lines: list[str] = []
    for i, act in enumerate(batch_activities, 1):
        alias = f"a{i}"
        act_alias_to_id[alias] = act["id"]

        member_ids = json.loads(act.get("member_step_ids_json") or "[]")
        member_steps = [steps_by_id[sid] for sid in member_ids if sid in steps_by_id]
        rep_texts = [
            (s.get("text_en") or s.get("text_ja") or "")
            for s in member_steps[:5]
            if (s.get("text_en") or s.get("text_ja"))
        ]
        sample_str = " | Sample steps: " + "; ".join(rep_texts) if rep_texts else ""

        act_registry_lines.append(
            f"{alias} | Name: {act['name_en']} | Summary: {act.get('summary_en') or ''}{sample_str}"
        )

    user_prompt = f"""BD FLOWS CATALOG ({len(bd_flows)} specification flows):
{chr(10).join(bd_catalog_lines) if bd_catalog_lines else "No BD flows found in cluster."}

USER ACTIVITIES TO MATCH ({len(batch_activities)} activities):
{chr(10).join(act_registry_lines)}

Evaluate each activity a1..a{len(batch_activities)}. Output JSON matching the schema.
"""

    if not provider_id or not bd_flows:
        # Offline or empty BD catalog fallback -> all NONE
        default_matches = [
            {
                "activity_id": act["id"],
                "bd_flow_ids": [],
                "match_status": "NONE",
                "confidence": 0.0,
                "reason": "offline_mode_or_no_bd_flows",
            }
            for act in batch_activities
        ]
        return default_matches, {"origin": "fallback", "reason": "offline"}

    def _build_request(effort: str) -> ChatRequest:
        return ChatRequest(
            provider_id=provider_id,
            messages=[
                ChatMessage(role="system", content=_TIER1_SYSTEM_PROMPT),
                ChatMessage(role="user", content=user_prompt),
            ],
            stream=True,
            max_completion_tokens=25000,
            temperature=0.0,
            json_mode=True,
            reasoning_effort=effort,
        )

    def _parse_and_validate(raw_text: str) -> LLMTier1MatchResponse:
        data = _parse_llm_json(raw_text)
        if "matches" not in data and isinstance(data, list):
            data = {"matches": data}
        res = LLMTier1MatchResponse.model_validate(data)

        # Exact coverage: every expected activity alias exactly once, no unknowns, no duplicates.
        assert_exact_id_coverage(
            [m.activity_alias for m in res.matches],
            set(act_alias_to_id.keys()),
            "Tier1 activity match",
        )

        # Validate bd_flow_aliases exist
        for m in res.matches:
            if m.match_status == "NONE" and m.bd_flow_aliases:
                raise ValueError("Tier1 activity match: NONE match cannot have bd_flow_aliases")
            for bfa in m.bd_flow_aliases:
                if bfa not in bd_alias_to_id:
                    raise ValueError(f"Tier1 activity match: unknown bd_flow_alias {bfa}")

        return res

    validated_resp: LLMTier1MatchResponse | None = None
    raw_text: str = ""

    async with _semaphore:
        try:
            ladder_res = await call_with_reasoning_ladder(
                _build_request,
                _parse_and_validate,
                first_effort="low",
                label=f"tier1_activity_match:{len(batch_activities)}acts",
                provider_id=provider_id,
            )
            if ladder_res is not None:
                validated_resp, raw_text = ladder_res
        except Exception as e:
            logger.error(f"[activity_match] Batch LLM call failed: {e}")

    if not validated_resp or not validated_resp.matches:
        # Total failure -> record NONE with LLM_NO_RESPONSE
        logger.warning("[activity_match] Batch matching failed. Falling back to NONE.")
        default_matches = [
            {
                "activity_id": act["id"],
                "bd_flow_ids": [],
                "match_status": "NONE",
                "confidence": 0.0,
                "reason": "LLM_NO_RESPONSE",
            }
            for act in batch_activities
        ]
        return default_matches, {"origin": "fallback", "reason": "LLM_NO_RESPONSE", "raw_text": raw_text}

    results: list[dict[str, Any]] = []
    for m in validated_resp.matches:
        if m.activity_alias not in act_alias_to_id:
            continue
        act_id = act_alias_to_id[m.activity_alias]
        real_bd_ids = [bd_alias_to_id[bfa] for bfa in m.bd_flow_aliases if bfa in bd_alias_to_id]
        results.append({
            "activity_id": act_id,
            "bd_flow_ids": real_bd_ids,
            "match_status": m.match_status,
            "confidence": m.confidence,
            "reason": m.reason,
        })

    return results, {"origin": "llm", "raw_text": raw_text}


async def match_user_activities_to_bd_flows(
    db: Any,
    doc_id: str,
    cluster_id: str,
    activities: list[dict[str, Any]],
    steps: list[dict[str, Any]],
    provider_id: str | None,
    run_id: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Execute LLM#6 Tier-1 Matching across all activities of a document (in batches <= 5).

    Persists results to `user_activity_matches` table.
    Returns:
        (persisted_matches_list, run_artifact_dict)
    """
    if not activities:
        return [], {"activities_count": 0, "matched_count": 0}

    bd_flows = await _load_cluster_bd_flows(db, cluster_id)
    steps_by_id = {s["id"]: s for s in steps}

    # Split activities into batches of <= BATCH_SIZE
    batches = [activities[i : i + BATCH_SIZE] for i in range(0, len(activities), BATCH_SIZE)]

    batch_results = await asyncio.gather(
        *[_evaluate_activity_batch(batch, bd_flows, steps_by_id, provider_id) for batch in batches]
    )

    all_matched_items: list[dict[str, Any]] = []
    batch_artifacts: list[dict[str, Any]] = []

    for b_results, b_artifact in batch_results:
        all_matched_items.extend(b_results)
        batch_artifacts.append(b_artifact)

    # Persist to user_activity_matches
    # Delete old matches for this run_id if re-running
    await db.execute("DELETE FROM user_activity_matches WHERE run_id = ?", (run_id,))

    rows_to_insert: list[dict[str, Any]] = []
    for item in all_matched_items:
        act_id = item["activity_id"]
        match_status = item["match_status"]
        conf = item["confidence"]
        reason = item["reason"]
        bd_flow_ids = item.get("bd_flow_ids") or []

        if match_status == "NONE" or not bd_flow_ids:
            row_id = f"uactm:{new_id()}"
            await db.execute(
                "INSERT INTO user_activity_matches (id, run_id, activity_id, bd_flow_id, match_status, confidence, reason, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (row_id, run_id, act_id, None, "NONE", conf, reason, utc_now_iso()),
            )
            rows_to_insert.append({
                "id": row_id,
                "run_id": run_id,
                "activity_id": act_id,
                "bd_flow_id": None,
                "match_status": "NONE",
                "confidence": conf,
                "reason": reason,
            })
        else:
            for bfid in bd_flow_ids:
                row_id = f"uactm:{new_id()}"
                await db.execute(
                    "INSERT INTO user_activity_matches (id, run_id, activity_id, bd_flow_id, match_status, confidence, reason, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (row_id, run_id, act_id, bfid, match_status, conf, reason, utc_now_iso()),
                )
                rows_to_insert.append({
                    "id": row_id,
                    "run_id": run_id,
                    "activity_id": act_id,
                    "bd_flow_id": bfid,
                    "match_status": match_status,
                    "confidence": conf,
                    "reason": reason,
                })

    await db.commit()

    return rows_to_insert, {
        "run_id": run_id,
        "doc_id": doc_id,
        "cluster_id": cluster_id,
        "total_activities": len(activities),
        "matches_count": len(rows_to_insert),
        "batch_artifacts": batch_artifacts,
    }
