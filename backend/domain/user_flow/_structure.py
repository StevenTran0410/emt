"""Phase U User Flow Structuring Orchestrator (TICKET UR + UR-FIX).

Orchestrates:
1. Sheet Classifier (1 call, HIGH reasoning).
2. Outline stage (1 call, HIGH reasoning, compact whole-workbook row registry).
3. Detail & Corrector batches (LOW reasoning detail + HIGH reasoning patch corrector).
4. Step-level Completeness Sweep & Bounded Recovery (1 call).
5. Deterministic Assembly & Deduplication into database tables (aggregating multi-span flows into 1 user_flows row).
"""
from __future__ import annotations

import asyncio
import json
import re
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

from ._detail import (
    CaseItem,
    DetailResult,
    PatchOp,
    StepItem,
    UnitProcessingArtifact,
    compute_uncovered_rows,
    correct_detail_batch,
    extract_detail_batch,
)
from ._extract import Block, RawRow, SheetExtract, WorkbookExtract
from ._skeleton import (
    CompletenessArtifact,
    FlowUnit,
    MissingSpanItem,
    OutlineArtifact,
    build_outline,
    check_step_completeness,
)

BIG_FLOW_STEPS = 25
BIG_FLOW_ROWS = 50


def canonicalize_flow_name(name: str) -> str:
    """Strip trailing continuation markers and deduplication indices e.g. (16), (2), (その2), (続), 【続】."""
    if not name:
        return ""
    cleaned = name.strip()
    cleaned = re.sub(r'[\s_\-]*[\(（](?:\d+|その\d+|続|part\s*\d+)[\)）]$', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'[\s_\-]*【(?:続|その\d+)】$', '', cleaned)
    cleaned = re.sub(r'[\s_\-]*-\s*part\s*\d+$', '', cleaned, flags=re.IGNORECASE)
    return cleaned.strip()



# ---------------------------------------------------------------------------
# Sheet Classifier Models & Prompts
# ---------------------------------------------------------------------------

class SheetClassItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    sheet_id: str
    sheet_class: Literal[
        "narrative_flow",
        "case_table",
        "raw_source",
        "flowchart",
        "diagram",
        "administrative",
        "data_dictionary",
    ]
    reason: str


class SheetClassResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    results: list[SheetClassItem]


@dataclass
class SheetClassifierArtifact:
    agent_raw: str
    retry_count: int
    latency_ms: float
    success: bool
    error: str | None
    classifications: dict[str, str] = field(default_factory=dict)


_CLASSIFIER_SYSTEM_PROMPT = """You are an expert legacy modernisation analyst inspecting a Japanese Excel test/flow scenario workbook.
Your job is to classify EACH worksheet into one of the following categories:
- narrative_flow: Structured operator-level test scenario with steps, expectations, and section numbers (e.g. 想定表, 業務フロー).
- case_table: Matrix of test cases, input condition combinations, and expected output values (e.g. テスト項目表, 項目一覧).
- raw_source: Direct copy-paste of COBOL, CLIST, JCL, SQL, or source files into cells (e.g. HNIXLOT_CBL, PHNIXLOT_CLIST).
- flowchart: Flow diagrams, ASCII/shape workflows, or block diagrams (e.g. 処理フロー図).
- diagram: Screen layout mockups, panel definitions, or architecture diagrams.
- data_dictionary: Field layout lists, record layouts, or variable definitions.
- administrative: Cover sheet, revision history, table of contents, or memo notes.

Return a JSON object with:
{
  "results": [
    {
      "sheet_id": "s1",
      "sheet_class": "narrative_flow",
      "reason": "Contains sequential test scenario steps with 2.1 section markers."
    }
  ]
}
Every sheet_id from the input MUST appear exactly once in the results list.
"""


