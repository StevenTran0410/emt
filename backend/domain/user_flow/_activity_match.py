"""Phase U2: Tier-1 Activity Matcher Stage (LLM#6).

Matches high-level user activities against the cluster's BD business flows
(batch <= 5 activities per call) with FULLY | PARTIAL | NONE verdicts.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from domain.business_flow_integrity._llm import call_with_reasoning_ladder
from domain.doc_graph._llm_citation import _parse_llm_json
from domain.model_connector.types import ChatMessage, ChatRequest
from shared.utils import new_id, utc_now_iso

logger = logging.getLogger("codespectra.user_flow.activity_match")

TIER1_PROMPT_VERSION = "u2_match_v2"
TIER1_SCHEMA_VERSION = "u2_match_v2"
_MAX_CONCURRENT_MATCHING = 4
_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_MATCHING)
BATCH_SIZE = 5

MatchStatus = Literal["FULLY", "PARTIAL", "NONE", "UNRESOLVED"]


class LLMActivityMatchPair(BaseModel):
    model_config = ConfigDict(extra="ignore")
    activity_alias: str
    bd_flow_alias: str | None = None  # None for NONE status
    match_status: Literal["FULLY", "PARTIAL", "NONE"]
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reason: str = ""


class LLMTier1MatchResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    matches: list[LLMActivityMatchPair]


_TIER1_SYSTEM_PROMPT = """You are an expert systems auditor performing Tier-1 high-level reconciliation between customer user activities and specification Business Flows (BD Flows).

For each user activity in the batch, determine whether it corresponds to one or more specification BD Flows in the catalog:
- FULLY: The activity's business purpose is fully covered by the matched BD Flow.
- PARTIAL: The activity has substantial overlap with the matched BD Flow, but some customer requirements are not covered in the BD specification.
- NONE: The customer performs this activity, but the system specification (BD) has NO corresponding business flow for it.

CRITICAL RULES:
1. NONE is a legitimate, expected answer. If an activity describes functionality absent from the BD catalog, choose NONE.
2. If the activity plausibly relates to a flow but you are unsure, emit a PARTIAL pair with low confidence rather than NONE. Reserve NONE for activities with no plausible flow.
3. An activity may match multiple BD Flows. Emit one object per (activity, bd_flow) pair with its own match_status, confidence, and reason.
4. If an activity matches NO BD flows, emit exactly ONE object with bd_flow_alias: null and match_status: "NONE".
5. Do NOT mix NONE with FULLY/PARTIAL for the same activity. An activity has either exactly one NONE or one or more FULLY/PARTIAL pairs.
6. Every activity alias (e.g. a1, a2, ...) in the batch MUST have at least one match entry.
7. Output pure valid JSON.

