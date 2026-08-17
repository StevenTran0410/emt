"""Outline and Step-level Completeness stages for User Flow structuring (TICKET UR + UR-FIX).

- Outline stage (build_outline, 1 call): whole-workbook compact overview returning ~10-20 top-level flows with exact row spans and section markers.
- Deterministic outline gate with targeted repair and fragmentation coarsening.
- FlowUnit carries flow_group_key for multi-span flow aggregation.
- Step-level completeness stage (check_step_completeness, 1 call): sweeps final step-row ledger against all semantic workbook rows.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from domain.business_flow_integrity._llm import (
    call_with_reasoning_ladder,
    coerce_results_wrapper,
)
from domain.doc_graph._llm_citation import _parse_llm_json
from domain.model_connector.types import ChatMessage, ChatRequest
from shared.logger import logger
from shared.utils import new_id

from ._extract import Block, RawRow, SheetExtract, WorkbookExtract

OUTLINE_PAD = 2


@dataclass
class FlowUnit:
    unit_id: str
    sheet: str
    kind: str  # 'narrative' | 'case_group' | 'message'
    name_ja: str
    name_en: str | None
    row_start: int  # padded row start
    row_end: int    # padded row end
    raw_row_start: int  # unpadded
    raw_row_end: int    # unpadded
    flow_group_key: str = ""  # outline flow_alias (e.g. 'f1')
    section_markers: list[str] = field(default_factory=list)
    scope_note: str | None = None
    in_scope: bool = True


@dataclass
class OutlineArtifact:
    agent_raw: str
    retry_count: int
    latency_ms: float
    success: bool
    error: str | None
    total_flows: int
    repaired_items: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Outline Models & Prompts
# ---------------------------------------------------------------------------

class OutlineSpan(BaseModel):
    model_config = ConfigDict(extra="ignore")
    sheet_id: str
    row_start: int
    row_end: int


class OutlineFlowItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    flow_alias: str
    name_ja: str
    name_en: str | None = None
    spans: list[OutlineSpan] = Field(default_factory=list)
    section_markers: list[str] = Field(default_factory=list)
    kind: str = "narrative"  # 'narrative' | 'case_group' | 'message'
    in_scope: bool = True
    scope_note: str | None = None


class OutlineResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    results: list[OutlineFlowItem]


_OUTLINE_SYSTEM_PROMPT = """You are an expert system analyzing Japanese Excel test/flow scenario workbooks from legacy mainframe modernization projects.
You see a compact structural overview of a WHOLE Japanese test/flow workbook (truncated semantic rows + indent levels across relevant sheets).

Your task is to identify the TOP-LEVEL business flows in the workbook.
A typical scenario workbook contains ~10-20 top-level business flows (NOT one flow per row or block).

INSTRUCTIONS:
1. Group rows describing the same operator task or test scenario into ONE business flow.
2. For each flow, output:
   - `flow_alias`: "f1", "f2", ...
   - `name_ja`: Verbatim Japanese name/title of the flow (e.g. "コイル試験指示入力フロー").
   - `name_en`: Clean, business-readable English gloss (e.g. "Coil Test Entry Flow").
   - `kind`: "narrative" (for outline/narrative sheets), "case_group" (for test case tables), or "message" (for message lists).
   - `spans`: List of exact row ranges `[{"sheet_id": "s1", "row_start": 1, "row_end": 25}]` owned by this flow. A flow may span multiple row ranges if broken by headers.
   - `section_markers`: Verbatim section identifiers found in the rows (e.g. "2.1", "2.1①", "3.1", "3.2").
   - `in_scope`: True, unless explicitly noted as out of scope (e.g. "※PoCとしては対象外").
   - `scope_note`: Any explicit scope exclusions or notes.
3. DO NOT extract step-by-step details. Focus strictly on top-level flow boundaries and spans.
4. Do NOT emit a flow for every block or row. Coarse grouping is required.