async def classify_sheets(
    workbook: WorkbookExtract,
    provider_id: str,
    doc_id: str | None = None,
) -> tuple[dict[str, str], SheetClassifierArtifact]:
    """Execute Stage A: Sheet classifier (1 call over all sheets in the workbook)."""
    sheet_summaries: list[dict[str, Any]] = []
    alias_to_name: dict[str, str] = {}

    for idx, s in enumerate(workbook.sheets, start=1):
        alias = f"s{idx}"
        alias_to_name[alias] = s.sheet

        sample_rows: list[str] = []
        for b in s.blocks[:3]:
            for r in b.rows[:5]:
                vals = [c.value for c in r.cells if c.value]
                if vals:
                    sample_rows.append(" | ".join(vals[:6])[:120])

        sheet_summaries.append({
            "sheet_id": alias,
            "name": s.sheet,
            "hidden": s.hidden,
            "total_rows": s.n_rows,
            "total_cols": s.n_cols,
            "block_count": len(s.blocks),
            "sample_content": "\n".join(sample_rows),
        })

    user_prompt = "WORKSHEETS TO CLASSIFY:\n" + json.dumps(sheet_summaries, ensure_ascii=False, indent=2)

    attempts = {"n": 0}

    def _build_req(effort: str) -> ChatRequest:
        attempts["n"] += 1
        return ChatRequest(
            provider_id=provider_id,
            messages=[
                ChatMessage(role="system", content=_CLASSIFIER_SYSTEM_PROMPT),
                ChatMessage(role="user", content=user_prompt),
            ],
            stream=True,
            max_completion_tokens=25000,
            temperature=0.0,
            json_mode=True,
            reasoning_effort=effort,
        )

    def _parse(text: str) -> list[SheetClassItem]:
        parsed = coerce_results_wrapper(_parse_llm_json(text))
        validated = SheetClassResponse.model_validate(parsed)
        expected_ids = {s["sheet_id"] for s in sheet_summaries}
        actual_ids = [item.sheet_id for item in validated.results]
        assert_exact_id_coverage(actual_ids, expected_ids, label="sheet classifier")
        return validated.results

    start = time.monotonic()
    ladder_res = await call_with_reasoning_ladder(
        _build_req, _parse, first_effort="low", label="UserFlow Sheet Classifier (LLM#1a)",
        provider_id=provider_id,
    )
    latency_ms = round((time.monotonic() - start) * 1000, 1)
    retry_count = max(attempts["n"] - 1, 0)

    class_map: dict[str, str] = {}
    raw_text = ""

    if ladder_res is not None:
        items, raw_text = ladder_res
        for it in items:
            name = alias_to_name.get(it.sheet_id)
            if name:
                class_map[name] = it.sheet_class

    # Deterministic fallback for any missing sheet
    for s in workbook.sheets:
        if s.sheet not in class_map:
            s_lower = s.sheet.lower()
            if s.hidden:
                class_map[s.sheet] = "administrative"
            elif any(k in s_lower for k in ["想定", "フロー", "シナリオ", "flow", "scenario"]):
                class_map[s.sheet] = "narrative_flow"
            elif any(k in s_lower for k in ["項目", "テスト", "case", "test"]):
                class_map[s.sheet] = "case_table"
            else:
                class_map[s.sheet] = "narrative_flow"

    artifact = SheetClassifierArtifact(
        agent_raw=raw_text,
        retry_count=retry_count,
        latency_ms=latency_ms,
        success=ladder_res is not None,
        error=None if ladder_res is not None else "CLASSIFIER_LADDER_FAILED",
        classifications=class_map,
    )

    return class_map, artifact


# ---------------------------------------------------------------------------
# Pipeline Result & Orchestrator
# ---------------------------------------------------------------------------

@dataclass
class StructureResult:
    flows: list[dict[str, Any]]
    steps: list[dict[str, Any]]
    cases: list[dict[str, Any]]
    outline_artifact: OutlineArtifact | None = None
    unit_artifacts: list[UnitProcessingArtifact] = field(default_factory=list)
    completeness_artifact: CompletenessArtifact | None = None
    logical_calls: int = 0       # real import-stage LLM batch count (outline+detail+corrector+completeness+recovery)
    physical_attempts: int = 0   # logical + ladder retries actually issued to the provider


