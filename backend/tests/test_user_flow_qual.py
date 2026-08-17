"""Unit and acceptance tests for TICKET U-QUAL (Alignment & Grouping Quality Fixes).

Covers:
- ISSUE 2: Shortlist recall for CSV / batch transmission flows
- ISSUE 3: Anti-catch-all quality gate for BD mappings (confidence floors + evidence requirements)
- ISSUE 1: Flow-tail grouping and continuation suffix merging
- ISSUE 4: Precise step row-span provenance derived from cell-refs

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
    BDContext,
    BDUnit,
    Block,
    CellRef,
    DetailFlowResult,
    DetailResult,
    DetailStepItem,
    FlowUnit,
    RawCell,
    RawRow,
    SheetExtract,
    StepAlignContext,
    StepItem,
    align_user_flow_steps,
    canonicalize_flow_name,
    evaluate_align_batch,
    structure_user_flow,
)
from domain.user_flow._align import _shortlist_bd_candidates
from domain.user_flow._detail import materialize_step
from infrastructure.db.database import close_db, get_db, init_db
from shared.utils import new_id, utc_now_iso


# ---------------------------------------------------------------------------
# Test 1: ISSUE 2 — Shortlist Recall for CSV / Batch Flow
# ---------------------------------------------------------------------------

def test_shortlist_recall_csv_flow():
    """Verify that a CSV/batch transfer step mentioning 'HNDM004J' and 'Linkexpress'
    scores and shortlists batch transfer BD units (3_exseq_19 / 3_exseq_22),
    even when step_code_files contains online UI files like 'HNIXLOT.cbl'.
    """
    bd_online_init = BDUnit(
        alias="bd1",
        unit_id="bdbf:c1:doc1:1_evtab:s1",
        bd_kind="step",
        flow_id="bdbf:c1:doc1:1_evtab",
        name="FHNIXLOT Initialization",
        description="Initializes the FHNIXLOT screen and waits for user input HNIXLOT.cbl FHNIXLOT.ipf",
    )
    bd_online_event = BDUnit(
        alias="bd2",
        unit_id="bdbf:c1:doc1:1_evtab:s3",
        bd_kind="step",
        flow_id="bdbf:c1:doc1:1_evtab",
        name="FHNIXLOT Status Events",
        description="Handles status events for FHNIXLOT HNIXLOT.cbl FHNIXLOT.ipf",
    )
    bd_csv_transfer = BDUnit(
        alias="bd3",
        unit_id="bdbf:c1:doc1:3_exseq_19:s5",
        bd_kind="step",
        flow_id="bdbf:c1:doc1:3_exseq_19",
        name="Finalize and Deliver Transfer Evidence",
        description="Submits HNDM004J job to perform outbound CSV file transfer via Linkexpress HNDM004J.jcl",
    )
    bd_batch_exec = BDUnit(
        alias="bd4",
        unit_id="bdbf:c1:doc1:3_exseq_22:s1",
        bd_kind="step",
        flow_id="bdbf:c1:doc1:3_exseq_22",
        name="Remote CSV Transfer and Batch Execution",
        description="Executes Linkexpress remote transfer of KIROKU.DAT and batch execution HNDM004J",
    )

    all_units = [bd_online_init, bd_online_event, bd_csv_transfer, bd_batch_exec]
    # Add dummy online units to test top-12 filtering
    for i in range(5, 20):
        all_units.append(
            BDUnit(
                alias=f"bd{i}",
                unit_id=f"bdbf:c1:doc1:dummy:{i}",
                bd_kind="step",
                flow_id="bdbf:c1:doc1:dummy",
                name=f"Online Step {i}",
                description="General online screen processing HNIXLOT.cbl",
            )
        )

    bd_ctx = BDContext(
        cluster_id="c1",
        units=all_units,
        alias_to_unit={u.alias: u for u in all_units},
        id_to_alias={u.unit_id: u.alias for u in all_units},
        catalog_text="",
    )

    step_dict = {
        "text_ja": "めっき曲げ・剝離試験記録表ＣＳＶ送信：HNDM004J",
        "text_en": "Plating bend and peel test record CSV transmission (HNDM004J)",
        "expected_ja": "Linkexpress送信データ：KIROKU.DATファイル",
        "screen_name_ja": "HNDM004J",
        "section_id": "2.1",
    }
    # Notice: step_code_files has HNIXLOT.cbl from the flow-level fallback
    step_code_files = {"HNIXLOT.cbl", "HNDM004J.jcl", "FHNIXLOT.ipf"}

    shortlist = _shortlist_bd_candidates(step_dict, bd_ctx, step_code_files)
    shortlisted_ids = {u.unit_id for u in shortlist}

    # Must include the two CSV/transfer units in the top shortlist!
    assert bd_csv_transfer.unit_id in shortlisted_ids
    assert bd_batch_exec.unit_id in shortlisted_ids


# ---------------------------------------------------------------------------
# Test 2: ISSUE 3 — Anti-Catch-All Quality Gate in BD Alignment
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_catch_all_mapping_rejected(tmp_path: Path, monkeypatch):
    """Verify that the BD Mapping Gate drops low-confidence or claim-less catch-all mappings
    while accepting high-confidence valid mappings.
    """
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(tmp_path))
    await init_db()
    db = get_db()

    try:
        snapshot_id = f"snap:{new_id()}"
        doc_id = f"ufdoc:{new_id()}"
        cluster_id = f"c:{new_id()}"
        now = utc_now_iso()
        snap_dir = tmp_path / "repo"
        snap_dir.mkdir(parents=True, exist_ok=True)
        cbl_text = (
            "       IDENTIFICATION DIVISION.\n"
            "       PROGRAM-ID. HNIXLOT.\n"
            "       PROCEDURE DIVISION.\n"
            "           IF TEST-CODE = 'COIL' PERFORM 1000-PROCESS-COIL.\n"
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
            line_end=5,
            text=cbl_text,
            source_sha256="sha1",
            occurrence_id="occ1",
            parse_status="ok",
        )

        bd_init = BDUnit(alias="bd1", unit_id="bs_init", bd_kind="step", flow_id="bf1", name="Init", description="desc")
        bd_rule = BDUnit(alias="bd2", unit_id="bs_rule", bd_kind="step", flow_id="bf1", name="Rule", description="desc")

        ctx1 = StepAlignContext(
            user_step_id="us1",
            step_alias="u1",
            flow_id="f1",
            ordinal=1,
            kind="action",
            section_id="2.1①",
            text_ja="条件区分=3",
            text_en="Condition class = 3",
            trigger_ja=None,
            expected_ja=None,
            screen_name_ja="FHNIXLOT",
            in_scope=True,
            snippets=[shared_snip],
            snippet_id_map={"src1": shared_snip},
            bd_candidates=[bd_init, bd_rule],
            bd_alias_map={"bd1": bd_init, "bd2": bd_rule},
            bd_id_to_alias={"bs_init": "bd1", "bs_rule": "bd2"},
        )

        # Mock Fused Align response returning:
        # 1. Low-confidence (0.45) "related" mapping to bd2 (catch-all init bs_init) -> MUST BE DROPPED
        # 2. High-confidence (0.95) "realizes" mapping to bd3 (specific rule bs_rule) with claim -> MUST BE ACCEPTED
        async def stub_align(self, request: ChatRequest):
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
                            "bd_unit_id": "bd2",
                            "relation": "related",
                            "confidence": 0.45,
                            "reason": "Generic screen initialization parameter",
                            "support_claim_ids": [],
                        },
                        {
                            "bd_unit_id": "bd3",
                            "relation": "realizes",
                            "confidence": 0.95,
                            "reason": "Validates coil test condition code",
                            "support_claim_ids": ["c1"],
                        },
                    ],
                }
            ]
            yield {"type": "content", "text": json.dumps({"results": results})}

        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", stub_align)

        # Execute align_user_flow_steps
        user_steps = [
            {
                "id": "us1",
                "flow_id": "f1",
                "ordinal": 1,
                "kind": "action",
                "section_id": "2.1①",
                "text_ja": "条件区分=3",
                "in_scope": 1,
            }
        ]

        # Insert dummy user flow doc & flow
        await db.execute("INSERT INTO user_flow_docs (id, source_name, file_hash, imported_at) VALUES (?, 'test.xlsx', 'hash', ?)", (doc_id, now))
        await db.execute("INSERT INTO user_flows (id, doc_id, ordinal, name_ja, name_en, kind, sheet, scope_note, created_at) VALUES ('f1', ?, 1, 'Flow 1', 'Flow 1', 'narrative', 's1', NULL, ?)", (doc_id, now))
        await db.execute("INSERT INTO user_steps (id, flow_id, ordinal, kind, section_id, text_ja, in_scope, sheet, created_at) VALUES ('us1', 'f1', 1, 'action', '2.1①', '条件区分=3', 1, 's1', ?)", (now,))

        # Insert BD units in DB so load_bd_context finds them
        await db.execute("INSERT INTO bd_business_flows (id, cluster_id, doc_id, sub_ix, block_key, name, description, ordinal, origin, model_id, created_at) VALUES ('bf1', ?, 'doc1', 0, 'bk1', 'Flow1', 'desc', 1, 'origin', 'm1', ?)", (cluster_id, now))
        await db.execute("INSERT INTO bd_business_steps (id, flow_id, name, functionality, ordinal, source_node_ids, doc_line_start, doc_line_end, created_at) VALUES ('bs_init', 'bf1', 'Init', 'Initializes screen', 1, '[]', 1, 5, ?)", (now,))
        await db.execute("INSERT INTO bd_business_steps (id, flow_id, name, functionality, ordinal, source_node_ids, doc_line_start, doc_line_end, created_at) VALUES ('bs_rule', 'bf1', 'Rule', 'Validates test code', 2, '[]', 6, 10, ?)", (now,))

        await align_user_flow_steps(
            db=db,
            doc_id=doc_id,
            cluster_id=cluster_id,
            snapshot_id=snapshot_id,
            run_id=f"run:{new_id()}",
            user_steps=user_steps,
            provider_id="test_provider",
            local_path=snap_dir,
        )

        # Assert that only bd2 (realizes, 0.95) was saved; bd1 (related, 0.45) was dropped!
        async with db.execute("SELECT bd_id, relation, confidence FROM user_bd_mappings WHERE user_step_id = 'us1'") as cur:
            saved_mappings = await cur.fetchall()

        assert len(saved_mappings) == 1
        assert saved_mappings[0][0] == "bs_rule"
        assert saved_mappings[0][1] == "realizes"
        assert saved_mappings[0][2] == 0.95
    finally:
        await close_db()


# ---------------------------------------------------------------------------
# Test 3: ISSUE 1 — Flow-Tail Grouping & Continuation Suffix Merging
# ---------------------------------------------------------------------------

def test_canonicalize_flow_name():
    """Verify canonicalize_flow_name cleanly strips continuation suffixes and deduplication indices."""
    assert canonicalize_flow_name("機械試験要求処理：HNDM001N (16)") == "機械試験要求処理：HNDM001N"
    assert canonicalize_flow_name("機械試験要求処理：HNDM001N（2）") == "機械試験要求処理：HNDM001N"
    assert canonicalize_flow_name("岡山工場　島津引張試験メニュー (3)") == "岡山工場　島津引張試験メニュー"
    assert canonicalize_flow_name("引張試験ロット入力（コイル） (その2)") == "引張試験ロット入力（コイル）"
    assert canonicalize_flow_name("引張試験ロット入力（コイル） - part 2") == "引張試験ロット入力（コイル）"
    assert canonicalize_flow_name("引張試験ロット入力（コイル）") == "引張試験ロット入力（コイル）"
    assert canonicalize_flow_name("めっき曲げ・剝離試験記録表ＣＳＶ送信：HNDM004J") == "めっき曲げ・剝離試験記録表ＣＳＶ送信：HNDM004J"


@pytest.mark.asyncio
async def test_structure_merges_continuation_flows(monkeypatch):
    """Verify that structure_user_flow merges flow units sharing the same canonical name on the same sheet."""
    row1 = RawRow(row_ix=2, indent_col=1, cells=[RawCell(col=1, value="2.1① メイン処理")])
    row2 = RawRow(row_ix=3, indent_col=2, cells=[RawCell(col=2, value="2.1② 継続処理")])
    b = Block(block_ix=1, chunk_ix=0, row_start=2, row_end=3, rows=[row1, row2])
    sheet = SheetExtract(sheet="想定表", hidden=False, n_rows=3, n_cols=2, blocks=[b])

    # Classifier stub
    async def stub_classify(self, request: ChatRequest):
        yield {"type": "content", "text": json.dumps({"results": [{"sheet_id": "s1", "sheet_class": "narrative_flow", "reason": "narrative"}]})}

    # Outline stub returning TWO flow items with same base name but one has (16) continuation
    async def stub_outline(self, request: ChatRequest):
        yield {
            "type": "content",
            "text": json.dumps({
                "results": [
                    {
                        "flow_alias": "f1",
                        "name_ja": "機械試験要求処理：HNDM001N",
                        "name_en": "Mechanical Test HNDM001N",
                        "kind": "narrative",
                        "spans": [{"sheet_id": "s1", "row_start": 2, "row_end": 2}],
                    },
                    {
                        "flow_alias": "f2",
                        "name_ja": "機械試験要求処理：HNDM001N (16)",
                        "name_en": "Mechanical Test HNDM001N (16)",
                        "kind": "narrative",
                        "spans": [{"sheet_id": "s1", "row_start": 3, "row_end": 3}],
                    },
                ]
            }),
        }

    async def stub_detail(self, request: ChatRequest):
        resp = {
            "results": [
                {
                    "flow_alias": "f1",
                    "steps": [{"kind": "action", "section_id": "2.1①", "text_cell_ref": {"row_ix": 2, "col": 1}, "row_start": 2, "row_end": 2}],
                    "cases": [],
                },
                {
                    "flow_alias": "f2",
                    "steps": [{"kind": "action", "section_id": "2.1②", "text_cell_ref": {"row_ix": 3, "col": 2}, "row_start": 3, "row_end": 3}],
                    "cases": [],
                },
            ]
        }
        yield {"type": "content", "text": json.dumps(resp)}

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
        elif "completeness" in sys_msg.lower():
            async for chunk in stub_completeness(self, request):
                yield chunk
        else:
            async for chunk in stub_detail(self, request):
                yield chunk

    monkeypatch.setattr(ProviderConfigService, "chat_stream_events", router_stub)

    struct_res = await structure_user_flow([(sheet, "narrative_flow")], provider_id="test_provider", doc_id="doc1")

    # Assert exactly ONE merged flow was produced, with 2 steps!
    assert len(struct_res.flows) == 1
    assert struct_res.flows[0]["name_ja"] == "機械試験要求処理：HNDM001N"
    assert len(struct_res.steps) == 2


# ---------------------------------------------------------------------------
# Test 4: ISSUE 4 — Expectation Step Exact Row Bounds Provenance
# ---------------------------------------------------------------------------

def test_expectation_step_row_span_provenance():
    """Verify that materialize_step derives exact row_start and row_end from the populated
    cell-reference (ref_rows), ignoring any wide or coarse model-provided row_start/row_end span.
    """
    row16 = RawRow(row_ix=16, indent_col=3, cells=[RawCell(col=3, value="青色で表示されること")])
    b = Block(block_ix=1, chunk_ix=0, row_start=14, row_end=20, rows=[row16])
    sheet = SheetExtract(sheet="想定表", hidden=False, n_rows=20, n_cols=4, blocks=[b])

    # Model returned a wide coarse span: row_start=14, row_end=20
    detail_step = DetailStepItem(
        kind="expectation",
        section_id="2.1①",
        text_cell_ref=CellRef(row_ix=16, col=3),
        text_en="Displayed in blue color",
        row_start=14,
        row_end=20,
        in_scope=True,
    )

    mat_step = materialize_step(detail_step, sheet)
    assert mat_step is not None
    assert mat_step.text_ja == "青色で表示されること"
    assert mat_step.kind == "expectation"
    # Exact row bounds must be 16..16 (from CellRef row_ix=16), NOT 14..20!
    assert mat_step.row_start == 16
    assert mat_step.row_end == 16


@pytest.mark.asyncio
async def test_multisheet_spans_merge_into_single_flow(monkeypatch):
    """Verify that a logical flow whose spans cover multiple sheets (e.g. 想定表, 項目表, ホスト_エラー１)
    is aggregated into exactly ONE user_flows row in structure_user_flow.
    """
    row_s1 = RawRow(row_ix=2, indent_col=1, cells=[RawCell(col=1, value="2.1① ロット入力")])
    b1 = Block(block_ix=1, chunk_ix=0, row_start=2, row_end=2, rows=[row_s1])
    sheet1 = SheetExtract(sheet="想定表", hidden=False, n_rows=3, n_cols=2, blocks=[b1])

    row_s2 = RawRow(row_ix=5, indent_col=1, cells=[RawCell(col=1, value="2.1② 項目定義")])
    b2 = Block(block_ix=1, chunk_ix=0, row_start=5, row_end=5, rows=[row_s2])
    sheet2 = SheetExtract(sheet="項目表", hidden=False, n_rows=6, n_cols=2, blocks=[b2])

    row_s3 = RawRow(row_ix=10, indent_col=1, cells=[RawCell(col=1, value="2.1③ エラーケース")])
    b3 = Block(block_ix=1, chunk_ix=0, row_start=10, row_end=10, rows=[row_s3])
    sheet3 = SheetExtract(sheet="ホスト_エラー１", hidden=False, n_rows=11, n_cols=2, blocks=[b3])

    async def stub_classify(self, request: ChatRequest):
        yield {
            "type": "content",
            "text": json.dumps({
                "results": [
                    {"sheet_id": "s1", "sheet_class": "narrative_flow", "reason": "narrative"},
                    {"sheet_id": "s2", "sheet_class": "case_table", "reason": "cases"},
                    {"sheet_id": "s3", "sheet_class": "narrative_flow", "reason": "errors"},
                ]
            }),
        }

    # Outline returns 1 flow with 3 spans across 3 different sheets
    async def stub_outline(self, request: ChatRequest):
        yield {
            "type": "content",
            "text": json.dumps({
                "results": [
                    {
                        "flow_alias": "f1",
                        "name_ja": "引張試験ロット入力（コイル）",
                        "name_en": "Tensile Test Lot Entry (Coil)",
                        "kind": "narrative",
                        "spans": [
                            {"sheet_id": "s1", "row_start": 2, "row_end": 2},
                            {"sheet_id": "s2", "row_start": 5, "row_end": 5},
                            {"sheet_id": "s3", "row_start": 10, "row_end": 10},
                        ],
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
                        {"kind": "action", "section_id": "2.1②", "text_cell_ref": {"row_ix": 5, "col": 1}, "row_start": 5, "row_end": 5},
                        {"kind": "error_rule", "section_id": "2.1③", "text_cell_ref": {"row_ix": 10, "col": 1}, "row_start": 10, "row_end": 10},
                    ],
                    "cases": [],
                }
            ]
        }
        yield {"type": "content", "text": json.dumps(resp)}

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
        elif "completeness" in sys_msg.lower():
            async for chunk in stub_completeness(self, request):
                yield chunk
        else:
            async for chunk in stub_detail(self, request):
                yield chunk

    monkeypatch.setattr(ProviderConfigService, "chat_stream_events", router_stub)

    sheets_to_process = [
        (sheet1, "narrative_flow"),
        (sheet2, "case_table"),
        (sheet3, "narrative_flow"),
    ]
    struct_res = await structure_user_flow(sheets_to_process, provider_id="test_provider", doc_id="doc_multi")

    # Assert exactly ONE merged flow was produced across all 3 sheets!
    assert len(struct_res.flows) == 1
    assert struct_res.flows[0]["name_ja"] == "引張試験ロット入力（コイル）"
    assert struct_res.flows[0]["name_en"] == "Tensile Test Lot Entry (Coil)"
    assert len(struct_res.steps) == 3
    # Verify each step recorded its own respective sheet
    sheets_in_steps = {s["sheet"] for s in struct_res.steps}
    assert sheets_in_steps == {"想定表", "項目表", "ホスト_エラー１"}