OUTPUT FORMAT:
Respond with a JSON object strictly matching this schema:
{
  "results": [
    {
      "flow_alias": "f1",
      "name_ja": "コイル試験指示入力フロー",
      "name_en": "Coil Test Entry Flow",
      "kind": "narrative",
      "spans": [
        {
          "sheet_id": "s1",
          "row_start": 1,
          "row_end": 25
        }
      ],
      "section_markers": ["2.1①", "2.1②"],
      "in_scope": true,
      "scope_note": null
    }
  ]
}
"""


async def build_outline(
    blocks_by_sheet: list[tuple[SheetExtract, str]],
    provider_id: str,
    doc_id: str | None = None,
) -> tuple[list[FlowUnit], OutlineArtifact]:
    """Execute Stage A: 1-call whole-workbook outline extraction with deterministic gating & repair."""
    # 1. Build sheet alias mapping and compact row registry
    sheet_alias_map: dict[str, SheetExtract] = {}
    sheet_name_to_alias: dict[str, str] = {}
    sheet_class_map: dict[str, str] = {}

    all_semantic_rows: list[dict[str, Any]] = []
    sheet_min_max_rows: dict[str, tuple[int, int]] = {}

    for idx, (s_extract, s_class) in enumerate(blocks_by_sheet, start=1):
        s_alias = f"s{idx}"
        sheet_alias_map[s_alias] = s_extract
        sheet_name_to_alias[s_extract.sheet] = s_alias
        sheet_class_map[s_alias] = s_class

        min_r = 999999
        max_r = 0
        seen_row_ix: set[int] = set()

        for b in s_extract.blocks:
            for r in b.rows:
                if r.row_ix in seen_row_ix:
                    continue
                seen_row_ix.add(r.row_ix)
                min_r = min(min_r, r.row_ix)
                max_r = max(max_r, r.row_ix)

                # Concatenate non-empty cells
                cell_vals = [c.value.strip() for c in r.cells if c.value and c.value.strip()]
                row_text = " | ".join(cell_vals)
                if len(row_text) > 80:
                    row_text = row_text[:80] + "…"

                all_semantic_rows.append({
                    "sheet_alias": s_alias,
                    "sheet_name": s_extract.sheet,
                    "sheet_class": s_class,
                    "row_ix": r.row_ix,
                    "indent": r.indent_col,
                    "text_60": row_text,
                })

        if max_r > 0:
            sheet_min_max_rows[s_alias] = (min_r, max_r)

    # Build prompt representation of all semantic rows
    prompt_lines: list[str] = ["WORKBOOK SHEETS & ROW REGISTRY:"]
    for s_alias, s_extract in sheet_alias_map.items():
        s_class = sheet_class_map[s_alias]
        prompt_lines.append(f"\n--- SHEET {s_alias}: '{s_extract.sheet}' (Class: {s_class}) ---")
        sheet_rows = [r for r in all_semantic_rows if r["sheet_alias"] == s_alias]
        for sr in sheet_rows:
            prompt_lines.append(f"[r{sr['row_ix']}, ind:{sr['indent']}] {sr['text_60']}")

    user_prompt = "\n".join(prompt_lines)

    # 2. Invoke LLM with HIGH reasoning tier
    attempts = {"n": 0}

    def _build_req(effort: str) -> ChatRequest:
        attempts["n"] += 1
        return ChatRequest(
            provider_id=provider_id,
            messages=[
                ChatMessage(role="system", content=_OUTLINE_SYSTEM_PROMPT),
                ChatMessage(role="user", content=user_prompt),
            ],
            stream=True,
            max_completion_tokens=25000,
            temperature=0.0,
            json_mode=True,
            reasoning_effort=effort,
        )

    def _parse(text: str) -> list[OutlineFlowItem]:
        parsed = coerce_results_wrapper(_parse_llm_json(text))
        validated = OutlineResponse.model_validate(parsed)
        return validated.results

    start = time.monotonic()
    ladder_res = await call_with_reasoning_ladder(
        _build_req, _parse, first_effort="low", label="UserFlow Outline (LLM#1 Outline)",
        provider_id=provider_id,
    )
    latency_ms = round((time.monotonic() - start) * 1000, 1)
    retry_count = max(attempts["n"] - 1, 0)

    repaired_items: list[str] = []
    warnings: list[str] = []

    # 3. Deterministic Gate & Targeted Repair
    flow_items: list[OutlineFlowItem] = []
    raw_text = ""

    if ladder_res is not None:
        flow_items, raw_text = ladder_res
    else:
        warnings.append("OUTLINE_LADDER_FAILED: generating coarse whole-sheet fallback flows")

    # If LLM returned empty or failed, generate 1 coarse flow per sheet (never 1-per-block)
    if not flow_items:
        for s_alias, s_extract in sheet_alias_map.items():
            min_r, max_r = sheet_min_max_rows.get(s_alias, (1, 10))
            s_class = sheet_class_map[s_alias]
            kind = "narrative" if "narrative" in s_class else ("case_group" if "case" in s_class else "message")
            flow_items.append(
                OutlineFlowItem(
                    flow_alias=f"f_{s_alias}",
                    name_ja=s_extract.sheet,
                    name_en=f"{s_extract.sheet} Flow",
                    kind=kind,
                    spans=[OutlineSpan(sheet_id=s_alias, row_start=min_r, row_end=max_r)],
                    section_markers=[],
                    in_scope=True,
                )
            )

    # Check and mitigate implausible fragmentation
    total_semantic = len(all_semantic_rows)
    max_plausible_flows = max(6, total_semantic // 4)
    if total_semantic > 0 and len(flow_items) > max_plausible_flows:
        warnings.append(f"IMPLAUSIBLE_FRAGMENTATION: {len(flow_items)} flows for {total_semantic} semantic rows; coarsening")
        # Deterministically merge flows by identical name_ja & sheet
        merged_by_name: dict[str, OutlineFlowItem] = {}
        for it in flow_items:
            sh_id = it.spans[0].sheet_id if it.spans else "s1"
            m_key = f"{it.name_ja.strip()}_{sh_id}"
            if m_key not in merged_by_name:
                merged_by_name[m_key] = it
            else:
                existing = merged_by_name[m_key]
                existing.spans.extend(it.spans)
                existing.section_markers.extend(it.section_markers)
                repaired_items.append(f"Coarsened/merged fragmented flow '{it.name_ja}' into '{existing.flow_alias}'")
        flow_items = list(merged_by_name.values())

    # Gate each flow item
    flow_units: list[FlowUnit] = []
    seen_names: set[str] = set()

    for item in flow_items:
        # Normalize kind
        k_lower = (item.kind or "").lower()
        if "case" in k_lower:
            norm_kind = "case_group"
        elif "msg" in k_lower or "message" in k_lower:
            norm_kind = "message"
        else:
            norm_kind = "narrative"

        # Validate name_ja
        name_ja = (item.name_ja or "").strip()
        if not name_ja:
            name_ja = "ユーザーフロー"
            repaired_items.append(f"Fixed empty name_ja on {item.flow_alias}")

        # Deduplicate identical flow names
        if name_ja in seen_names:
            name_ja = f"{name_ja} ({len(flow_units) + 1})"
            repaired_items.append(f"Deduplicated flow name on {item.flow_alias} -> {name_ja}")
        seen_names.add(name_ja)

        # Validate spans
        valid_spans: list[tuple[str, int, int]] = []
        for sp in item.spans:
            sh_id = sp.sheet_id
            if sh_id not in sheet_alias_map:
                # Try to map by sheet name
                if sh_id in sheet_name_to_alias:
                    sh_id = sheet_name_to_alias[sh_id]
                else:
                    # Default to first sheet
                    sh_id = list(sheet_alias_map.keys())[0] if sheet_alias_map else "s1"
                    repaired_items.append(f"Remapped unknown sheet_id '{sp.sheet_id}' to '{sh_id}' on {item.flow_alias}")

            min_bound, max_bound = sheet_min_max_rows.get(sh_id, (1, 100))
            r_start = max(1, min(sp.row_start, max_bound))
            r_end = max(r_start, min(sp.row_end, max_bound))
            valid_spans.append((sh_id, r_start, r_end))

        if not valid_spans:
            first_sh = list(sheet_alias_map.keys())[0] if sheet_alias_map else "s1"
            min_bound, max_bound = sheet_min_max_rows.get(first_sh, (1, 100))
            valid_spans.append((first_sh, min_bound, max_bound))
            repaired_items.append(f"Created default span for {item.flow_alias}")

        # Filter section markers to those actually present in the registry
        valid_markers = []
        for sm in item.section_markers:
            sm_clean = str(sm).strip()
            if any(sm_clean in r["text_60"] for r in all_semantic_rows):
                valid_markers.append(sm_clean)
            else:
                repaired_items.append(f"Dropped invented section marker '{sm_clean}' on {item.flow_alias}")

        # Build FlowUnits (one unit per span for extraction window, carrying flow_group_key for assembly)
        for sh_id, raw_start, raw_end in valid_spans:
            s_extract = sheet_alias_map[sh_id]
            pad_start = max(1, raw_start - OUTLINE_PAD)
            pad_end = raw_end + OUTLINE_PAD

            flow_units.append(
                FlowUnit(
                    unit_id=f"uf:{new_id()}",
                    sheet=s_extract.sheet,
                    kind=norm_kind,
                    name_ja=name_ja,
                    name_en=item.name_en or name_ja,
                    row_start=pad_start,
                    row_end=pad_end,
                    raw_row_start=raw_start,
                    raw_row_end=raw_end,
                    flow_group_key=item.flow_alias,
                    section_markers=valid_markers,
                    scope_note=item.scope_note,
                    in_scope=item.in_scope,
                )
            )

    outline_artifact = OutlineArtifact(
        agent_raw=raw_text,
        retry_count=retry_count,
        latency_ms=latency_ms,
        success=ladder_res is not None,
        error=None if ladder_res is not None else "OUTLINE_LADDER_FAILED",
        total_flows=len(flow_units),
        repaired_items=repaired_items,
        warnings=warnings,
    )

    return flow_units, outline_artifact


# ---------------------------------------------------------------------------
# Step-Level Completeness (Stage D)
# ---------------------------------------------------------------------------

@dataclass
class CompletenessArtifact:
    produced_step_count: int
    total_semantic_rows: int
    uncovered_rows_count: int
    missing_spans_count: int
    recovered_count: int
    missing_spans: list[dict[str, Any]] = field(default_factory=list)
    agent_raw: str = ""
    retry_count: int = 0
    latency_ms: float = 0.0


class MissingSpanItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    sheet_name: str
    row_start: int
    row_end: int
    flow_name_ja: str | None = None
    reason: str = ""


class CompletenessResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    missing_spans: list[MissingSpanItem] = Field(default_factory=list)


_COMPLETENESS_SYSTEM_PROMPT = """You are an expert completeness auditor for Japanese Excel test/flow scenario workbooks.
You are given a list of semantic rows that were NOT captured by any extracted user steps.
Analyze whether any of these uncovered rows describe valid operator actions, expectations, or error handling rules that should be recovered.

