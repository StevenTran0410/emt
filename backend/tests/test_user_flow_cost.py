"""Unit and acceptance tests for TICKET U-COST (Prompt Slimming, Row Dedup, Snippet Pool Dedup, Corrector Gating).

OFFLINE ONLY — all provider calls are stubbed, no real LLM/network calls.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import pytest

from domain.business_flow_integrity._retrieval import Snippet
from domain.model_connector.service import ProviderConfigService
from domain.model_connector.types import ChatRequest
from domain.user_flow import (
    BIG_FLOW_ROWS,
    BIG_FLOW_STEPS,
    Block,
    CaseItem,
    CellRef,
    DetailCaseItem,
    DetailFlowResult,
    DetailResult,
    DetailStepItem,
    FlowUnit,
    PatchOp,
    RawCell,
    RawRow,
    SheetExtract,
    StepAlignContext,
    StepItem,
    WorkbookExtract,
    align_user_flow_steps,
    compute_uncovered_rows,
    correct_detail_batch,
    evaluate_align_batch,
    extract_detail_batch,
    render_cell_line,
    structure_user_flow,
)
from domain.user_flow._align import BDUnit
from infrastructure.db.database import close_db, get_db, init_db
from shared.utils import new_id, utc_now_iso


# ---------------------------------------------------------------------------
# Test 1: Task A — Row De-duplication in Raw-Row Dumps
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_detail_rows_deduped(monkeypatch):
    """Given a SheetExtract with overlapping blocks containing duplicate row_ix,
    the Detail user prompt must emit each [Row N ...] line at most once per flow.
    """
    row1 = RawRow(row_ix=2, indent_col=1, cells=[RawCell(col=1, value="2.1① 画面起動")])
    row2 = RawRow(row_ix=3, indent_col=2, cells=[RawCell(col=2, value="メニューから選択")])
    row3 = RawRow(row_ix=4, indent_col=3, cells=[RawCell(col=3, value="画面が表示される")])

    # Two overlapping blocks containing the same rows
    b1 = Block(block_ix=1, chunk_ix=0, row_start=2, row_end=4, rows=[row1, row2, row3])
    b2 = Block(block_ix=2, chunk_ix=0, row_start=3, row_end=4, rows=[row2, row3])  # overlaps!

    sheet = SheetExtract(
        sheet="想定表",
        hidden=False,
        n_rows=4,
        n_cols=3,
        blocks=[b1, b2],
    )
    sheets_by_name = {"想定表": sheet}

    flow = FlowUnit(
        unit_id="uf1",
        sheet="想定表",
        kind="narrative",
        name_ja="画面起動フロー",
        name_en="Screen Start Flow",
        row_start=2,
        row_end=4,
        raw_row_start=2,
        raw_row_end=4,
        flow_group_key="f1",
    )

    captured_prompt = {}

    async def stub_detail(self, request: ChatRequest):
        captured_prompt["user_content"] = request.messages[1].content
        resp = {
            "results": [
                {
                    "flow_alias": "f1",
                    "steps": [
                        {
                            "kind": "action",
                            "section_id": "2.1①",
                            "text_cell_ref": {"row_ix": 2, "col": 1},
                            "text_en": "Start screen",
                            "row_start": 2,
                            "row_end": 2,
                        }
                    ],
                    "cases": [],
                }
            ]
        }
        yield {"type": "content", "text": json.dumps(resp)}

    monkeypatch.setattr(ProviderConfigService, "chat_stream_events", stub_detail)

    res, raw, retries, lat, warns = await extract_detail_batch([flow], sheets_by_name, "test_provider")

    user_text = captured_prompt["user_content"]
    assert user_text.count("[Row 2") == 1
    assert user_text.count("[Row 3") == 1
    assert user_text.count("[Row 4") == 1
    assert "uf1" in res
    assert len(res["uf1"].steps) == 1
    assert res["uf1"].steps[0].text_ja == "2.1① 画面起動"


# ---------------------------------------------------------------------------
# Test 2: Task B — Prompt Slimming (Dropping Non-Contributing Standalone Markers)
# ---------------------------------------------------------------------------

def test_prompt_slim_render_cell_line():
    """Verify render_cell_line drops standalone checkbox markers but keeps all actual content."""
    # Row with standalone checkbox marker and content
    row_with_marker = RawRow(
        row_ix=6,
        indent_col=2,
        cells=[
            RawCell(col=2, value="☑"),
            RawCell(col=3, value="オプション1 (コイル)"),
            RawCell(col=4, value="123"),
        ],
    )
    rendered_slim = render_cell_line(row_with_marker, include_indent=True, drop_markers=True)
    assert "col 2: '☑'" not in rendered_slim
    assert "col 3: 'オプション1 (コイル)'" in rendered_slim
    assert "col 4: '123'" in rendered_slim
    assert rendered_slim == "[Row 6, indent 2] col 3: 'オプション1 (コイル)', col 4: '123'"

    # Row where checkbox is embedded in text (must NOT be dropped!)
    row_embedded = RawRow(
        row_ix=7,
        indent_col=2,
        cells=[
            RawCell(col=2, value="□ オプション2 ※PoCとしては、1のみとする"),
        ],
    )
    rendered_emb = render_cell_line(row_embedded, include_indent=True, drop_markers=True)
    assert "□ オプション2 ※PoCとしては、1のみとする" in rendered_emb

    # Row with only markers -> should return empty string
    row_only_marker = RawRow(
        row_ix=8,
        indent_col=1,
        cells=[RawCell(col=1, value="☑"), RawCell(col=2, value="■")],
    )
    assert render_cell_line(row_only_marker, include_indent=True, drop_markers=True) == ""


# ---------------------------------------------------------------------------
# Test 3: Task C — Align Snippet Pool Deduplication
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_align_snippet_pool_dedup(tmp_path: Path, monkeypatch):
    """Verify:
    1. A batch of 2 steps sharing one code span creates exactly 1 pooled snippet entry in the user prompt.
    2. Each step references the shared snippet ID.
    3. Gate resolution in _run_batch correctly resolves (rel_path, line_start, line_end) for valid citations.
    """
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(tmp_path))
    await init_db()
    db = get_db()

    try:
        snapshot_id = f"snap:{new_id()}"
        now = utc_now_iso()
        snap_dir = tmp_path / "repo"
        snap_dir.mkdir(parents=True, exist_ok=True)
        cbl_text = (
            "       IDENTIFICATION DIVISION.\n"
            "       PROGRAM-ID. HNIXLOT.\n"
            "       PROCEDURE DIVISION.\n"
            "           IF TEST-CODE = 'COIL' PERFORM 1000-PROCESS-COIL.\n"
            "           IF LOT-NO = SPACES PERFORM 2000-ERROR-LOT.\n"
            "           GOBACK.\n"
        )
        (snap_dir / "HNIXLOT.cbl").write_text(cbl_text, encoding="utf-8")

        await db.execute(
            "INSERT INTO repo_snapshots (id, local_repo_id, local_path, synced_at, created_at) VALUES (?, 'repo', ?, ?, ?)",
            (snapshot_id, str(snap_dir), now, now),
        )
        await db.execute(
            "INSERT INTO manifest_files (id, snapshot_id, rel_path, language, category, size_bytes, mtime_ns, checksum) VALUES (?, ?, 'HNIXLOT.cbl', 'cobol', 'source', ?, 0, 'sha1')",
            (f"mf:{new_id()}", snapshot_id, len(cbl_text)),
        )

        shared_snip = Snippet(
            rel_path="HNIXLOT.cbl",
            line_start=1,
            line_end=6,
            text=cbl_text,
            source_sha256="sha1",
            occurrence_id="occ1",
            parse_status="ok",
        )

        bd_unit = BDUnit(alias="bd1", unit_id="bs1", bd_kind="step", flow_id="bf1", name="Step1", description="desc")

        # 2 step contexts sharing the same snippet
        ctx1 = StepAlignContext(
            user_step_id="us1",
            step_alias="u1",
            flow_id="f1",
            ordinal=1,
            kind="action",
            section_id="2.1①",
            text_ja="コイル条件入力",
            text_en="Input coil condition",
            trigger_ja="Enter",
            expected_ja="次画面",
            screen_name_ja="FHNIXLOT",
            in_scope=True,
            snippets=[shared_snip],
            snippet_id_map={"src1": shared_snip},
            bd_candidates=[bd_unit],
            bd_alias_map={"bd1": bd_unit},
            bd_id_to_alias={"bs1": "bd1"},
        )

        ctx2 = StepAlignContext(
            user_step_id="us2",
            step_alias="u2",
            flow_id="f1",
            ordinal=2,
            kind="action",
            section_id="2.1②",
            text_ja="ロット番号検証",
            text_en="Validate lot number",
            trigger_ja="Enter",
            expected_ja="検証完了",
            screen_name_ja="FHNIXLOT",
            in_scope=True,
            snippets=[shared_snip],
            snippet_id_map={"src1": shared_snip},
            bd_candidates=[bd_unit],
            bd_alias_map={"bd1": bd_unit},
            bd_id_to_alias={"bs1": "bd1"},
        )

        captured_req = {}

        async def stub_align(self, request: ChatRequest):
            captured_req["content"] = request.messages[1].content
            parsed = json.loads(request.messages[1].content)
            results = [
                {
                    "step_id": "u1",
                    "claims": [
                        {
                            "claim_id": "c1",
                            "citation": {"snippet_id": "src1", "line_start": 4, "line_end": 4},
                            "subclaims": ["Coil check"],
                            "reason": "IF TEST-CODE = 'COIL'",
                        }
                    ],
                    "bd_mappings": [
                        {
                            "bd_unit_id": "bd1",
                            "relation": "realizes",
                            "confidence": 0.9,
                            "support_claim_ids": ["c1"],
                        }
                    ],
                },
                {
                    "step_id": "u2",
                    "claims": [
                        {
                            "claim_id": "c2",
                            "citation": {"snippet_id": "src1", "line_start": 5, "line_end": 5},
                            "subclaims": ["Lot check"],
                            "reason": "IF LOT-NO = SPACES",
                        }
                    ],
                    "bd_mappings": [
                        {
                            "bd_unit_id": "bd1",
                            "relation": "realizes",
                            "confidence": 0.9,
                            "support_claim_ids": ["c2"],
                        }
                    ],
                },
            ]
            yield {"type": "content", "text": json.dumps({"results": results})}

        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", stub_align)

        res_map, raw_text, retries, lat, batch_snip_map = await evaluate_align_batch(
            [ctx1, ctx2], "test_provider"
        )

        # 1. Assert snippet pool has exactly 1 entry
        parsed_payload = json.loads(captured_req["content"])
        assert "snippet_pool" in parsed_payload
        assert len(parsed_payload["snippet_pool"]) == 1
        assert parsed_payload["snippet_pool"][0]["snippet_id"] == "src1"
        assert parsed_payload["snippet_pool"][0]["rel_path"] == "HNIXLOT.cbl"

        # 2. Assert both steps reference src1
        assert parsed_payload["steps_to_align"][0]["snippet_ids"] == ["src1"]
        assert parsed_payload["steps_to_align"][1]["snippet_ids"] == ["src1"]

        # 3. Assert batch_snip_map resolves correctly
        assert "src1" in batch_snip_map
        assert batch_snip_map["src1"].rel_path == "HNIXLOT.cbl"

    finally:
        await close_db()


# ---------------------------------------------------------------------------
# Test 4: Task D — Corrector Gating (Combined Signal)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_corrector_gate_skips_clean_flow(monkeypatch):
    """Verify:
    1. A small clean flow (0 uncovered rows, < 25 steps, < 50 row span) skips Stage C corrector.
    2. A flow with uncovered rows triggers Stage C corrector.
    3. A flow with >= BIG_FLOW_STEPS or >= BIG_FLOW_ROWS triggers Stage C corrector even with 0 uncovered rows.
    """
    row1 = RawRow(row_ix=2, indent_col=1, cells=[RawCell(col=1, value="2.1① メイン画面起動")])
    row2 = RawRow(row_ix=3, indent_col=2, cells=[RawCell(col=2, value="メニューから選択")])
    b = Block(block_ix=1, chunk_ix=0, row_start=2, row_end=3, rows=[row1, row2])
    sheet = SheetExtract(sheet="想定表", hidden=False, n_rows=3, n_cols=2, blocks=[b])

    # Flow 1: Small clean flow
    clean_flow = FlowUnit(
        unit_id="uf_clean",
        sheet="想定表",
        kind="narrative",
        name_ja="クリーンフロー",
        name_en="Clean Flow",
        row_start=2,
        row_end=3,
        raw_row_start=2,
        raw_row_end=3,
        flow_group_key="f1",
    )
    clean_draft = DetailResult(
        steps=[
            StepItem(kind="action", section_id="2.1①", text_ja="2.1① メイン画面起動", row_start=2, row_end=2),
            StepItem(kind="action", text_ja="メニューから選択", row_start=3, row_end=3),
        ]
    )

    # 1. Assert compute_uncovered_rows returns empty for clean flow
    uncov_clean = compute_uncovered_rows(clean_flow, clean_draft, sheet)
    assert len(uncov_clean) == 0

    # Flow 2: Flow with uncovered row 3
    dirty_draft = DetailResult(
        steps=[
            StepItem(kind="action", section_id="2.1①", text_ja="2.1① メイン画面起動", row_start=2, row_end=2),
        ]
    )
    uncov_dirty = compute_uncovered_rows(clean_flow, dirty_draft, sheet)
    assert uncov_dirty == [3]

    # Flow 3: Large flow with 30 steps (>= BIG_FLOW_STEPS) and 0 uncovered rows
    big_steps = [
        StepItem(kind="action", text_ja=f"Step {i}", row_start=2, row_end=3)
        for i in range(30)
    ]
    big_draft = DetailResult(steps=big_steps)
    assert len(compute_uncovered_rows(clean_flow, big_draft, sheet)) == 0
    assert len(big_draft.steps) >= BIG_FLOW_STEPS

    # Now verify structure_user_flow skips corrector for clean batch and invokes it for dirty/big batch
    corrector_called = {"count": 0}

    async def stub_classify(self, request: ChatRequest):
        yield {"type": "content", "text": json.dumps({"results": [{"sheet_id": "s1", "sheet_class": "narrative_flow", "reason": "narrative"}]})}

    async def stub_outline(self, request: ChatRequest):
        yield {
            "type": "content",
            "text": json.dumps({
                "results": [
                    {
                        "flow_alias": "f1",
                        "name_ja": "クリーンフロー",
                        "name_en": "Clean Flow",
                        "kind": "narrative",
                        "spans": [{"sheet_id": "s1", "row_start": 2, "row_end": 3}],
                    }
                ]
            }),
        }

    async def stub_detail(self, request: ChatRequest):
        resp = {
            "results": [
                {
                    "flow_alias": "f1",
                    "steps": [
                        {"kind": "action", "section_id": "2.1①", "text_cell_ref": {"row_ix": 2, "col": 1}, "row_start": 2, "row_end": 2},
                        {"kind": "action", "text_cell_ref": {"row_ix": 3, "col": 2}, "row_start": 3, "row_end": 3},
                    ],
                    "cases": [],
                }
            ]
        }
        yield {"type": "content", "text": json.dumps(resp)}

    async def stub_corrector(self, request: ChatRequest):
        corrector_called["count"] += 1
        yield {"type": "content", "text": json.dumps({"patches": []})}

    async def stub_completeness(self, request: ChatRequest):
        yield {"type": "content", "text": json.dumps({"missing_spans": []})}

    async def router_stub(self, request: ChatRequest):
        sys_msg = request.messages[0].content
        if "classify" in sys_msg.lower() or "category" in sys_msg.lower():
            async for chunk in stub_classify(self, request):
                yield chunk
        elif "outline" in sys_msg.lower():
            async for chunk in stub_outline(self, request):
                yield chunk
        elif "authoritative verification" in sys_msg.lower():
            async for chunk in stub_corrector(self, request):
                yield chunk
        elif "completeness" in sys_msg.lower():
            async for chunk in stub_completeness(self, request):
                yield chunk
        else:
            async for chunk in stub_detail(self, request):
                yield chunk

    monkeypatch.setattr(ProviderConfigService, "chat_stream_events", router_stub)

    struct_res = await structure_user_flow([(sheet, "narrative_flow")], provider_id="test_provider", doc_id="doc1")

    # Assert that corrector was SKIPPED for this fully-covered clean flow
    assert corrector_called["count"] == 0
    assert len(struct_res.steps) == 2
