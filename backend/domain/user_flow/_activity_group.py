"""Phase U2: User Activity Condensation Stage (LLM#5).

Condenses fine-grained user steps into business activities (1 LLM call per user_flow),
with exact step coverage validation and deterministic fallback.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict

from domain.business_flow_integrity._llm import call_with_reasoning_ladder
from domain.doc_graph._llm_citation import _parse_llm_json
from domain.model_connector.types import ChatMessage, ChatRequest
from shared.utils import new_id, utc_now_iso

logger = logging.getLogger("codespectra.user_flow.activity_group")

ACTIVITY_PROMPT_VERSION = "u2_act_v1"
ACTIVITY_SCHEMA_VERSION = "u2_act_v1"
_MAX_CONCURRENT_CONDENSATION = 5
_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_CONDENSATION)


# Schema tolerance (U-1 lesson): extra="ignore" + soft defaults — never reject a whole flow's
# condensation because the model added or omitted a cosmetic field.
class LLMActivitySpec(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name_en: str
    name_ja: str | None = None
    summary_en: str = ""
    member_step_aliases: list[str]
    ordinal: int = 0


class LLMCondensationResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    activities: list[LLMActivitySpec]


_CONDENSATION_SYSTEM_PROMPT = """You are an expert system architect analyzing customer test scenarios and user-flow specifications from spreadsheet workbooks.

Your task is to condense the provided fine-grained user steps of ONE user flow into high-level business activities (8-14 activities per large flow).

OUTPUT JSON SCHEMA:
{
  "activities": [
    {
      "name_en": "Descriptive Activity Name in English",
      "name_ja": "日本語のアクティビティ名",
      "summary_en": "1-2 sentences summarizing the business purpose and action performed.",
      "member_step_aliases": ["s1", "s2", "s3"],
      "ordinal": 1
    }
  ]
}

