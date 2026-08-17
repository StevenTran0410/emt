"""Cell-ID Detail and Patch-based Corrector stages for User Flow structuring (TICKET UR + UR-FIX).

- Detail stage (extract_detail_batch, LOW reasoning): packs 2-4 flows/call.
  Model returns ROW/CELL IDs (NO copied Japanese text); code materializes text_ja VERBATIM from raw cells.
  Per-item tolerant parsing + assert_exact_id_coverage on flow aliases.
- Corrector stage (correct_detail_batch, HIGH reasoning): independent verifier pass over raw rows.
  Returns PATCH ops only; code applies non-delete ops first and delete ops in descending index order.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from domain.business_flow_integrity._llm import (
    assert_exact_id_coverage,
    call_with_reasoning_ladder,
    coerce_results_wrapper,
)
from domain.doc_graph._llm_citation import _parse_llm_json
from domain.model_connector.types import ChatMessage, ChatRequest
from shared.logger import logger

from ._extract import RawCell, RawRow, SheetExtract
from ._skeleton import FlowUnit


# ---------------------------------------------------------------------------
# Core Data Models
# ---------------------------------------------------------------------------

class StepItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    kind: str = "action"  # 'action' | 'expectation' | 'error_rule'
    section_id: str | None = None  # e.g. '2.1①'
    text_ja: str = ""  # materialized verbatim from sheet
    text_en: str | None = None
    trigger_ja: str | None = None
    expected_ja: str | None = None
    screen_name_ja: str | None = None
    in_scope: bool = True
    scope_note: str | None = None
    row_start: int | None = None
    row_end: int | None = None
    sheet: str | None = None


class CaseItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    screen_name_ja: str | None = None
    area_ja: str | None = None
    viewpoint_ja: str | None = None
    conditions: list[str] | None = None
    trigger_ja: str | None = None
    check_item_ja: str | None = None
    expected_ja: str | None = None
    link_section_id: str | None = None  # 想定表との紐づけ
    row_ix: int | None = None
    sheet: str | None = None


@dataclass
class DetailResult:
    steps: list[StepItem] = field(default_factory=list)
    cases: list[CaseItem] = field(default_factory=list)


@dataclass
class UnitProcessingArtifact:
    flow_unit_id: str
    detail_raw: str = ""
    corrector_raw: str = ""
    detail_latency_ms: float = 0.0
    corrector_latency_ms: float = 0.0
    detail_retry_count: int = 0
    corrector_retry_count: int = 0
    corrected: bool = False
    patches_applied: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Detail Models (Cell-ID Based)
# ---------------------------------------------------------------------------

class CellRef(BaseModel):
    model_config = ConfigDict(extra="ignore")
    sheet_id: str | None = None
    row_ix: int | None = None
    col: int | None = None


class DetailStepItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    kind: str = "action"
    section_id: str | None = None
    text_cell_ref: CellRef | list[CellRef] | None = None
    text_en: str | None = None
    trigger_cell_ref: CellRef | None = None
    expected_cell_ref: CellRef | None = None
    screen_name_cell_ref: CellRef | None = None
    in_scope: bool = True
    scope_note: str | None = None
    row_start: int | None = None
    row_end: int | None = None


class DetailCaseItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    screen_name_cell_ref: CellRef | None = None
    area_cell_ref: CellRef | None = None
    viewpoint_cell_ref: CellRef | None = None
    condition_cell_refs: list[CellRef] | None = None
    trigger_cell_ref: CellRef | None = None
    check_item_cell_ref: CellRef | None = None
    expected_cell_ref: CellRef | None = None
    link_section_id: str | None = None
    row_ix: int | None = None


class DetailFlowResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    flow_alias: str
    steps: list[DetailStepItem] = Field(default_factory=list)
    cases: list[DetailCaseItem] = Field(default_factory=list)


class DetailBatchResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    results: list[DetailFlowResult]


_STANDALONE_MARKERS = frozenset({"☑", "☐", "■", "✓", "□", "✔", "▼", "▲", "◆", "◇", "○", "●"})


def render_cell_line(
    row: RawRow,
    *,
    include_indent: bool = True,
    drop_markers: bool = True,
) -> str:
    """Render a RawRow to a prompt line, optionally dropping standalone marker cells."""
    cells = []
    for c in row.cells:
        if not c.value or not c.value.strip():
            continue
        v = c.value.strip()
        if drop_markers and v in _STANDALONE_MARKERS:
            continue
        cells.append(f"col {c.col}: '{v}'")
    if not cells:
        return ""
    if include_indent:
        return f"[Row {row.row_ix}, indent {row.indent_col}] " + ", ".join(cells)
    return f"[Row {row.row_ix}] " + ", ".join(cells)


def compute_uncovered_rows(
    flow: FlowUnit,
    draft: DetailResult,
    sheet_extract: SheetExtract | None,
) -> list[int]:
    """Compute the list of raw row indices inside flow span not covered by any draft step/case."""
    flow_raw_row_indices: list[int] = []
    if sheet_extract:
        seen_row_ix: set[int] = set()
        for b in sheet_extract.blocks:
            for r in b.rows:
                if flow.row_start <= r.row_ix <= flow.row_end and r.row_ix not in seen_row_ix:
                    seen_row_ix.add(r.row_ix)
                    flow_raw_row_indices.append(r.row_ix)

    covered_rows: set[int] = set()
    for st in draft.steps:
        if st.row_start:
            for r_ix in range(st.row_start, (st.row_end or st.row_start) + 1):
                covered_rows.add(r_ix)
    for cs in draft.cases:
        if cs.row_ix:
            covered_rows.add(cs.row_ix)

    return [r_ix for r_ix in flow_raw_row_indices if r_ix not in covered_rows]


# ---------------------------------------------------------------------------
# Verbatim Text Materialization
# ---------------------------------------------------------------------------

def _find_row(sheet_extract: SheetExtract, row_ix: int) -> RawRow | None:
    for b in sheet_extract.blocks:
        for r in b.rows:
            if r.row_ix == row_ix:
                return r
    return None


def materialize_cell_text(
    sheet_extract: SheetExtract,
    cell_ref: CellRef | list[CellRef] | None,
) -> str:
    """Materialize Japanese text VERBATIM from the referenced sheet cells."""
    if cell_ref is None:
        return ""
    if isinstance(cell_ref, list):
        parts = [materialize_cell_text(sheet_extract, c) for c in cell_ref]
        return " ".join(p for p in parts if p).strip()

    if cell_ref.row_ix is None:
        return ""

    row = _find_row(sheet_extract, cell_ref.row_ix)
    if row is None:
        return ""

    if cell_ref.col is not None:
        for c in row.cells:
            if c.col == cell_ref.col:
                return c.value.strip()

    # If no specific column or column not matched, concatenate non-empty cell values
    vals = [c.value.strip() for c in row.cells if c.value and c.value.strip()]
    return " ".join(vals).strip()


def _collect_row_indices(step: DetailStepItem) -> list[int]:
    rows: list[int] = []
    for ref in [step.text_cell_ref, step.trigger_cell_ref, step.expected_cell_ref, step.screen_name_cell_ref]:
        if isinstance(ref, CellRef) and ref.row_ix is not None:
            rows.append(ref.row_ix)
        elif isinstance(ref, list):
            for r in ref:
                if isinstance(r, CellRef) and r.row_ix is not None:
                    rows.append(r.row_ix)
    return rows


def materialize_step(
    detail_step: DetailStepItem,
    sheet_extract: SheetExtract,
) -> StepItem | None:
    """Materialize a StepItem from a DetailStepItem using verbatim cell lookup."""
    text_ja = materialize_cell_text(sheet_extract, detail_step.text_cell_ref)
    trigger_ja = materialize_cell_text(sheet_extract, detail_step.trigger_cell_ref) or None
    expected_ja = materialize_cell_text(sheet_extract, detail_step.expected_cell_ref) or None
    screen_name_ja = materialize_cell_text(sheet_extract, detail_step.screen_name_cell_ref) or None

    # Derive exact row bounds from populated cell references (Issue 4)
    ref_rows = _collect_row_indices(detail_step)
    if ref_rows:
        row_s = min(ref_rows)
        row_e = max(ref_rows)
    else:
        row_s = detail_step.row_start
        row_e = detail_step.row_end
        if row_s is not None and row_e is None:
            row_e = row_s
        if row_e is not None and row_s is None:
            row_s = row_e

    if not text_ja and not trigger_ja and not expected_ja:
        # Invalid ref with no text -> drop step
        return None

    # Normalize kind
    k = (detail_step.kind or "action").lower()
    if "expect" in k:
        norm_kind = "expectation"
    elif "err" in k or "rule" in k:
        norm_kind = "error_rule"
    else:
        norm_kind = "action"

    return StepItem(
        kind=norm_kind,
        section_id=detail_step.section_id,
        text_ja=text_ja,
        text_en=detail_step.text_en or text_ja,
        trigger_ja=trigger_ja,
        expected_ja=expected_ja,
        screen_name_ja=screen_name_ja,
        in_scope=detail_step.in_scope,
        scope_note=detail_step.scope_note,
        row_start=row_s,
        row_end=row_e,
        sheet=sheet_extract.sheet,
    )


def materialize_case(
    detail_case: DetailCaseItem,
    sheet_extract: SheetExtract,
) -> CaseItem | None:
    """Materialize a CaseItem from a DetailCaseItem using verbatim cell lookup."""
    screen_name = materialize_cell_text(sheet_extract, detail_case.screen_name_cell_ref) or None
    area = materialize_cell_text(sheet_extract, detail_case.area_cell_ref) or None
    viewpoint = materialize_cell_text(sheet_extract, detail_case.viewpoint_cell_ref) or None
    trigger = materialize_cell_text(sheet_extract, detail_case.trigger_cell_ref) or None
    check_item = materialize_cell_text(sheet_extract, detail_case.check_item_cell_ref) or None
    expected = materialize_cell_text(sheet_extract, detail_case.expected_cell_ref) or None

    conds = None
    if detail_case.condition_cell_refs:
        cond_texts = [materialize_cell_text(sheet_extract, c) for c in detail_case.condition_cell_refs]
        conds = [ct for ct in cond_texts if ct]

    row_ix = detail_case.row_ix
    if row_ix is None:
        for ref in [detail_case.trigger_cell_ref, detail_case.expected_cell_ref, detail_case.check_item_cell_ref, detail_case.screen_name_cell_ref]:
            if isinstance(ref, CellRef) and ref.row_ix is not None:
                row_ix = ref.row_ix
                break

    if not trigger and not expected and not check_item and not screen_name and not viewpoint:
        return None

    return CaseItem(
        screen_name_ja=screen_name,
        area_ja=area,
        viewpoint_ja=viewpoint,
        conditions=conds,
        trigger_ja=trigger,
        check_item_ja=check_item,
        expected_ja=expected,
        link_section_id=detail_case.link_section_id,
        row_ix=row_ix,
        sheet=sheet_extract.sheet,
    )


# ---------------------------------------------------------------------------
# Detail Prompts & Batch Evaluator
# ---------------------------------------------------------------------------

_DETAIL_SYSTEM_PROMPT = """You are an expert system analyzing Japanese Excel test/flow scenario workbooks.
You are given the exact raw rows for 1-4 business flows.

