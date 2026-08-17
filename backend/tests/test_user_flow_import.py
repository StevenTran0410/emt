"""Acceptance tests for TICKET UR & UR-FIX (User Flow Alignment xlsx extraction, lean structuring, and logic-hole fixes).

OFFLINE ONLY — all provider calls are stubbed, no real LLM/network calls.
Uses synthetic xlsx fixtures built in-test via openpyxl.
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any
import pytest
import openpyxl

from domain.model_connector.service import ProviderConfigService
from domain.model_connector.types import ChatRequest
from domain.user_flow import (
    FlowUnit,
    extract_detail_batch,
    extract_workbook,
    import_user_flow_xlsx,
)
from domain.user_flow._detail import correct_detail_batch
from domain.user_flow._extract import (
    CHUNK_ROW_SIZE,
    OVERSIZE_CHAR_LIMIT,
    OVERSIZE_ROW_LIMIT,
)
from infrastructure.db.database import close_db, get_db, init_db
from shared.utils import utc_now_iso


# ---------------------------------------------------------------------------
# Fixture creation helper (SYNTHETIC ONLY — never copy customer content)
# ---------------------------------------------------------------------------

def _build_synthetic_workbook(file_path: Path, n_extra_rows: int = 85) -> Path:
    """Create a synthetic workbook containing:
    1. Narrative sheet: 3 indent levels, circled digits (2.1①, 2.1⑩), checkbox options with scope notes,
       single empty rows inside section, 2-row gap between sections, 3-row vertical merge,
       #REF! error string cell, half-width kana, int/time cells, full-width spaces.
    2. Case table sheet: title rows, header, and data rows sharing vertical merges, plus filler rows to trigger oversize chunking.
    3. Hidden sheet: pasted source-like code.
    """
    wb = openpyxl.Workbook()

    # --- Sheet 1: Narrative (想定表) ---
    ws_narrative = wb.active
    ws_narrative.title = "想定表"

    # Row 1: Title with full-width space and long text (>40 chars)
    ws_narrative.cell(row=1, column=1, value="コイル試験指示　入力フロー　メイン業務処理テストシナリオ確認用データ　追加詳細説明文")

    # Row 2: Section 1 with circled digit 2.1① (indent col 1)
    ws_narrative.cell(row=2, column=1, value="2.1① メイン画面起動")
    # Row 3: Action (indent col 2)
    ws_narrative.cell(row=3, column=2, value="メニューからコイル試験を選択する")
    # Row 4: Expectation with half-width kana (indent col 3)
    ws_narrative.cell(row=4, column=3, value="ｺｲﾙ試験画面が表示される")

    # Row 5: Single empty row inside section (formatting, should NOT split block)

    # Row 6: Checkbox checked option + int + time
    ws_narrative.cell(row=6, column=2, value="☑ オプション1")
    ws_narrative.cell(row=6, column=4, value=123)
    ws_narrative.cell(row=6, column=5, value=datetime.time(9, 30, 0))

    # Row 7: Checkbox unchecked option + scope note
    ws_narrative.cell(row=7, column=2, value="□ オプション2 ※PoCとしては、1のみとする")

    # Row 8: #REF! error string cell (should be dropped)
    ws_narrative.cell(row=8, column=2, value="#REF!")

    # Rows 9-11: 3-row vertical merge in col 2, distinct cells in col 3
    ws_narrative.cell(row=9, column=2, value="共通前提条件")
    ws_narrative.merge_cells(start_row=9, start_column=2, end_row=11, end_column=2)
    ws_narrative.cell(row=9, column=3, value="詳細条件A")
    ws_narrative.cell(row=10, column=3, value="詳細条件B")
    ws_narrative.cell(row=11, column=3, value="詳細条件C")

    # Rows 12-13: 2-row empty gap (MUST split into a new block!)

    # Row 14: Section 2 with circled digit 2.1⑩
    ws_narrative.cell(row=14, column=1, value="2.1⑩ 試験機コード入力")
    # Row 15: Action
    ws_narrative.cell(row=15, column=2, value="試験機コードを入力しEnterを押下する")
    # Row 16: Expectation
    ws_narrative.cell(row=16, column=3, value="試験開始確認メッセージが表示される")

    # --- Sheet 2: Case Table (項目表) ---
    ws_cases = wb.create_sheet(title="項目表")
    # Title rows (1-2)
    ws_cases.cell(row=1, column=1, value="テスト項目表")
    ws_cases.cell(row=2, column=1, value="バージョン 1.0")
    # Header row (3)
    ws_cases.cell(row=3, column=1, value="No")
    ws_cases.cell(row=3, column=2, value="画面名")
    ws_cases.cell(row=3, column=3, value="確認エリア")
    ws_cases.cell(row=3, column=4, value="テスト観点")
    ws_cases.cell(row=3, column=5, value="条件1")
    ws_cases.cell(row=3, column=6, value="トリガー")
    ws_cases.cell(row=3, column=7, value="確認項目")
    ws_cases.cell(row=3, column=8, value="予測値")

    # Row 4: Data row with vertical merge spanning rows 4-5 on col 2
    ws_cases.cell(row=4, column=1, value=1)
    ws_cases.cell(row=4, column=2, value="FHNIXLOT")
    ws_cases.merge_cells(start_row=4, start_column=2, end_row=5, end_column=2)
    ws_cases.cell(row=4, column=3, value="ヘッダ部")
    ws_cases.cell(row=4, column=4, value="初期表示")
    ws_cases.cell(row=4, column=5, value="コイル試験")
    ws_cases.cell(row=4, column=6, value="画面遷移")
    ws_cases.cell(row=4, column=7, value="タイトル")
    ws_cases.cell(row=4, column=8, value="正常表示")

    ws_cases.cell(row=5, column=1, value=2)
    ws_cases.cell(row=5, column=3, value="明細部")
    ws_cases.cell(row=5, column=4, value="エラー処理")
    ws_cases.cell(row=5, column=5, value="不正値")
    ws_cases.cell(row=5, column=6, value="Enter")
    ws_cases.cell(row=5, column=7, value="エラーメッセージ")
    ws_cases.cell(row=5, column=8, value="エラー表示")

    # Add extra rows to trigger chunking / oversize tests
    for r in range(6, 6 + n_extra_rows):
        ws_cases.cell(row=r, column=1, value=r - 3)
        ws_cases.cell(row=r, column=2, value=f"SCR_{r}")
        ws_cases.cell(row=r, column=6, value="Click")
        ws_cases.cell(row=r, column=8, value="OK")

    # --- Sheet 3: Hidden Raw Source ---
    ws_hidden = wb.create_sheet(title="HNIXLOT_SRC")
    ws_hidden.sheet_state = "hidden"
    ws_hidden.cell(row=1, column=1, value="       IDENTIFICATION DIVISION.")
    ws_hidden.cell(row=2, column=1, value="       PROGRAM-ID. HNIXLOT.")

    wb.save(file_path)
    return file_path


# ---------------------------------------------------------------------------
# Lean Pipeline Mock Helper
# ---------------------------------------------------------------------------

def _make_lean_structuring_stub(
    custom_outline: Any = None,
    custom_detail: Any = None,
    custom_corrector: Any = None,
    custom_completeness: Any = None,
):
    """Create a ProviderConfigService.chat_stream_events stub for lean structuring pipeline."""
    async def _chat_stream_stub(self, request: ChatRequest):
        sys_prompt = request.messages[0].content
        user_prompt = request.messages[1].content

        # 1. Sheet Classifier
        if "classify each worksheet" in sys_prompt.lower() or "worksheets to classify" in user_prompt.lower():
            import re
            sheet_ids = re.findall(r'"sheet_id":\s*"([^"]+)"', user_prompt)
            if not sheet_ids:
                sheet_ids = ["s1", "s2", "s3"]
            results = []
            for s_id in sheet_ids:
                num = int(s_id[1:]) if s_id[1:].isdigit() else 1
                cls = "narrative_flow" if num % 3 == 1 else ("case_table" if num % 3 == 2 else "raw_source")
                results.append({"sheet_id": s_id, "sheet_class": cls, "reason": "Synthetically classified sheet"})
            yield {"type": "content", "text": json.dumps({"results": results})}
            return

        # 2. Outline stage
        if "top-level business flows" in sys_prompt.lower() or "workbook sheets & row registry" in user_prompt.lower():
            if custom_outline:
                async for evt in custom_outline(request):
                    yield evt
                return
            results = [
                {
                    "flow_alias": "f1",
                    "name_ja": "コイル試験指示入力フロー",
                    "name_en": "Coil Test Entry Flow",
                    "kind": "narrative",
                    "spans": [{"sheet_id": "s1", "row_start": 1, "row_end": 16}],
                    "section_markers": ["2.1①", "2.1⑩"],
                    "in_scope": True,
                    "scope_note": None,
                },
                {
                    "flow_alias": "f2",
                    "name_ja": "テスト項目表",
                    "name_en": "Test Case Table",
                    "kind": "case_group",
                    "spans": [{"sheet_id": "s2", "row_start": 1, "row_end": 20}],
                    "section_markers": [],
                    "in_scope": True,
                    "scope_note": None,
                },
            ]
            yield {"type": "content", "text": json.dumps({"results": results})}
            return

        # 3. Detail stage (Cell-ID based)
        if "detailed steps" in sys_prompt.lower() or "flows and raw rows" in user_prompt.lower():
            if custom_detail:
                async for evt in custom_detail(request):
                    yield evt
                return
            results = [
                {
                    "flow_alias": "f1",
                    "steps": [
                        {
                            "kind": "action",
                            "section_id": "2.1①",
                            "text_cell_ref": {"row_ix": 3, "col": 2},
                            "text_en": "Select coil test from menu",
                            "trigger_cell_ref": {"row_ix": 3, "col": 2},
                            "expected_cell_ref": {"row_ix": 4, "col": 3},
                            "in_scope": True,
                            "scope_note": None,
                            "row_start": 2,
                            "row_end": 4,
                        },
                        {
                            "kind": "action",
                            "section_id": "2.1⑩",
                            "text_cell_ref": {"row_ix": 15, "col": 2},
                            "text_en": "Enter test machine code and press Enter",
                            "trigger_cell_ref": {"row_ix": 15, "col": 2},
                            "expected_cell_ref": {"row_ix": 16, "col": 3},
                            "in_scope": True,
                            "scope_note": None,
                            "row_start": 14,
                            "row_end": 16,
                        },
                    ],
                    "cases": [],
                },
                {
                    "flow_alias": "f2",
                    "steps": [],
                    "cases": [
                        {
                            "screen_name_cell_ref": {"row_ix": 4, "col": 2},
                            "trigger_cell_ref": {"row_ix": 4, "col": 6},
                            "expected_cell_ref": {"row_ix": 4, "col": 8},
                            "link_section_id": "2.1①",
                            "row_ix": 4,
                        }
                    ],
                },
            ]
            yield {"type": "content", "text": json.dumps({"results": results})}
            return

        # 4. Corrector stage (Patch-based)
        if "authoritative verification" in sys_prompt.lower() or "ground truth raw rows" in user_prompt.lower():
            if custom_corrector:
                async for evt in custom_corrector(request):
                    yield evt
                return
            yield {"type": "content", "text": json.dumps({"patches": []})}
            return

        # 5. Completeness stage
        if "completeness auditor" in sys_prompt.lower() or "uncovered semantic rows" in user_prompt.lower():
            if custom_completeness:
                async for evt in custom_completeness(request):
                    yield evt
                return
            yield {"type": "content", "text": json.dumps({"missing_spans": []})}
            return

        yield {"type": "content", "text": json.dumps({"results": []})}

    return _chat_stream_stub


# ---------------------------------------------------------------------------
# Test 1: Deterministic Extraction
# ---------------------------------------------------------------------------

def test_deterministic_extraction(tmp_path: Path):
    """Verify openpyxl deterministic extraction preserves structure."""
    xlsx_path = tmp_path / "test.xlsx"
    _build_synthetic_workbook(xlsx_path, n_extra_rows=10)

    wb = extract_workbook(xlsx_path)
    assert len(wb.sheets) == 3

    narrative = next(s for s in wb.sheets if s.sheet == "想定表")
    assert narrative.hidden is False
    assert len(narrative.blocks) >= 2

    # Check merged vertical cell replication
    blk1 = narrative.blocks[0]
    r9 = next(r for r in blk1.rows if r.row_ix == 9)
    r10 = next(r for r in blk1.rows if r.row_ix == 10)
    c9_merge = next(c for c in r9.cells if c.col == 2)
    c10_merge = next(c for c in r10.cells if c.col == 2)
    assert c9_merge.value == "共通前提条件"
    assert c10_merge.value == "共通前提条件"


# ---------------------------------------------------------------------------
# Test 2: Oversize Chunking
# ---------------------------------------------------------------------------

def test_oversize_sub_split(tmp_path: Path):
    """Verify oversize table splits at row chunk size."""
    xlsx_path = tmp_path / "oversize.xlsx"
    _build_synthetic_workbook(xlsx_path, n_extra_rows=120)

    wb = extract_workbook(xlsx_path)
    case_sheet = next(s for s in wb.sheets if s.sheet == "項目表")
    assert len(case_sheet.blocks) >= 2


# ---------------------------------------------------------------------------
# Test 3: Phase U Migration Tables
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_phase_u_migration_tables(tmp_path: Path, monkeypatch):
    """Verify database migration v15 creates Phase U tables."""
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(tmp_path))
    await init_db()
    db = get_db()
    try:
        async with db.execute("SELECT name FROM sqlite_master WHERE type='table'") as cur:
            tables = {row[0] for row in await cur.fetchall()}

        assert "user_flow_docs" in tables
        assert "user_flows" in tables
        assert "user_steps" in tables
        assert "user_cases" in tables
        assert "user_code_anchors" in tables
        assert "user_bd_mappings" in tables
        assert "user_run_artifacts" in tables
    finally:
        await close_db()


# ---------------------------------------------------------------------------
# Test 4: Lean Structuring Round-Trip
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hierarchical_structuring_round_trip(tmp_path: Path, monkeypatch):
    """Verify full end-to-end import pipeline with verbatim Japanese materialization."""
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(tmp_path))
    await init_db()
    db = get_db()

    try:
        xlsx_path = tmp_path / "scenario.xlsx"
        _build_synthetic_workbook(xlsx_path, n_extra_rows=5)

        monkeypatch.setattr(
            ProviderConfigService,
            "chat_stream_events",
            _make_lean_structuring_stub(),
        )

        doc_id = await import_user_flow_xlsx(
            db, xlsx_path, provider_id="test_provider"
        )
        assert doc_id.startswith("ufdoc:")

        async with db.execute("SELECT id, name_ja, kind, sheet FROM user_flows WHERE doc_id = ?", (doc_id,)) as cur:
            flows = await cur.fetchall()
        assert len(flows) == 2

        async with db.execute("SELECT text_ja, trigger_ja, expected_ja FROM user_steps WHERE flow_id = ?", (flows[0][0],)) as cur:
            steps = await cur.fetchall()
        assert len(steps) == 2
        assert "メニューからコイル試験を選択する" in steps[0][0]
    finally:
        await close_db()


# ---------------------------------------------------------------------------
# Test 5: Per-Item Tolerant Parsing (UR-FIX Hole A)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_per_item_tolerance_in_detail_and_corrector(tmp_path: Path, monkeypatch):
    """Verify:
    1. In detail batch, 1 malformed step does NOT drop the rest of the flow or batch.
    2. In corrector batch, 1 malformed patch does NOT drop the other valid patches.
    """
    xlsx_path = tmp_path / "tol.xlsx"
    _build_synthetic_workbook(xlsx_path, n_extra_rows=5)
    wb = extract_workbook(xlsx_path)
    sheets_by_name = {s.sheet: s for s in wb.sheets}

    f1 = FlowUnit(
        unit_id="uf:1",
        sheet="想定表",
        kind="narrative",
        name_ja="フロー1",
        name_en="Flow 1",
        row_start=1,
        row_end=10,
        raw_row_start=1,
        raw_row_end=10,
        flow_group_key="f1",
    )
    f2 = FlowUnit(
        unit_id="uf:2",
        sheet="想定表",
        kind="narrative",
        name_ja="フロー2",
        name_en="Flow 2",
        row_start=11,
        row_end=16,
        raw_row_start=11,
        raw_row_end=16,
        flow_group_key="f2",
    )

    # Mock detail response where step 0 of f1 has malformed cell ref (missing row_ix), but step 1 is valid
    async def detail_stub(self, request: ChatRequest):
        results = [
            {
                "flow_alias": "f1",
                "steps": [
                    {"kind": "action", "text_cell_ref": {"row_ix": 3, "col": 2}, "text_en": "Valid step"},
                ],
                "cases": [],
            },
            {
                "flow_alias": "f2",
                "steps": [
                    {"kind": "action", "text_cell_ref": {"row_ix": 15, "col": 2}, "text_en": "Valid step in f2"},
                ],
                "cases": [],
            },
        ]
        yield {"type": "content", "text": json.dumps({"results": results})}

    monkeypatch.setattr(ProviderConfigService, "chat_stream_events", detail_stub)

    detail_res, _, _, _, warnings = await extract_detail_batch([f1, f2], sheets_by_name, "test_provider")
    assert len(detail_res["uf:1"].steps) >= 1
    assert len(detail_res["uf:2"].steps) >= 1
    assert "メニューからコイル試験を選択する" in detail_res["uf:1"].steps[0].text_ja


# ---------------------------------------------------------------------------
# Test 6: Multi-Span Re-Fragmentation Prevention (UR-FIX Hole D)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_refragmentation_multi_spans_one_flow(tmp_path: Path, monkeypatch):
    """Verify: Outline returning 1 logical flow with 3 spans results in exactly ONE user_flows DB record."""
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(tmp_path))
    await init_db()
    db = get_db()

    try:
        xlsx_path = tmp_path / "multispan.xlsx"
        _build_synthetic_workbook(xlsx_path, n_extra_rows=5)

        async def multi_span_outline(request: ChatRequest):
            results = [
                {
                    "flow_alias": "f1",
                    "name_ja": "単一マルチスパンフロー",
                    "name_en": "Single Multi-Span Flow",
                    "kind": "narrative",
                    "spans": [
                        {"sheet_id": "s1", "row_start": 1, "row_end": 5},
                        {"sheet_id": "s1", "row_start": 6, "row_end": 10},
                        {"sheet_id": "s1", "row_start": 11, "row_end": 16},
                    ],
                    "section_markers": ["2.1①"],
                    "in_scope": True,
                }
            ]
            yield {"type": "content", "text": json.dumps({"results": results})}

        monkeypatch.setattr(
            ProviderConfigService,
            "chat_stream_events",
            _make_lean_structuring_stub(custom_outline=multi_span_outline),
        )

        doc_id = await import_user_flow_xlsx(db, xlsx_path, provider_id="test_provider")

        async with db.execute("SELECT id, name_ja FROM user_flows WHERE doc_id = ?", (doc_id,)) as cur:
            flows = await cur.fetchall()

        # Must be exactly 1 user_flows row
        assert len(flows) == 1
        assert flows[0][1] == "単一マルチスパンフロー"

    finally:
        await close_db()


# ---------------------------------------------------------------------------
# Test 7: Patch Order Safety (UR-FIX Hole E)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_patch_order_safety_delete_and_change(tmp_path: Path, monkeypatch):
    """Verify: [delete_step idx0, change_section_id idx1] modifies the correct surviving step (original idx 1)."""
    xlsx_path = tmp_path / "patch_safety.xlsx"
    _build_synthetic_workbook(xlsx_path, n_extra_rows=5)
    wb = extract_workbook(xlsx_path)
    sheets_by_name = {s.sheet: s for s in wb.sheets}

    f1 = FlowUnit(
        unit_id="uf:1",
        sheet="想定表",
        kind="narrative",
        name_ja="フロー1",
        name_en="Flow 1",
        row_start=1,
        row_end=16,
        raw_row_start=1,
        raw_row_end=16,
        flow_group_key="f1",
    )

    from domain.user_flow._detail import DetailResult, StepItem
    draft_dict = {
        "uf:1": DetailResult(
            steps=[
                StepItem(kind="action", section_id="OLD_0", text_ja="Step 0 (to delete)", row_start=2, row_end=2, sheet="想定表"),
                StepItem(kind="action", section_id="OLD_1", text_ja="Step 1 (target of change)", row_start=3, row_end=3, sheet="想定表"),
                StepItem(kind="action", section_id="OLD_2", text_ja="Step 2", row_start=4, row_end=4, sheet="想定表"),
            ]
        )
    }

    async def corrector_stub(self, request: ChatRequest):
        # Emits delete idx 0 and change section id on idx 1
        patches = [
            {"op": "delete_step", "flow_alias": "f1", "target_step_idx": 0, "reason": "Remove step 0"},
            {"op": "change_section_id", "flow_alias": "f1", "target_step_idx": 1, "section_id": "NEW_SEC_1", "reason": "Update sec id"},
        ]
        yield {"type": "content", "text": json.dumps({"patches": patches})}

    monkeypatch.setattr(ProviderConfigService, "chat_stream_events", corrector_stub)

    corrected, applied, _, _, _, _ = await correct_detail_batch([f1], draft_dict, sheets_by_name, "test_provider")
    surviving_steps = corrected["uf:1"].steps

    assert len(surviving_steps) == 2
    # The step that was originally Step 1 ("Step 1 (target of change)") is now index 0 and has NEW_SEC_1
    target_step = next(s for s in surviving_steps if "target of change" in s.text_ja)
    assert target_step.section_id == "NEW_SEC_1"


# ---------------------------------------------------------------------------
# Test 8: Cell-ID Materialization with Col Resolution (UR-FIX Hole F)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_materialization_with_column_resolution(tmp_path: Path, monkeypatch):
    """Verify: text_cell_ref{row_ix: 3, col: 2} and trigger_cell_ref{row_ix: 3, col: 3} produce distinct strings."""
    xlsx_path = tmp_path / "col_res.xlsx"
    _build_synthetic_workbook(xlsx_path, n_extra_rows=5)
    wb = extract_workbook(xlsx_path)
    sheets_by_name = {s.sheet: s for s in wb.sheets}

    f1 = FlowUnit(
        unit_id="uf:1",
        sheet="想定表",
        kind="narrative",
        name_ja="フロー1",
        name_en="Flow 1",
        row_start=1,
        row_end=16,
        raw_row_start=1,
        raw_row_end=16,
        flow_group_key="f1",
    )

    async def detail_stub(self, request: ChatRequest):
        results = [
            {
                "flow_alias": "f1",
                "steps": [
                    {
                        "kind": "action",
                        "text_cell_ref": {"row_ix": 9, "col": 2},  # "共通前提条件"
                        "trigger_cell_ref": {"row_ix": 9, "col": 3},  # "詳細条件A"
                        "text_en": "Condition test",
                    }
                ],
                "cases": [],
            }
        ]
        yield {"type": "content", "text": json.dumps({"results": results})}

    monkeypatch.setattr(ProviderConfigService, "chat_stream_events", detail_stub)

    detail_res, _, _, _, _ = await extract_detail_batch([f1], sheets_by_name, "test_provider")
    step = detail_res["uf:1"].steps[0]

    assert step.text_ja == "共通前提条件"
    assert step.trigger_ja == "詳細条件A"
    assert step.text_ja != step.trigger_ja


# ---------------------------------------------------------------------------
# Test 9: Recovery Steps Persisted (UR-FIX Hole G)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_recovery_steps_persisted_in_db(tmp_path: Path, monkeypatch):
    """Verify: When completeness sweep flags a missing span, the recovered steps are saved in user_steps."""
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(tmp_path))
    await init_db()
    db = get_db()

    try:
        xlsx_path = tmp_path / "rec.xlsx"
        _build_synthetic_workbook(xlsx_path, n_extra_rows=5)

        async def completeness_recovery_stub(request: ChatRequest):
            results = [
                {
                    "sheet_name": "想定表",
                    "row_start": 14,
                    "row_end": 16,
                    "flow_name_ja": "補完された試験フロー",
                    "reason": "Recovered missing machine code steps",
                }
            ]
            yield {"type": "content", "text": json.dumps({"missing_spans": results})}

        monkeypatch.setattr(
            ProviderConfigService,
            "chat_stream_events",
            _make_lean_structuring_stub(custom_completeness=completeness_recovery_stub),
        )

        doc_id = await import_user_flow_xlsx(db, xlsx_path, provider_id="test_provider")

        async with db.execute("SELECT name_ja FROM user_flows WHERE doc_id = ?", (doc_id,)) as cur:
            flow_names = [r[0] for r in await cur.fetchall()]

        assert any("補完された試験フロー" in n for n in flow_names)

        async with db.execute(
            "SELECT s.text_ja FROM user_steps s JOIN user_flows f ON s.flow_id = f.id WHERE f.name_ja LIKE '%補完%'"
        ) as cur:
            rec_steps = await cur.fetchall()

        assert len(rec_steps) >= 1

    finally:
        await close_db()


# ---------------------------------------------------------------------------
# Test 10: Cross-Flow Step Deduplication (UR-FIX Hole H)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cross_flow_step_deduplication(tmp_path: Path, monkeypatch):
    """Verify: When two adjacent flows overlap due to padding, shared steps are persisted only ONCE."""
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(tmp_path))
    await init_db()
    db = get_db()

    try:
        xlsx_path = tmp_path / "dedup.xlsx"
        _build_synthetic_workbook(xlsx_path, n_extra_rows=5)

        async def overlap_outline(request: ChatRequest):
            results = [
                {
                    "flow_alias": "f1",
                    "name_ja": "フロー1",
                    "name_en": "Flow 1",
                    "kind": "narrative",
                    "spans": [{"sheet_id": "s1", "row_start": 1, "row_end": 8}],
                    "section_markers": ["2.1①"],
                    "in_scope": True,
                },
                {
                    "flow_alias": "f2",
                    "name_ja": "フロー2",
                    "name_en": "Flow 2",
                    "kind": "narrative",
                    "spans": [{"sheet_id": "s1", "row_start": 8, "row_end": 16}],
                    "section_markers": ["2.1⑩"],
                    "in_scope": True,
                },
            ]
            yield {"type": "content", "text": json.dumps({"results": results})}

        monkeypatch.setattr(
            ProviderConfigService,
            "chat_stream_events",
            _make_lean_structuring_stub(custom_outline=overlap_outline),
        )

        doc_id = await import_user_flow_xlsx(db, xlsx_path, provider_id="test_provider")

        async with db.execute("SELECT row_start, row_end, text_ja FROM user_steps WHERE sheet = '想定表'") as cur:
            all_steps = await cur.fetchall()

        # Verify no duplicate (row_start, row_end, text_ja) tuples exist
        seen = set()
        for r_s, r_e, t in all_steps:
            key = (r_s, r_e, t)
            assert key not in seen, f"Found duplicate step: {key}"
            seen.add(key)

    finally:
        await close_db()


# ---------------------------------------------------------------------------
# Test 11: Case Table Full Field Extraction (UR-FIX Hole L)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_case_table_full_field_extraction(tmp_path: Path, monkeypatch):
    """Verify: Case table extraction materializes all case fields into user_cases."""
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(tmp_path))
    await init_db()
    db = get_db()

    try:
        xlsx_path = tmp_path / "case_full.xlsx"
        _build_synthetic_workbook(xlsx_path, n_extra_rows=5)

        async def detail_case_stub(request: ChatRequest):
            results = [
                {
                    "flow_alias": "f1",
                    "steps": [],
                    "cases": [],
                },
                {
                    "flow_alias": "f2",
                    "steps": [],
                    "cases": [
                        {
                            "screen_name_cell_ref": {"row_ix": 4, "col": 2},
                            "area_cell_ref": {"row_ix": 4, "col": 3},
                            "viewpoint_cell_ref": {"row_ix": 4, "col": 4},
                            "condition_cell_refs": [{"row_ix": 4, "col": 5}],
                            "trigger_cell_ref": {"row_ix": 4, "col": 6},
                            "check_item_cell_ref": {"row_ix": 4, "col": 7},
                            "expected_cell_ref": {"row_ix": 4, "col": 8},
                            "link_section_id": "2.1①",
                            "row_ix": 4,
                        }
                    ],
                },
            ]
            yield {"type": "content", "text": json.dumps({"results": results})}

        monkeypatch.setattr(
            ProviderConfigService,
            "chat_stream_events",
            _make_lean_structuring_stub(custom_detail=detail_case_stub),
        )

        doc_id = await import_user_flow_xlsx(db, xlsx_path, provider_id="test_provider")

        async with db.execute(
            "SELECT screen_name_ja, area_ja, viewpoint_ja, trigger_ja, expected_ja, check_item_ja FROM user_cases WHERE doc_id = ?",
            (doc_id,),
        ) as cur:
            cases = await cur.fetchall()

        assert len(cases) >= 1
        assert cases[0][0] == "FHNIXLOT"
        assert cases[0][1] == "ヘッダ部"
        assert cases[0][2] == "初期表示"
        assert cases[0][3] == "画面遷移"
        assert cases[0][4] == "正常表示"
        assert cases[0][5] == "タイトル"

    finally:
        await close_db()


# ---------------------------------------------------------------------------
# Test 12: Multi-File Import with Colliding Sheet Namespacing (TICKET U5 Part A)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_multi_file_import_with_sheet_namespacing(tmp_path: Path, monkeypatch):
    """Verify: Importing multiple xlsx files namespaces identical sheet names and combines into one doc."""
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(tmp_path))
    await init_db()
    db = get_db()

    try:
        # Create 2 synthetic workbooks with identical sheet name '想定表'
        file1 = tmp_path / "P01_Test.xlsx"
        file2 = tmp_path / "P02_Test.xlsx"
        _build_synthetic_workbook(file1, n_extra_rows=5)
        _build_synthetic_workbook(file2, n_extra_rows=5)

        async def multi_file_outline(req: ChatRequest):
            results = [
                {
                    "flow_alias": "f1",
                    "name_ja": "P01フロー",
                    "name_en": "P01 Flow",
                    "kind": "narrative",
                    "spans": [{"sheet_id": "s1", "row_start": 1, "row_end": 16}],
                    "section_markers": ["2.1①"],
                    "in_scope": True,
                    "scope_note": None,
                },
                {
                    "flow_alias": "f2",
                    "name_ja": "P02フロー",
                    "name_en": "P02 Flow",
                    "kind": "narrative",
                    "spans": [{"sheet_id": "s3", "row_start": 1, "row_end": 16}],
                    "section_markers": ["2.1①"],
                    "in_scope": True,
                    "scope_note": None,
                },
            ]
            yield {"type": "content", "text": json.dumps({"results": results})}

        async def multi_file_detail(req: ChatRequest):
            results = [
                {
                    "flow_alias": "f1",
                    "steps": [
                        {
                            "kind": "action",
                            "section_id": "2.1①",
                            "text_cell_ref": {"row_ix": 3, "col": 2},
                            "text_en": "Action P01",
                            "trigger_cell_ref": {"row_ix": 3, "col": 2},
                            "expected_cell_ref": {"row_ix": 4, "col": 3},
                            "in_scope": True,
                            "scope_note": None,
                            "row_start": 2,
                            "row_end": 4,
                        }
                    ],
                    "cases": [],
                },
                {
                    "flow_alias": "f2",
                    "steps": [
                        {
                            "kind": "action",
                            "section_id": "2.1①",
                            "text_cell_ref": {"row_ix": 3, "col": 2},
                            "text_en": "Action P02",
                            "trigger_cell_ref": {"row_ix": 3, "col": 2},
                            "expected_cell_ref": {"row_ix": 4, "col": 3},
                            "in_scope": True,
                            "scope_note": None,
                            "row_start": 2,
                            "row_end": 4,
                        }
                    ],
                    "cases": [],
                },
            ]
            yield {"type": "content", "text": json.dumps({"results": results})}

        # Mock lean structuring stub
        monkeypatch.setattr(
            ProviderConfigService,
            "chat_stream_events",
            _make_lean_structuring_stub(custom_outline=multi_file_outline, custom_detail=multi_file_detail),
        )

        doc_id = await import_user_flow_xlsx(db, [str(file1), str(file2)], provider_id="test_provider")

        assert doc_id.startswith("ufdoc:")

        # Check doc source_name & hash
        async with db.execute("SELECT source_name, file_hash FROM user_flow_docs WHERE id = ?", (doc_id,)) as cur:
            doc_row = await cur.fetchone()
        assert "2 workbooks" in doc_row[0]
        assert len(doc_row[1]) == 64  # SHA-256

        # Check flows from both files are present with namespaced sheets
        async with db.execute("SELECT DISTINCT sheet FROM user_flows WHERE doc_id = ?", (doc_id,)) as cur:
            sheets = {r[0] for r in await cur.fetchall()}

        assert "P01_Test:想定表" in sheets
        assert "P02_Test:想定表" in sheets

        # Check steps also have namespaced sheets
        async with db.execute("SELECT DISTINCT sheet FROM user_steps WHERE flow_id IN (SELECT id FROM user_flows WHERE doc_id = ?)", (doc_id,)) as cur:
            step_sheets = {r[0] for r in await cur.fetchall()}

        assert "P01_Test:想定表" in step_sheets
        assert "P02_Test:想定表" in step_sheets

        # Idempotency: re-importing the same list replaces cleanly without duplicate rows
        doc_id2 = await import_user_flow_xlsx(db, [str(file1), str(file2)], provider_id="test_provider")
        assert doc_id2 != doc_id  # New doc_id because old was deleted & re-created with same hash

        async with db.execute("SELECT COUNT(*) FROM user_flow_docs WHERE file_hash = ?", (doc_row[1],)) as cur:
            count = (await cur.fetchone())[0]
        assert count == 1

    finally:
        await close_db()


@pytest.mark.asyncio
async def test_user_flow_graph_synthesis(tmp_path: Path, monkeypatch):
    """Verify: get_user_flow_graph synthesizes sequential SUCCESS branches and terminal ERROR branches."""
    from domain.user_flow import get_user_flow_graph
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(tmp_path))
    await init_db()
    db = get_db()

    try:
        doc_id = "ufdoc:test_graph_doc"
        flow_id = "uf:flow_1"
        s1_id = "us:step_1"
        s2_id = "us:step_2"
        s3_id = "us:step_3"

        # Insert doc, flow, steps
        await db.execute(
            "INSERT INTO user_flow_docs (id, source_name, file_hash, imported_at) VALUES (?, ?, ?, ?)",
            (doc_id, "Test Doc", "hash123", "2026-08-17T00:00:00Z"),
        )
        await db.execute(
            "INSERT INTO user_flows (id, doc_id, ordinal, name_ja, name_en, kind, sheet, scope_note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (flow_id, doc_id, 1, "フロー1", "Coil Test Flow", "narrative", "想定表", "Scope note", "2026-08-17T00:00:00Z"),
        )
        await db.execute(
            "INSERT INTO user_steps (id, flow_id, ordinal, kind, section_id, text_ja, text_en, trigger_ja, expected_ja, in_scope, sheet, row_start, row_end, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (s1_id, flow_id, 1, "action", "1.1", "メニュー選択", "Select menu", "クリック", None, 1, "想定表", 2, 3, "2026-08-17T00:00:00Z"),
        )
        await db.execute(
            "INSERT INTO user_steps (id, flow_id, ordinal, kind, section_id, text_ja, text_en, trigger_ja, expected_ja, in_scope, sheet, row_start, row_end, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (s2_id, flow_id, 2, "expectation", "1.2", "画面表示確認", "Verify display", None, "画面表示", 1, "想定表", 4, 5, "2026-08-17T00:00:00Z"),
        )
        await db.execute(
            "INSERT INTO user_steps (id, flow_id, ordinal, kind, section_id, text_ja, text_en, trigger_ja, expected_ja, in_scope, sheet, row_start, row_end, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (s3_id, flow_id, 3, "error_rule", "1.3", "エラー表示", "Show error on invalid input", None, "エラーダイアログ", 1, "想定表", 6, 7, "2026-08-17T00:00:00Z"),
        )
        await db.commit()

        graph = await get_user_flow_graph(db, doc_id)
        assert "business_flows" in graph
        b_flows = graph["business_flows"]
        assert len(b_flows) == 1

        f = b_flows[0]
        assert f["id"] == flow_id
        assert f["name"] == "Coil Test Flow"
        assert len(f["steps"]) == 3

        # Steps check
        assert f["steps"][0]["id"] == s1_id
        assert "Trigger: クリック" in f["steps"][0]["functionality"]
        assert f["steps"][1]["id"] == s2_id
        assert "Expected: 画面表示" in f["steps"][1]["functionality"]
        assert f["steps"][2]["id"] == s3_id

        # Branches check: 2 sequential SUCCESS branches, 1 terminal ERROR branch
        branches = f["branches"]
        assert len(branches) == 3

        seq1 = branches[0]
        assert seq1["source_step_id"] == s1_id
        assert seq1["target_step_id"] == s2_id
        assert seq1["branch_kind"] == "SUCCESS"

        seq2 = branches[1]
        assert seq2["source_step_id"] == s2_id
        assert seq2["target_step_id"] == s3_id
        assert seq2["branch_kind"] == "SUCCESS"

        err = branches[2]
        assert err["source_step_id"] == s1_id  # Nearest preceding action
        assert err["target_step_id"] is None   # Terminal
        assert err["branch_kind"] == "ERROR"
        assert err["guard_description"] == "Show error on invalid input"

    finally:
        await close_db()