async def structure_user_flow(
    blocks_by_sheet: list[tuple[SheetExtract, str]],
    provider_id: str | None,
    doc_id: str,
) -> StructureResult:
    """Structure relevant sheets using the Lean Re-Architected pipeline (Outline -> Detail -> Corrector -> Completeness)."""
    if provider_id is None:
        logger.warning("offline: user flow structuring skipped (provider_id=None)")
        return StructureResult(flows=[], steps=[], cases=[])

    sheets_by_name: dict[str, SheetExtract] = {
        s_extract.sheet: s_extract for s_extract, _ in blocks_by_sheet
    }

    # Real LLM-call counters (logical batches + physical provider attempts incl. ladder retries)
    llm_logical = 1  # the outline call
    llm_physical = 1

    # 1. Outline stage (1 call over compact whole-workbook registry)
    flow_units, outline_art = await build_outline(blocks_by_sheet, provider_id, doc_id=doc_id)
    llm_physical += (outline_art.retry_count if outline_art else 0)

    if not flow_units:
        return StructureResult(
            flows=[], steps=[], cases=[], outline_artifact=outline_art,
            logical_calls=llm_logical, physical_attempts=llm_physical,
        )

    # 2. Detail & Corrector in batches (pack 2-4 flows/call)
    unit_artifacts: list[UnitProcessingArtifact] = []
    final_unit_results: dict[str, DetailResult] = {}

    detail_batches: list[list[FlowUnit]] = []
    curr_batch: list[FlowUnit] = []
    curr_rows_count = 0

    for f in flow_units:
        rows_span = (f.row_end - f.row_start) + 1
        if curr_batch and (len(curr_batch) >= 4 or curr_rows_count + rows_span > 60):
            detail_batches.append(curr_batch)
            curr_batch = []
            curr_rows_count = 0
        curr_batch.append(f)
        curr_rows_count += rows_span

    if curr_batch:
        detail_batches.append(curr_batch)

    semaphore = asyncio.Semaphore(LLM_CONCURRENCY)

    async def _process_batch(batch: list[FlowUnit]):
        nonlocal llm_logical, llm_physical
        async with semaphore:
            batch_alias_map = {f"f{idx}": f for idx, f in enumerate(batch, start=1)}
            flow_to_alias_local = {f.unit_id: f"f{idx}" for idx, f in enumerate(batch, start=1)}

            # Stage B: Cell-ID detail extraction
            detail_dict, l2_raw, l2_retries, l2_lat, l2_warns = await extract_detail_batch(
                batch, sheets_by_name, provider_id
            )

            # Stage C: Gated patch-based corrector
            flows_needing_correction = []
            for f in batch:
                draft = detail_dict.get(f.unit_id, DetailResult())
                s_extract = sheets_by_name.get(f.sheet)
                uncovered = compute_uncovered_rows(f, draft, s_extract)
                flow_step_count = len(draft.steps)
                flow_row_span = (f.row_end - f.row_start) + 1
                if uncovered or flow_step_count >= BIG_FLOW_STEPS or flow_row_span >= BIG_FLOW_ROWS:
                    flows_needing_correction.append(f)

            if flows_needing_correction:
                corrected_dict, patches, l3_raw, l3_retries, l3_lat, l3_warns = await correct_detail_batch(
                    flows_needing_correction, detail_dict, sheets_by_name, provider_id
                )
                llm_logical += 2
                llm_physical += (1 + l2_retries) + (1 + l3_retries)
            else:
                corrected_dict = detail_dict
                patches = []
                l3_raw = ""
                l3_retries = 0
                l3_lat = 0.0
                l3_warns = []
                llm_logical += 1
                llm_physical += (1 + l2_retries)

            for f in batch:
                res = corrected_dict.get(f.unit_id, detail_dict.get(f.unit_id, DetailResult()))
                final_unit_results[f.unit_id] = res

                f_alias = flow_to_alias_local.get(f.unit_id, "")
                unit_patches = [p.model_dump() for p in patches if p.flow_alias == f_alias]
                unit_artifacts.append(
                    UnitProcessingArtifact(
                        flow_unit_id=f.unit_id,
                        detail_raw=l2_raw,
                        corrector_raw=l3_raw,
                        detail_latency_ms=l2_lat,
                        corrector_latency_ms=l3_lat,
                        detail_retry_count=l2_retries,
                        corrector_retry_count=l3_retries,
                        corrected=len(unit_patches) > 0,
                        patches_applied=unit_patches,
                        warnings=l2_warns + l3_warns,
                    )
                )

    await asyncio.gather(*[_process_batch(b) for b in detail_batches])

    # 3. Stage D: Step-level Completeness Sweep
    step_ledger: set[tuple[str, int]] = set()
    for f in flow_units:
        res = final_unit_results.get(f.unit_id, DetailResult())
        for st in res.steps:
            if st.row_start:
                for r_ix in range(st.row_start, (st.row_end or st.row_start) + 1):
                    step_ledger.add((f.sheet, r_ix))
        for cs in res.cases:
            if cs.row_ix:
                step_ledger.add((f.sheet, cs.row_ix))

    # Collect all semantic rows across relevant sheets
    all_semantic_rows: list[dict[str, Any]] = []
    for s_extract, s_class in blocks_by_sheet:
        seen_row_ix: set[int] = set()
        for b in s_extract.blocks:
            for r in b.rows:
                if r.row_ix in seen_row_ix:
                    continue
                seen_row_ix.add(r.row_ix)
                cell_vals = [c.value.strip() for c in r.cells if c.value and c.value.strip()]
                if cell_vals:
                    all_semantic_rows.append({
                        "sheet_name": s_extract.sheet,
                        "row_ix": r.row_ix,
                        "indent": r.indent_col,
                        "text_60": " | ".join(cell_vals)[:60],
                    })

    missing_spans, completeness_art = await check_step_completeness(
        step_ledger, all_semantic_rows, flow_units, provider_id
    )
    llm_logical += 1  # completeness call
    llm_physical += 1 + (completeness_art.retry_count if completeness_art else 0)

    # 4. Bounded Recovery (1 round) if missing spans found
    if missing_spans:
        recovery_units: list[FlowUnit] = []
        for ms in missing_spans:
            s_extract = sheets_by_name.get(ms.sheet_name)
            if s_extract:
                rec_f = FlowUnit(
                    unit_id=f"uf:{new_id()}",
                    sheet=ms.sheet_name,
                    kind="narrative",
                    name_ja=ms.flow_name_ja or f"(補完) {ms.reason}",
                    name_en="Recovered User Flow",
                    row_start=ms.row_start,
                    row_end=ms.row_end,
                    raw_row_start=ms.row_start,
                    raw_row_end=ms.row_end,
                    flow_group_key=f"rec_{new_id()[:6]}",
                )
                recovery_units.append(rec_f)

        if recovery_units:
            await _process_batch(recovery_units)
            completeness_art.recovered_count = len(recovery_units)
            # Append recovery units to flow_units so they are assembled into the database
            flow_units.extend(recovery_units)

    # 5. Deterministic Assembly & Deduplication
    # Group FlowUnits by canonical flow key (aggregating multi-sheet spans and continuation fragments into ONE flow)
    now = utc_now_iso()
    flows_list: list[dict[str, Any]] = []
    steps_list: list[dict[str, Any]] = []
    cases_list: list[dict[str, Any]] = []

    # Build canonical group key for each FlowUnit
    grouped_units: dict[str, list[FlowUnit]] = {}
    for f in flow_units:
        c_ja = canonicalize_flow_name(f.name_ja)
        # Group by canonical Japanese name if available, otherwise flow_group_key
        g_key = f"flow_{c_ja}" if c_ja else (f.flow_group_key or f.unit_id)
        grouped_units.setdefault(g_key, []).append(f)

    seen_steps: set[tuple[str, int, int, str]] = set()  # (sheet, row_start, row_end, text_ja)

    for g_key, units in grouped_units.items():
        primary_f = units[0]
        db_flow_id = primary_f.unit_id
        flow_ordinal = len(flows_list) + 1

        display_name_ja = canonicalize_flow_name(primary_f.name_ja) or primary_f.name_ja
        display_name_en = primary_f.name_en or display_name_ja

        flows_list.append({
            "id": db_flow_id,
            "doc_id": doc_id,
            "ordinal": flow_ordinal,
            "name_ja": display_name_ja,
            "name_en": display_name_en,
            "kind": primary_f.kind,
            "sheet": primary_f.sheet,
            "scope_note": primary_f.scope_note,
            "created_at": now,
        })

        for f in units:
            detail_res = final_unit_results.get(f.unit_id, DetailResult())

            for step in detail_res.steps:
                ja = (step.text_ja or "").strip()
                en = (step.text_en or "").strip()
                if not ja and not en:
                    continue

                r_start = step.row_start if step.row_start is not None else f.raw_row_start
                r_end = step.row_end if step.row_end is not None else f.raw_row_end

                # Clamp strictly against unpadded raw span
                clamped_start = max(f.raw_row_start, min(r_start, f.raw_row_end))
                clamped_end = max(f.raw_row_start, min(r_end, f.raw_row_end))
                if clamped_start > clamped_end:
                    clamped_start, clamped_end = clamped_end, clamped_start

                # Cross-flow deduplication
                step_key = (f.sheet, clamped_start, clamped_end, ja)
                if step_key in seen_steps:
                    continue
                seen_steps.add(step_key)

                was_clamped = (clamped_start != step.row_start) or (clamped_end != step.row_end)
                flags = ["ROW_ANCHOR_CLAMPED"] if was_clamped else []

                prov_json = json.dumps({
                    "flow_unit_id": f.unit_id,
                    "sheet": f.sheet,
                    "raw_row_start": step.row_start,
                    "raw_row_end": step.row_end,
                    "flags": flags,
                })

                step_id = f"us:{new_id()}"
                step_ordinal = len(steps_list) + 1
                steps_list.append({
                    "id": step_id,
                    "flow_id": db_flow_id,
                    "ordinal": step_ordinal,
                    "kind": step.kind,
                    "section_id": step.section_id,
                    "text_ja": ja,
                    "text_en": step.text_en or ja,
                    "trigger_ja": step.trigger_ja,
                    "expected_ja": step.expected_ja,
                    "screen_name_ja": step.screen_name_ja,
                    "in_scope": 1 if step.in_scope else 0,
                    "scope_note": step.scope_note,
                    "sheet": f.sheet,
                    "row_start": clamped_start,
                    "row_end": clamped_end,
                    "provenance_json": prov_json,
                    "created_at": now,
                })

            for case in detail_res.cases:
                raw_r_ix = case.row_ix if case.row_ix is not None else f.raw_row_start
                clamped_r_ix = max(f.raw_row_start, min(raw_r_ix, f.raw_row_end))

                case_id = f"uc:{new_id()}"
                case_ordinal = len(cases_list) + 1
                cases_list.append({
                    "id": case_id,
                    "doc_id": doc_id,
                    "ordinal": case_ordinal,
                    "screen_name_ja": case.screen_name_ja,
                    "area_ja": case.area_ja,
                    "viewpoint_ja": case.viewpoint_ja,
                    "conditions_json": json.dumps(case.conditions or [], ensure_ascii=False),
                    "trigger_ja": case.trigger_ja,
                    "check_item_ja": case.check_item_ja,
                    "expected_ja": case.expected_ja,
                    "link_section_id": case.link_section_id,
                    "sheet": f.sheet,
                    "row_ix": clamped_r_ix,
                    "raw_json": json.dumps(case.model_dump(), ensure_ascii=False),
                    "created_at": now,
                })

    return StructureResult(
        flows=flows_list,
        steps=steps_list,
        cases=cases_list,
        outline_artifact=outline_art,
        unit_artifacts=unit_artifacts,
        completeness_artifact=completeness_art,
        logical_calls=llm_logical,
        physical_attempts=llm_physical,
    )