Extract all detailed steps, expectations, error rules, and test cases.
DO NOT copy long Japanese text in your output — return ROW and CELL references (`text_cell_ref`, `trigger_cell_ref`, `expected_cell_ref`, `screen_name_cell_ref`).
Whenever multiple cell references on a step share the same row, specify `col` (e.g. `{"row_ix": 2, "col": 4}`).
The system will materialize verbatim Japanese text directly from the Excel cells.
Provide a clean English gloss in `text_en`.

Output Schema:
{
  "results": [
    {
      "flow_alias": "f1",
      "steps": [
        {
          "kind": "action",
          "section_id": "2.1①",
          "text_cell_ref": {"row_ix": 2, "col": 4},
          "text_en": "Input condition 1 (Coil) and press Enter",
          "trigger_cell_ref": {"row_ix": 2, "col": 5},
          "expected_cell_ref": {"row_ix": 3, "col": 6},
          "screen_name_cell_ref": {"row_ix": 1, "col": 2},
          "in_scope": true,
          "scope_note": null,
          "row_start": 2,
          "row_end": 3
        }
      ],
      "cases": [
        {
          "screen_name_cell_ref": {"row_ix": 10, "col": 2},
          "area_cell_ref": {"row_ix": 10, "col": 3},
          "viewpoint_cell_ref": {"row_ix": 10, "col": 4},
          "condition_cell_refs": [{"row_ix": 10, "col": 5}],
          "trigger_cell_ref": {"row_ix": 10, "col": 6},
          "check_item_cell_ref": {"row_ix": 10, "col": 7},
          "expected_cell_ref": {"row_ix": 10, "col": 8},
          "link_section_id": "2.1①",
          "row_ix": 10
        }
      ]
    }
  ]
}
Every flow_alias from the input MUST appear exactly once in the results list.
"""


async def extract_detail_batch(
    flows_batch: list[FlowUnit],
    sheets_by_name: dict[str, SheetExtract],
    provider_id: str,
) -> tuple[dict[str, DetailResult], str, int, float, list[str]]:
    """Execute Stage B: Extract cell-ID based steps and cases for a batch of flows (LOW reasoning)."""
    if not flows_batch:
        return {}, "", 0, 0.0, []

    alias_to_flow: dict[str, FlowUnit] = {}
    payload_lines = ["FLOWS AND RAW ROWS:"]

    for idx, f in enumerate(flows_batch, start=1):
        f_alias = f"f{idx}"
        alias_to_flow[f_alias] = f
        s_extract = sheets_by_name.get(f.sheet)
        payload_lines.append(f"\n--- FLOW {f_alias}: '{f.name_ja}' (Kind: {f.kind}, Sheet: {f.sheet}, Rows: {f.row_start}..{f.row_end}) ---")

        seen_row_ix: set[int] = set()
        if s_extract:
            for b in s_extract.blocks:
                for r in b.rows:
                    if f.row_start <= r.row_ix <= f.row_end and r.row_ix not in seen_row_ix:
                        seen_row_ix.add(r.row_ix)
                        row_line = render_cell_line(r, include_indent=True, drop_markers=True)
                        if row_line:
                            payload_lines.append(row_line)

    user_prompt = "\n".join(payload_lines)

    attempts = {"n": 0}

    def _build_req(effort: str) -> ChatRequest:
        attempts["n"] += 1
        return ChatRequest(
            provider_id=provider_id,
            messages=[
                ChatMessage(role="system", content=_DETAIL_SYSTEM_PROMPT),
                ChatMessage(role="user", content=user_prompt),
            ],
            stream=True,
            max_completion_tokens=25000,
            temperature=0.0,
            json_mode=True,
            reasoning_effort=effort,
        )

    def _parse(text: str) -> list[DetailFlowResult]:
        parsed = coerce_results_wrapper(_parse_llm_json(text))
        raw_items = parsed.get("results", []) if isinstance(parsed, dict) else (parsed if isinstance(parsed, list) else [])
        
        flow_results: list[DetailFlowResult] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            f_alias = item.get("flow_alias") or item.get("alias") or ""
            if not f_alias:
                continue

            # Parse steps tolerantly per-step
            valid_steps: list[DetailStepItem] = []
            for st in item.get("steps", []):
                if isinstance(st, dict):
                    try:
                        valid_steps.append(DetailStepItem.model_validate(st))
                    except Exception as e:
                        logger.debug(f"Skipping malformed step in flow {f_alias}: {e}")

            # Parse cases tolerantly per-case
            valid_cases: list[DetailCaseItem] = []
            for cs in item.get("cases", []):
                if isinstance(cs, dict):
                    try:
                        valid_cases.append(DetailCaseItem.model_validate(cs))
                    except Exception as e:
                        logger.debug(f"Skipping malformed case in flow {f_alias}: {e}")

            flow_results.append(DetailFlowResult(
                flow_alias=f_alias,
                steps=valid_steps,
                cases=valid_cases,
            ))

        expected_aliases = set(alias_to_flow.keys())
        got_aliases = [fr.flow_alias for fr in flow_results]
        assert_exact_id_coverage(got_aliases, expected_aliases, label="detail extraction")
        return flow_results

    start = time.monotonic()
    ladder_res = await call_with_reasoning_ladder(
        _build_req, _parse, first_effort="low", label="UserFlow Detail (LLM#1b)",
        provider_id=provider_id,
    )
    latency_ms = round((time.monotonic() - start) * 1000, 1)
    retry_count = max(attempts["n"] - 1, 0)

    results_by_unit: dict[str, DetailResult] = {}
    warnings: list[str] = []
    raw_text = ""

    if ladder_res is not None:
        flow_results, raw_text = ladder_res
        for fr in flow_results:
            flow_unit = alias_to_flow.get(fr.flow_alias)
            if not flow_unit:
                continue
            s_extract = sheets_by_name.get(flow_unit.sheet)
            if not s_extract:
                continue

            mat_steps: list[StepItem] = []
            for ds in fr.steps:
                st = materialize_step(ds, s_extract)
                if st:
                    mat_steps.append(st)
                else:
                    warnings.append(f"Dropped empty/invalid step in {fr.flow_alias}")

            mat_cases: list[CaseItem] = []
            for dc in fr.cases:
                cs = materialize_case(dc, s_extract)
                if cs:
                    mat_cases.append(cs)
                else:
                    warnings.append(f"Dropped empty/invalid case in {fr.flow_alias}")

            results_by_unit[flow_unit.unit_id] = DetailResult(steps=mat_steps, cases=mat_cases)

    # Ensure all flows in the batch have a result
    for f in flows_batch:
        if f.unit_id not in results_by_unit:
            warnings.append(f"Detail extraction ladder failed for flow {f.name_ja} ({f.unit_id}); generating coarse fallback step")
            s_extract = sheets_by_name.get(f.sheet)
            fallback_text = f.name_ja
            if s_extract:
                seen_row_ix: set[int] = set()
                row_texts = []
                for b in s_extract.blocks:
                    for r in b.rows:
                        if f.raw_row_start <= r.row_ix <= f.raw_row_end and r.row_ix not in seen_row_ix:
                            seen_row_ix.add(r.row_ix)
                            cell_vals = [c.value.strip() for c in r.cells if c.value and c.value.strip()]
                            if cell_vals:
                                row_texts.append(" ".join(cell_vals))
                if row_texts:
                    fallback_text = "\n".join(row_texts[:3])

            results_by_unit[f.unit_id] = DetailResult(
                steps=[
                    StepItem(
                        kind="action",
                        section_id=f.section_markers[0] if f.section_markers else None,
                        text_ja=fallback_text,
                        text_en=f.name_en or fallback_text,
                        in_scope=f.in_scope,
                        scope_note=f.scope_note,
                        row_start=f.raw_row_start,
                        row_end=f.raw_row_end,
                        sheet=f.sheet,
                    )
                ]
            )

    return results_by_unit, raw_text, retry_count, latency_ms, warnings


# ---------------------------------------------------------------------------
# Corrector Models & Batch Evaluator (Patch-Based)
# ---------------------------------------------------------------------------

class PatchOp(BaseModel):
    model_config = ConfigDict(extra="ignore")
    op: str  # "add_step" | "delete_step" | "change_span" | "change_scope" | "change_section_id" | "add_case" | "delete_case"
    flow_alias: str
    target_step_idx: int | None = None
    target_case_idx: int | None = None
    step: DetailStepItem | None = None
    case: DetailCaseItem | None = None
    section_id: str | None = None
    in_scope: bool | None = None
    scope_note: str | None = None
    row_start: int | None = None
    row_end: int | None = None
    reason: str | None = None


class CorrectorBatchResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    patches: list[PatchOp] = Field(default_factory=list)


_CORRECTOR_SYSTEM_PROMPT = """You are an authoritative verification and correction agent for Japanese Excel test/flow scenario workbooks.
You are given:
1. The EXACT raw rows of 1-4 business flows.
2. The DRAFT extracted steps and cases.
3. An UNCOVERED-ROW ledger of rows inside each flow that have not been included in any draft step.