Ignore administrative headers, empty spacer rows, and redundant title labels.
Output only genuinely missing step/case ranges.

Output Format:
{
  "missing_spans": [
    {
      "sheet_name": "想定表",
      "row_start": 14,
      "row_end": 15,
      "flow_name_ja": "コイル試験指示入力フロー",
      "reason": "Missing expected confirmation message step"
    }
  ]
}
"""


async def check_step_completeness(
    step_ledger: set[tuple[str, int]],
    all_semantic_rows: list[dict[str, Any]],
    flows: list[FlowUnit],
    provider_id: str,
) -> tuple[list[MissingSpanItem], CompletenessArtifact]:
    """Execute Stage D: Compare final step-row ledger against all semantic rows to find and recover missing steps."""
    uncovered_rows = [
        r for r in all_semantic_rows
        if (r["sheet_name"], r["row_ix"]) not in step_ledger
    ]

    total_semantic = len(all_semantic_rows)
    uncovered_count = len(uncovered_rows)

    if not uncovered_rows or provider_id is None:
        return [], CompletenessArtifact(
            produced_step_count=len(step_ledger),
            total_semantic_rows=total_semantic,
            uncovered_rows_count=uncovered_count,
            missing_spans_count=0,
            recovered_count=0,
            missing_spans=[],
        )

    # Format uncovered rows prompt
    lines = ["UNCOVERED SEMANTIC ROWS:"]
    for ur in uncovered_rows:
        lines.append(f"Sheet '{ur['sheet_name']}' [r{ur['row_ix']}, ind:{ur['indent']}] {ur['text_60']}")

    attempts = {"n": 0}

    def _build_req(effort: str) -> ChatRequest:
        attempts["n"] += 1
        return ChatRequest(
            provider_id=provider_id,
            messages=[
                ChatMessage(role="system", content=_COMPLETENESS_SYSTEM_PROMPT),
                ChatMessage(role="user", content="\n".join(lines)),
            ],
            stream=True,
            max_completion_tokens=25000,
            temperature=0.0,
            json_mode=True,
            reasoning_effort=effort,
        )

    def _parse(text: str) -> list[MissingSpanItem]:
        parsed = _parse_llm_json(text)
        validated = CompletenessResponse.model_validate(parsed)
        return validated.missing_spans

    start = time.monotonic()
    ladder_res = await call_with_reasoning_ladder(
        _build_req, _parse, first_effort="low", label="UserFlow Completeness (LLM#1d)",
        provider_id=provider_id,
    )
    latency_ms = round((time.monotonic() - start) * 1000, 1)
    retry_count = max(attempts["n"] - 1, 0)

    missing_spans: list[MissingSpanItem] = []
    raw_text = ""
    if ladder_res:
        missing_spans, raw_text = ladder_res

    missing_dicts = [
        {"sheet_name": ms.sheet_name, "row_start": ms.row_start, "row_end": ms.row_end, "reason": ms.reason}
        for ms in missing_spans
    ]

    artifact = CompletenessArtifact(
        produced_step_count=len(step_ledger),
        total_semantic_rows=total_semantic,
        uncovered_rows_count=uncovered_count,
        missing_spans_count=len(missing_spans),
        recovered_count=0,
        missing_spans=missing_dicts,
        agent_raw=raw_text,
        retry_count=retry_count,
        latency_ms=latency_ms,
    )

    return missing_spans, artifact