RULES:
1. EXACT COVERAGE: Every step alias from the Step Registry (e.g. s1, s2, ...) MUST appear in EXACTLY ONE activity. Do NOT omit any alias and do NOT include any alias more than once.
2. ALTITUDE: Group consecutive steps that belong to the same logical business operation (e.g. "Authenticate & Select Menu Option", "Configure Lot Search Criteria", "Validate Condition-Matrix Error Handling").
3. HINTS: Pay close attention to sheet boundaries and row-reset hints in the prompt — sheets represent distinct sub-tables.
4. ORDER: Order activities logically in execution sequence, assigning sequential 1-based ordinals (1, 2, 3...).
5. Respond ONLY in English for name_en and summary_en. Output pure valid JSON.
"""


def _strip_section_circled_digits(section_id: str | None) -> str:
    """Remove trailing circled number markers (①..⑳, parenthesized numbers) from section_id."""
    if not section_id or not section_id.strip():
        return "Other"
    cleaned = re.sub(r"[\u2460-\u2473\u3251-\u325f\u32b1-\u32bf\(\)\s]+$", "", section_id.strip()).strip()
    return cleaned or "Other"


def _build_deterministic_hints(steps: list[dict[str, Any]]) -> str:
    """Build deterministic prompt hints from sheet transitions, row resets, and section markers."""
    if not steps:
        return "No steps provided."

    hints: list[str] = []

    # 1. Sheet boundaries
    sheet_spans: list[tuple[str, int, int]] = []
    curr_sheet = steps[0].get("sheet") or "default"
    start_ix = 1

    for i, s in enumerate(steps, 1):
        s_sheet = s.get("sheet") or "default"
        if s_sheet != curr_sheet:
            sheet_spans.append((curr_sheet, start_ix, i - 1))
            curr_sheet = s_sheet
            start_ix = i
    sheet_spans.append((curr_sheet, start_ix, len(steps)))

    sheet_hints = [f"s{start}-s{end}: sheet '{sheet}'" for sheet, start, end in sheet_spans]
    hints.append("Sheet boundaries: " + "; ".join(sheet_hints))

    # 2. Row resets within sheets
    resets: list[str] = []
    prev_row = -1
    prev_sheet = None
    for i, s in enumerate(steps, 1):
        s_sheet = s.get("sheet")
        r_start = s.get("row_start")
        if r_start is not None:
            if prev_sheet == s_sheet and prev_row > 0 and r_start < prev_row - 10:
                resets.append(f"s{i} (sheet '{s_sheet}', row {r_start} restarts after row {prev_row})")
            prev_row = r_start
            prev_sheet = s_sheet

    if resets:
        hints.append("Row resets (possible sub-table restarts): " + "; ".join(resets))

    # 3. Cross-sheet section collision warning
    hints.append(
        "Note: Section numbers (e.g. 2.1①, ④) may repeat across different sheets with different meanings. "
        "Treat the sheet as the primary boundary."
    )

    return "\n".join(f"- {h}" for h in hints)


def _validate_condensation_coverage(
    validated: LLMCondensationResponse,
    alias_to_step_id: dict[str, str],
) -> str | None:
    """Validate that every step alias appears in exactly one activity. Returns None when valid,
    else a specific failure reason string for the caller to raise (so the ladder retries)."""
    used_aliases: set[str] = set()
    all_aliases = set(alias_to_step_id.keys())

    for act in validated.activities:
        for a in act.member_step_aliases:
            if a not in all_aliases:
                return f"LLM referenced unknown step alias: {a}"
            if a in used_aliases:
                return f"Step alias used multiple times: {a}"
            used_aliases.add(a)

    missing = all_aliases - used_aliases
    if missing:
        return f"LLM missed step aliases: {sorted(missing)}"

    return None


def _build_fallback_activities(
    flow_row: dict[str, Any],
    steps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Deterministic fallback: group steps by (sheet, stripped_top_section)."""
    flow_id = flow_row["id"]
    flow_name = flow_row.get("name_en") or flow_row.get("name_ja") or f"Flow {flow_row.get('ordinal', 1)}"

    # Bucket by (sheet, stripped_top_section)
    buckets: list[tuple[str, str, list[dict[str, Any]]]] = []
    curr_key: tuple[str, str] | None = None
    curr_list: list[dict[str, Any]] = []

    for s in steps:
        sheet = s.get("sheet") or "default"
        raw_sec = s.get("section_id")
        sec_key = _strip_section_circled_digits(raw_sec)

        # If section is Other and previous bucket was in same sheet, join it
        if sec_key == "Other" and curr_key is not None and curr_key[0] == sheet:
            curr_list.append(s)
            continue

        key = (sheet, sec_key)
        if key != curr_key:
            if curr_key is not None and curr_list:
                buckets.append((curr_key[0], curr_key[1], curr_list))
            curr_key = key
            curr_list = [s]
        else:
            curr_list.append(s)

    if curr_key is not None and curr_list:
        buckets.append((curr_key[0], curr_key[1], curr_list))

    # If no buckets formed, put all steps in one
    if not buckets:
        buckets = [("default", "All", steps)]

    activities: list[dict[str, Any]] = []
    for idx, (sheet, sec_key, b_steps) in enumerate(buckets, 1):
        member_ids = [s["id"] for s in sorted(b_steps, key=lambda x: x.get("ordinal", 0))]
        sheets_spanned = sorted(list({s.get("sheet") or "default" for s in b_steps}))

        r_starts = [s["row_start"] for s in b_steps if s.get("row_start") is not None]
        r_ends = [s["row_end"] for s in b_steps if s.get("row_end") is not None]

        act_name_en = f"{flow_name} - {sheet} ({sec_key})" if sec_key != "Other" else f"{flow_name} - {sheet}"

        activities.append({
            "id": f"uact:{new_id()}",
            "flow_id": flow_id,
            "ordinal": idx,
            "name_en": act_name_en,
            "name_ja": flow_row.get("name_ja"),
            "summary_en": f"Handles business operations for {sheet} section {sec_key} with {len(b_steps)} steps.",
            "member_step_ids_json": json.dumps(member_ids),
            "sheet_span_json": json.dumps(sheets_spanned),
            "step_count": len(b_steps),
            "action_count": sum(1 for s in b_steps if s.get("kind") == "action"),
            "error_rule_count": sum(1 for s in b_steps if s.get("kind") == "error_rule"),
            "expectation_count": sum(1 for s in b_steps if s.get("kind") == "expectation"),
            "row_start": min(r_starts) if r_starts else None,
            "row_end": max(r_ends) if r_ends else None,
            "origin": "fallback",
            "created_at": utc_now_iso(),
        })

    return activities