Your task is to inspect the ground truth and emit PATCH operations to correct any errors:
- `add_step`: Recover a step/expectation/error-rule that was missed in the raw rows. Use `text_cell_ref` with row and column.
- `delete_step`: Remove a spurious or duplicate step (provide 0-indexed `target_step_idx`).
- `change_section_id`: Fix an incorrect section identifier (provide `target_step_idx` and `section_id`).
- `change_scope`: Fix an incorrect in_scope flag or add a missing scope_note.
- `change_span`: Fix incorrect row_start/row_end bounds.

If the draft is completely accurate, return `{"patches": []}`.

Output Format:
{
  "patches": [
    {
      "op": "add_step",
      "flow_alias": "f1",
      "step": {
        "kind": "error_rule",
        "section_id": "2.1④",
        "text_cell_ref": {"row_ix": 5, "col": 4},
        "text_en": "Display error if invalid code entered",
        "in_scope": true
      },
      "reason": "Missed error handling row 5"
    }
  ]
}
"""


async def correct_detail_batch(
    flows_batch: list[FlowUnit],
    draft_by_unit: dict[str, DetailResult],
    sheets_by_name: dict[str, SheetExtract],
    provider_id: str,
) -> tuple[dict[str, DetailResult], list[PatchOp], str, int, float, list[str]]:
    """Execute Stage C: Independent patch-based corrector pass (HIGH reasoning)."""
    if not flows_batch:
        return draft_by_unit, [], "", 0, 0.0, []

    alias_to_flow: dict[str, FlowUnit] = {}
    flow_to_alias: dict[str, str] = {}
    prompt_lines = ["GROUND TRUTH RAW ROWS AND DRAFT EXTRACTIONS:"]

    for idx, f in enumerate(flows_batch, start=1):
        f_alias = f"f{idx}"
        alias_to_flow[f_alias] = f
        flow_to_alias[f.unit_id] = f_alias
        s_extract = sheets_by_name.get(f.sheet)
        draft = draft_by_unit.get(f.unit_id, DetailResult())

        prompt_lines.append(f"\n=== FLOW {f_alias}: '{f.name_ja}' (Sheet: {f.sheet}, Rows: {f.row_start}..{f.row_end}) ===")

        # Raw rows
        prompt_lines.append("RAW ROWS:")
        seen_row_ix: set[int] = set()
        if s_extract:
            for b in s_extract.blocks:
                for r in b.rows:
                    if f.row_start <= r.row_ix <= f.row_end and r.row_ix not in seen_row_ix:
                        seen_row_ix.add(r.row_ix)
                        row_line = render_cell_line(r, include_indent=False, drop_markers=True)
                        if row_line:
                            prompt_lines.append(row_line)

        # Draft steps
        prompt_lines.append("DRAFT STEPS:")
        for s_idx, st in enumerate(draft.steps):
            prompt_lines.append(f"  [idx={s_idx}] kind={st.kind}, sec={st.section_id}, rows={st.row_start}..{st.row_end}: '{st.text_ja[:60]}'")

        # Uncovered rows
        uncovered = compute_uncovered_rows(f, draft, s_extract)
        if uncovered:
            prompt_lines.append(f"UNCOVERED ROWS IN THIS FLOW: {uncovered}")

    user_prompt = "\n".join(prompt_lines)

    attempts = {"n": 0}

    def _build_req(effort: str) -> ChatRequest:
        attempts["n"] += 1
        return ChatRequest(
            provider_id=provider_id,
            messages=[
                ChatMessage(role="system", content=_CORRECTOR_SYSTEM_PROMPT),
                ChatMessage(role="user", content=user_prompt),
            ],
            stream=True,
            max_completion_tokens=25000,
            temperature=0.0,
            json_mode=True,
            reasoning_effort=effort,
        )

    def _parse(text: str) -> list[PatchOp]:
        parsed = _parse_llm_json(text)
        raw_patches = parsed.get("patches", []) if isinstance(parsed, dict) else (parsed if isinstance(parsed, list) else [])
        valid_patches: list[PatchOp] = []
        for p in raw_patches:
            if isinstance(p, dict):
                try:
                    valid_patches.append(PatchOp.model_validate(p))
                except Exception as e:
                    logger.debug(f"Skipping malformed patch: {e}")
        return valid_patches

    start = time.monotonic()
    ladder_res = await call_with_reasoning_ladder(
        _build_req, _parse, first_effort="low", label="UserFlow Corrector (LLM#1c)",
        provider_id=provider_id,
    )
    latency_ms = round((time.monotonic() - start) * 1000, 1)
    retry_count = max(attempts["n"] - 1, 0)

    patches: list[PatchOp] = []
    raw_text = ""
    warnings: list[str] = []
    if ladder_res is not None:
        patches, raw_text = ladder_res

    # Apply patches deterministically with positional safety
    # We create a deep copy of steps/cases for mutation
    corrected_by_unit: dict[str, DetailResult] = {}
    for f in flows_batch:
        orig = draft_by_unit.get(f.unit_id, DetailResult())
        corrected_by_unit[f.unit_id] = DetailResult(
            steps=[s.model_copy(deep=True) for s in orig.steps],
            cases=[c.model_copy(deep=True) for c in orig.cases],
        )

    # 1. Apply non-delete patches first (modifications and additions)
    applied_patches: list[PatchOp] = []
    delete_step_patches: list[PatchOp] = []
    delete_case_patches: list[PatchOp] = []

    for p in patches:
        flow_unit = alias_to_flow.get(p.flow_alias)
        if not flow_unit:
            warnings.append(f"Patch references unknown flow_alias '{p.flow_alias}'")
            continue
        cur = corrected_by_unit[flow_unit.unit_id]
        s_extract = sheets_by_name.get(flow_unit.sheet)

        op_lower = (p.op or "").lower()

        if "delete_step" in op_lower:
            delete_step_patches.append(p)
        elif "delete_case" in op_lower:
            delete_case_patches.append(p)
        elif "add_step" in op_lower and p.step and s_extract:
            st = materialize_step(p.step, s_extract)
            if st:
                # Deduplicate if step already exists with same span & text
                if not any(existing.row_start == st.row_start and existing.row_end == st.row_end and existing.text_ja == st.text_ja for existing in cur.steps):
                    cur.steps.append(st)
                    applied_patches.append(p)
                else:
                    warnings.append(f"Deduplicated redundant add_step patch in {p.flow_alias}")
        elif "change_section_id" in op_lower and p.target_step_idx is not None:
            if 0 <= p.target_step_idx < len(cur.steps) and p.section_id is not None:
                cur.steps[p.target_step_idx].section_id = p.section_id
                applied_patches.append(p)
        elif "change_scope" in op_lower and p.target_step_idx is not None:
            if 0 <= p.target_step_idx < len(cur.steps):
                if p.in_scope is not None:
                    cur.steps[p.target_step_idx].in_scope = p.in_scope
                if p.scope_note is not None:
                    cur.steps[p.target_step_idx].scope_note = p.scope_note
                applied_patches.append(p)
        elif "change_span" in op_lower and p.target_step_idx is not None:
            if 0 <= p.target_step_idx < len(cur.steps):
                if p.row_start is not None:
                    cur.steps[p.target_step_idx].row_start = p.row_start
                if p.row_end is not None:
                    cur.steps[p.target_step_idx].row_end = p.row_end
                applied_patches.append(p)
        elif "add_case" in op_lower and p.case and s_extract:
            cs = materialize_case(p.case, s_extract)
            if cs:
                cur.cases.append(cs)
                applied_patches.append(p)

    # 2. Apply delete patches in DESCENDING target index order to prevent index shifting
    delete_step_patches.sort(key=lambda p: p.target_step_idx if p.target_step_idx is not None else -1, reverse=True)
    for p in delete_step_patches:
        flow_unit = alias_to_flow.get(p.flow_alias)
        if not flow_unit:
            continue
        cur = corrected_by_unit[flow_unit.unit_id]
        if p.target_step_idx is not None and 0 <= p.target_step_idx < len(cur.steps):
            cur.steps.pop(p.target_step_idx)
            applied_patches.append(p)

    delete_case_patches.sort(key=lambda p: p.target_case_idx if p.target_case_idx is not None else -1, reverse=True)
    for p in delete_case_patches:
        flow_unit = alias_to_flow.get(p.flow_alias)
        if not flow_unit:
            continue
        cur = corrected_by_unit[flow_unit.unit_id]
        if p.target_case_idx is not None and 0 <= p.target_case_idx < len(cur.cases):
            cur.cases.pop(p.target_case_idx)
            applied_patches.append(p)

    return corrected_by_unit, applied_patches, raw_text, retry_count, latency_ms, warnings