OUTPUT JSON SCHEMA:
{
  "matches": [
    {
      "activity_alias": "a1",
      "bd_flow_alias": "bf3",
      "match_status": "FULLY",
      "confidence": 0.85,
      "reason": "Activity purpose matches BD flow bf3."
    },
    {
      "activity_alias": "a2",
      "bd_flow_alias": null,
      "match_status": "NONE",
      "confidence": 0.90,
      "reason": "No plausible specification flow in catalog."
    }
  ]
}
"""


async def _load_cluster_bd_flows(db: Any, cluster_id: str) -> list[dict[str, Any]]:
    """Load all BD business flows for the cluster with their step and branch rosters."""
    async with db.execute(
        "SELECT id, ordinal, name, description, block_key FROM bd_business_flows WHERE cluster_id = ? ORDER BY ordinal, id",
        (cluster_id,),
    ) as cur:
        rows = await cur.fetchall()

    flows: list[dict[str, Any]] = [
        dict(r) if hasattr(r, "keys") else {
            "id": r[0], "ordinal": r[1], "name": r[2], "description": r[3], "block_key": r[4]
        }
        for r in rows
    ]
    if not flows:
        return []

    flow_ids = [f["id"] for f in flows]
    placeholders = ",".join("?" for _ in flow_ids)

    # Load steps for catalog roster
    async with db.execute(
        f"SELECT id, flow_id, name, functionality, ordinal FROM bd_business_steps WHERE flow_id IN ({placeholders}) ORDER BY ordinal, id",
        flow_ids,
    ) as cur:
        step_rows = await cur.fetchall()

    steps_by_flow: dict[str, list[dict[str, Any]]] = {}
    for r in step_rows:
        s_d = dict(r) if hasattr(r, "keys") else {
            "id": r[0], "flow_id": r[1], "name": r[2], "functionality": r[3], "ordinal": r[4]
        }
        steps_by_flow.setdefault(s_d["flow_id"], []).append(s_d)

    # Load branches for catalog roster
    async with db.execute(
        f"SELECT id, flow_id, branch_kind, guard_description FROM bd_business_branches WHERE flow_id IN ({placeholders}) ORDER BY id",
        flow_ids,
    ) as cur:
        branch_rows = await cur.fetchall()

    branches_by_flow: dict[str, list[dict[str, Any]]] = {}
    for r in branch_rows:
        b_d = dict(r) if hasattr(r, "keys") else {
            "id": r[0], "flow_id": r[1], "branch_kind": r[2], "guard_description": r[3]
        }
        branches_by_flow.setdefault(b_d["flow_id"], []).append(b_d)

    for f in flows:
        f["steps"] = steps_by_flow.get(f["id"], [])
        f["branches"] = branches_by_flow.get(f["id"], [])

    return flows


async def _evaluate_activity_batch(
    batch_activities: list[dict[str, Any]],
    bd_flows: list[dict[str, Any]],
    steps_by_id: dict[str, dict[str, Any]],
    provider_id: str | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Match a single batch of <= 5 activities against BD flows."""
    # Build BD Catalog Registry with enriched step/branch roster
    bd_alias_to_id: dict[str, str] = {}
    bd_catalog_lines: list[str] = []
    for i, bf in enumerate(bd_flows, 1):
        alias = f"bf{i}"
        bd_alias_to_id[alias] = bf["id"]
        desc = bf.get("description") or "No description provided."

        step_roster: list[str] = []
        for s in bf.get("steps", []):
            s_name = s.get("name") or ""
            s_func = s.get("functionality") or ""
            if s_func and s_func != s_name:
                step_roster.append(f"{s_name} ({s_func})")
            elif s_name:
                step_roster.append(s_name)

        branch_roster: list[str] = []
        for b in bf.get("branches", []):
            b_kind = (b.get("branch_kind") or "").lower()
            b_guard = b.get("guard_description") or ""
            if b_kind not in ("success", "normal") and b_guard:
                branch_roster.append(f"[{b.get('branch_kind')}] {b_guard}")

        roster_items: list[str] = []
        if step_roster:
            roster_items.append("Steps: " + "; ".join(step_roster))
        if branch_roster:
            roster_items.append("Non-SUCCESS branches: " + "; ".join(branch_roster))

        roster_str = ("\n    - " + "\n    - ".join(roster_items)) if roster_items else ""
        bd_catalog_lines.append(f"{alias} | {bf['name']} | {desc}{roster_str}")

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

    if not provider_id:
        # No provider means the question was never asked. NONE claims "the specification has no
        # such flow" — a finding that would go on to manufacture BD_EXTRA — so stay UNRESOLVED.
        return [
            {
                "activity_id": act["id"],
                "bd_flow_id": None,
                "match_status": "UNRESOLVED",
                "confidence": 0.0,
                "reason": "NO_PROVIDER",
            }
            for act in batch_activities
        ], {"origin": "fallback", "reason": "NO_PROVIDER"}

    if not bd_flows:
        # An empty catalog is a real answer: there is genuinely no BD flow to match against.
        return [
            {
                "activity_id": act["id"],
                "bd_flow_id": None,
                "match_status": "NONE",
                "confidence": 0.0,
                "reason": "no_bd_flows_in_cluster",
            }
            for act in batch_activities
        ], {"origin": "fallback", "reason": "no_bd_flows_in_cluster"}

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

        # Invariant 1: Group matches by activity_alias
        expected_act_aliases = set(act_alias_to_id.keys())
        expected_flow_aliases = set(bd_alias_to_id.keys())

        grouped: dict[str, list[LLMActivityMatchPair]] = {}
        for m in res.matches:
            if m.activity_alias not in expected_act_aliases:
                raise ValueError(f"Tier1 activity match: unknown activity_alias '{m.activity_alias}'")
            grouped.setdefault(m.activity_alias, []).append(m)

        # Invariant 2: Every expected activity alias must appear in matches (>= 1 row)
        missing_acts = expected_act_aliases - set(grouped.keys())
        if missing_acts:
            raise ValueError(f"Tier1 activity match: missing expected activities: {sorted(missing_acts)}")

        # Invariants 3-7: Per activity invariants
        for act_alias, items in grouped.items():
            seen_flows: set[str | None] = set()
            for it in items:
                flow_key = it.bd_flow_alias.strip() if isinstance(it.bd_flow_alias, str) and it.bd_flow_alias.strip() else None
                if flow_key in seen_flows:
                    raise ValueError(f"Tier1 activity match: duplicate (activity, flow) pair for {act_alias}, {flow_key}")
                seen_flows.add(flow_key)

                # Finite float confidence [0, 1]
                if not isinstance(it.confidence, (int, float)) or it.confidence < 0.0 or it.confidence > 1.0:
                    it.confidence = max(0.0, min(1.0, float(it.confidence)))

            has_none = any(it.match_status == "NONE" for it in items)
            has_positive = any(it.match_status in ("FULLY", "PARTIAL") for it in items)

            # Exactly one NONE row XOR >=1 positive rows (never both)
            if has_none and has_positive:
                raise ValueError(f"Tier1 activity match: activity {act_alias} contains both NONE and FULLY/PARTIAL matches")

            if has_none:
                if len(items) != 1:
                    raise ValueError(f"Tier1 activity match: activity {act_alias} with NONE match has multiple entries ({len(items)})")
                it = items[0]
                if it.bd_flow_alias is not None and str(it.bd_flow_alias).strip().lower() not in ("", "null", "none"):
                    raise ValueError(f"Tier1 activity match: activity {act_alias} with NONE status must have null bd_flow_alias")
            else:
                for it in items:
                    if not it.bd_flow_alias:
                        raise ValueError(f"Tier1 activity match: activity {act_alias} positive match {it.match_status} requires non-null bd_flow_alias")
                    if it.bd_flow_alias not in expected_flow_aliases:
                        raise ValueError(f"Tier1 activity match: activity {act_alias} references unknown bd_flow_alias '{it.bd_flow_alias}'")

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
        # Total failure -> record UNRESOLVED with LLM_NO_RESPONSE (honest failure state)
        logger.warning("[activity_match] Batch matching failed. Falling back to UNRESOLVED.")
        default_matches = [
            {
                "activity_id": act["id"],
                "bd_flow_id": None,
                "match_status": "UNRESOLVED",
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
        real_bd_id = bd_alias_to_id[m.bd_flow_alias] if m.bd_flow_alias and m.bd_flow_alias in bd_alias_to_id else None
        results.append({
            "activity_id": act_id,
            "bd_flow_id": real_bd_id,
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
        bfid = item.get("bd_flow_id")

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