async def condense_flow_activities(
    db: Any,
    flow_row: dict[str, Any],
    steps: list[dict[str, Any]],
    provider_id: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Condense fine-grained steps of a single user flow into high-level business activities (LLM#5).

    Returns:
        (activities_list, run_artifact_dict)
    """
    flow_id = flow_row["id"]
    flow_name_en = flow_row.get("name_en") or flow_row.get("name_ja") or f"Flow {flow_row.get('ordinal', 1)}"

    if not steps:
        return [], {"flow_id": flow_id, "origin": "empty", "activities_count": 0}

    # If offline or no provider, use deterministic fallback
    if not provider_id:
        fallback_acts = _build_fallback_activities(flow_row, steps)
        return fallback_acts, {
            "flow_id": flow_id,
            "origin": "fallback",
            "activities_count": len(fallback_acts),
            "reason": "offline_mode",
        }

    # Build Step Alias Registry
    alias_to_step_id: dict[str, str] = {}
    alias_to_step: dict[str, dict[str, Any]] = {}
    registry_lines: list[str] = []

    sorted_steps = sorted(steps, key=lambda x: x.get("ordinal", 0))
    for i, s in enumerate(sorted_steps, 1):
        alias = f"s{i}"
        alias_to_step_id[alias] = s["id"]
        alias_to_step[alias] = s

        kind = s.get("kind") or "action"
        sheet = s.get("sheet") or ""
        sec = s.get("section_id") or ""
        text_raw = s.get("text_en") or s.get("text_ja") or ""
        text_short = (text_raw[:77] + "...") if len(text_raw) > 80 else text_raw

        extra_hints: list[str] = []
        if s.get("trigger_ja"):
            extra_hints.append(f"trig:{s['trigger_ja'][:25]}")
        if s.get("expected_ja"):
            extra_hints.append(f"exp:{s['expected_ja'][:25]}")
        hint_str = f" | {', '.join(extra_hints)}" if extra_hints else ""

        registry_lines.append(f"{alias} | {kind} | {sheet} | {sec} | {text_short}{hint_str}")

    hints_block = _build_deterministic_hints(sorted_steps)

    user_prompt = f"""FLOW METADATA:
Flow Name (EN): {flow_name_en}
Flow Name (JA): {flow_row.get('name_ja') or ''}
Source Sheet: {flow_row.get('sheet') or ''}
Scope Note: {flow_row.get('scope_note') or 'None'}

DETERMINISTIC HINTS:
{hints_block}

STEP REGISTRY ({len(sorted_steps)} steps):
{chr(10).join(registry_lines)}

Group these {len(sorted_steps)} steps into logical business activities. Every step alias s1..s{len(sorted_steps)} MUST appear in exactly one activity. Output pure JSON.
"""

    def _build_request(effort: str) -> ChatRequest:
        return ChatRequest(
            provider_id=provider_id,
            messages=[
                ChatMessage(role="system", content=_CONDENSATION_SYSTEM_PROMPT),
                ChatMessage(role="user", content=user_prompt),
            ],
            stream=True,
            max_completion_tokens=25000,
            temperature=0.0,
            json_mode=True,
            reasoning_effort=effort,
        )

    def _parse_and_validate(raw_text: str) -> LLMCondensationResponse:
        data = _parse_llm_json(raw_text)
        if "activities" not in data and isinstance(data, list):
            data = {"activities": data}
        res = LLMCondensationResponse.model_validate(data)
        failure_reason = _validate_condensation_coverage(res, alias_to_step_id)
        if failure_reason:
            logger.warning(f"[activity_group] Coverage validation failed: {failure_reason}")
            raise ValueError(f"activity condensation coverage: {failure_reason}")
        return res

    validated_resp: LLMCondensationResponse | None = None
    raw_text: str = ""

    async with _semaphore:
        try:
            ladder_res = await call_with_reasoning_ladder(
                _build_request,
                _parse_and_validate,
                first_effort="low",
                label=f"activity_condensation:{flow_id}",
                provider_id=provider_id,
            )
            if ladder_res is not None:
                validated_resp, raw_text = ladder_res
        except Exception as e:
            logger.error(f"[activity_group] LLM call failed for flow {flow_id}: {e}")

    if not validated_resp or not validated_resp.activities:
        logger.warning(f"[activity_group] LLM condensation failed/invalid for {flow_id}. Using fallback.")
        fallback_acts = _build_fallback_activities(flow_row, sorted_steps)
        return fallback_acts, {
            "flow_id": flow_id,
            "origin": "fallback",
            "activities_count": len(fallback_acts),
            "reason": "llm_validation_failed",
            "raw_text": raw_text,
        }

    # Transform validated response to user_activities rows
    activities: list[dict[str, Any]] = []
    for act_spec in validated_resp.activities:
        member_steps = [alias_to_step[a] for a in act_spec.member_step_aliases if a in alias_to_step]
        member_steps_sorted = sorted(member_steps, key=lambda x: x.get("ordinal", 0))
        member_ids = [s["id"] for s in member_steps_sorted]
        sheets_spanned = sorted(list({s.get("sheet") or "default" for s in member_steps_sorted}))

        r_starts = [s["row_start"] for s in member_steps_sorted if s.get("row_start") is not None]
        r_ends = [s["row_end"] for s in member_steps_sorted if s.get("row_end") is not None]

        activities.append({
            "id": f"uact:{new_id()}",
            "flow_id": flow_id,
            "ordinal": act_spec.ordinal,
            "name_en": act_spec.name_en,
            "name_ja": act_spec.name_ja or flow_row.get("name_ja"),
            "summary_en": act_spec.summary_en,
            "member_step_ids_json": json.dumps(member_ids),
            "sheet_span_json": json.dumps(sheets_spanned),
            "step_count": len(member_steps_sorted),
            "action_count": sum(1 for s in member_steps_sorted if s.get("kind") == "action"),
            "error_rule_count": sum(1 for s in member_steps_sorted if s.get("kind") == "error_rule"),
            "expectation_count": sum(1 for s in member_steps_sorted if s.get("kind") == "expectation"),
            "row_start": min(r_starts) if r_starts else None,
            "row_end": max(r_ends) if r_ends else None,
            "origin": "llm",
            "created_at": utc_now_iso(),
        })

    return activities, {
        "flow_id": flow_id,
        "origin": "llm",
        "activities_count": len(activities),
        "raw_text": raw_text,
    }
