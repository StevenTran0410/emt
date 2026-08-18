"""Acceptance tests for TICKET U4 (Verdict Verifier-Corrector + Fusion + Flow Rollup + API).

OFFLINE ONLY — all provider calls are stubbed via monkeypatching ProviderConfigService.chat_stream_events.
Uses synthetic BD flows/steps/branches, user flows/steps, and synthetic source code files.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import pytest
from httpx import AsyncClient, ASGITransport

from domain.model_connector.service import ProviderConfigService
from domain.model_connector.types import ChatRequest
from domain.user_flow import (
    UserFlowRunResult,
    VerdictRunSummary,
    get_user_flow_report,
    run_user_flow_alignment,
    run_user_flow_verdicts,
)
from infrastructure.db.database import close_db, get_db, init_db
from main import create_app
from shared.utils import new_id, utc_now_iso


# ---------------------------------------------------------------------------
# Synthetic Test Environment Setup
# ---------------------------------------------------------------------------

async def _setup_synthetic_verdict_fixture(
    db: Any,
    tmp_path: Path,
    run_id: str = "run_test",
) -> tuple[str, str, str, str, dict[str, str], dict[str, str]]:
    """Create synthetic snapshot, BD units, user flow with 5 steps (S1..S5)."""
    snapshot_id = f"snap:{new_id()}"
    cluster_id = f"clust:{new_id()}"
    doc_id = f"ufdoc:{new_id()}"
    flow_id = f"uf:{new_id()}"
    now = utc_now_iso()

    # 1. Snapshot directory and source files
    snap_dir = tmp_path / "repo_root"
    snap_dir.mkdir(parents=True, exist_ok=True)

    dummy_cbl = (
        "       IDENTIFICATION DIVISION.\n"
        "       PROGRAM-ID. HNIXLOT.\n"
        "       PROCEDURE DIVISION.\n"
        "           IF TEST-CODE = 'COIL' PERFORM 1000-PROCESS-COIL.\n"
        "           IF TEST-CODE = 'ERR' PERFORM 9000-ERROR-ROUTINE.\n"
        "           GOBACK.\n"
    )
    (snap_dir / "HNIXLOT.cbl").write_text(dummy_cbl, encoding="utf-8")

    dummy_ipf = (
        ")PANEL\n"
        ")BODY\n"
        "  画面名: FHNIXLOT\n"
        "  条件区分: 1.ｺｲﾙ 2.ﾒｯｷ\n"
        "  ボタン: [F1:ヘルプ] [F3:終了]\n"
        ")END\n"
    )
    (snap_dir / "FHNIXLOT.ipf").write_text(dummy_ipf, encoding="utf-8")

    await db.execute(
        "INSERT INTO repo_snapshots (id, local_repo_id, local_path, synced_at, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (snapshot_id, "test_repo", str(snap_dir), now, now),
    )
    await db.execute(
        "INSERT INTO manifest_files (id, snapshot_id, rel_path, language, category, size_bytes, mtime_ns, checksum) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (f"mf:{new_id()}", snapshot_id, "HNIXLOT.cbl", "cobol", "source", len(dummy_cbl), 0, "sha_cbl"),
    )
    await db.execute(
        "INSERT INTO manifest_files (id, snapshot_id, rel_path, language, category, size_bytes, mtime_ns, checksum) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (f"mf:{new_id()}", snapshot_id, "FHNIXLOT.ipf", "ipf", "screen", len(dummy_ipf), 0, "sha_ipf"),
    )

    # 2. Insert BD Units (2 flows, 3 steps, 2 branches)
    bf1_id = f"bf:coil_{new_id()}"
    await db.execute(
        "INSERT INTO bd_business_flows (id, cluster_id, doc_id, sub_ix, block_key, name, description, ordinal, origin, created_at) "
        "VALUES (?, ?, 'doc1', 1, 'blk1', 'Coil Test Entry Flow', 'Processes coil testing.', 1, 'declared', ?)",
        (bf1_id, cluster_id, now),
    )
    bs1_id = f"bs:screen_{new_id()}"
    await db.execute(
        "INSERT INTO bd_business_steps (id, flow_id, name, functionality, ordinal, source_node_ids, doc_line_start, doc_line_end, created_at) "
        "VALUES (?, ?, 'Display Initial Screen', 'Loads screen FHNIXLOT and initializes input fields.', 1, '[]', 10, 15, ?)",
        (bs1_id, bf1_id, now),
    )
    bs2_id = f"bs:valcoil_{new_id()}"
    await db.execute(
        "INSERT INTO bd_business_steps (id, flow_id, name, functionality, ordinal, source_node_ids, doc_line_start, doc_line_end, created_at) "
        "VALUES (?, ?, 'Validate Lot Condition', 'Checks condition category 1 in HNIXLOT.', 2, '[]', 16, 25, ?)",
        (bs2_id, bf1_id, now),
    )
    bb1_id = f"bb:errcoil_{new_id()}"
    await db.execute(
        "INSERT INTO bd_business_branches (id, flow_id, branch_kind, guard_description, source_step_id, target_step_id, source_edge_ids, created_at) "
        "VALUES (?, ?, 'error', 'Invalid Condition Category entered', ?, NULL, '[]', ?)",
        (bb1_id, bf1_id, bs2_id, now),
    )

    # BD Flow 2 (Plating - has an unreferenced branch for BD_EXTRA)
    bf2_id = f"bf:plate_{new_id()}"
    await db.execute(
        "INSERT INTO bd_business_flows (id, cluster_id, doc_id, sub_ix, block_key, name, description, ordinal, origin, created_at) "
        "VALUES (?, ?, 'doc1', 2, 'blk2', 'Plating Test Entry Flow', 'Handles plating test.', 2, 'declared', ?)",
        (bf2_id, cluster_id, now),
    )
    bs3_id = f"bs:valplate_{new_id()}"
    await db.execute(
        "INSERT INTO bd_business_steps (id, flow_id, name, functionality, ordinal, source_node_ids, doc_line_start, doc_line_end, created_at) "
        "VALUES (?, ?, 'Validate Plating Lot', 'Validates plating parameters.', 1, '[]', 30, 40, ?)",
        (bs3_id, bf2_id, now),
    )
    bb2_id = f"bb:extra_branch_{new_id()}"
    await db.execute(
        "INSERT INTO bd_business_branches (id, flow_id, branch_kind, guard_description, source_step_id, target_step_id, source_edge_ids, created_at) "
        "VALUES (?, ?, 'error', 'Plating Thickness Check Failed', ?, NULL, '[]', ?)",
        (bb2_id, bf2_id, bs3_id, now),
    )

    # Seed BD unit verdicts (bs2 is MATCH)
    await db.execute(
        "INSERT INTO business_unit_verdicts (id, cluster_id, snapshot_id, unit_id, unit_kind, mapping_status, mapping_method, route_segment_json, verdict, guard_verdict, ai_bucket, reason, evidence_json, created_at) "
        "VALUES (?, ?, ?, ?, 'step', 'mapped', 'llm', '[]', 'MATCH', 'MATCH', 'confirmed', 'Matched', '[]', ?)",
        (f"buv:{new_id()}", cluster_id, snapshot_id, bs2_id, now),
    )

    # 3. User Flow Doc & User Flow
    await db.execute(
        "INSERT INTO user_flow_docs (id, source_name, file_hash, imported_at) "
        "VALUES (?, 'test_scenario.xlsx', 'hash123', ?)",
        (doc_id, now),
    )
    await db.execute(
        "INSERT INTO user_flows (id, doc_id, ordinal, name_ja, name_en, kind, sheet, scope_note, created_at) "
        "VALUES (?, ?, 1, 'コイル試験指示', 'Coil Test Instruction', 'narrative', '想定表', NULL, ?)",
        (flow_id, doc_id, now),
    )

    # 4. User Steps: S1 (covered), S2 (BD-missing), S3 (contradicted), S4 (presentation), S5 (out-of-scope)
    s1_id = f"us:s1_{new_id()}"
    s2_id = f"us:s2_{new_id()}"
    s3_id = f"us:s3_{new_id()}"
    s4_id = f"us:s4_{new_id()}"
    s5_id = f"us:s5_{new_id()}"

    # S1: Covered step
    await db.execute(
        "INSERT INTO user_steps (id, flow_id, ordinal, kind, section_id, text_ja, text_en, trigger_ja, expected_ja, screen_name_ja, in_scope, scope_note, sheet, row_start, row_end, provenance_json, created_at) "
        "VALUES (?, ?, 1, 'action', '2.1①', '条件区分に1(ｺｲﾙ)を入力', 'Enter condition 1 (Coil)', 'Enterキー', '画面遷移', 'FHNIXLOT', 1, NULL, '想定表', 10, 11, '{}', ?)",
        (s1_id, flow_id, now),
    )
    # S2: BD-Missing step (code has error routine, but BD doesn't have this step)
    await db.execute(
        "INSERT INTO user_steps (id, flow_id, ordinal, kind, section_id, text_ja, text_en, trigger_ja, expected_ja, screen_name_ja, in_scope, scope_note, sheet, row_start, row_end, provenance_json, created_at) "
        "VALUES (?, ?, 2, 'action', '2.1②', '未定義コードを入力してエラー確認', 'Enter invalid code and verify error', 'Enterキー', 'エラーメッセージ', 'FHNIXLOT', 1, NULL, '想定表', 12, 13, '{}', ?)",
        (s2_id, flow_id, now),
    )
    # S3: Contradicted step (user expects error popup, BD asserts normal retry)
    await db.execute(
        "INSERT INTO user_steps (id, flow_id, ordinal, kind, section_id, text_ja, text_en, trigger_ja, expected_ja, screen_name_ja, in_scope, scope_note, sheet, row_start, row_end, provenance_json, created_at) "
        "VALUES (?, ?, 3, 'action', '2.1③', '異常時に即時終了ダイアログを表示', 'Show termination dialog on error', 'F3キー', '終了ダイアログ', 'FHNIXLOT', 1, NULL, '想定表', 14, 15, '{}', ?)",
        (s3_id, flow_id, now),
    )
    # S4: Presentation step (layout/color)
    await db.execute(
        "INSERT INTO user_steps (id, flow_id, ordinal, kind, section_id, text_ja, text_en, trigger_ja, expected_ja, screen_name_ja, in_scope, scope_note, sheet, row_start, row_end, provenance_json, created_at) "
        "VALUES (?, ?, 4, 'action', '2.1④', '画面タイトルが青色で中央揃えで表示されること', 'Title displayed in blue and centered', NULL, '青色表示', 'FHNIXLOT', 1, NULL, '想定表', 16, 17, '{}', ?)",
        (s4_id, flow_id, now),
    )
    # S5: Out of scope step
    await db.execute(
        "INSERT INTO user_steps (id, flow_id, ordinal, kind, section_id, text_ja, text_en, trigger_ja, expected_ja, screen_name_ja, in_scope, scope_note, sheet, row_start, row_end, provenance_json, created_at) "
        "VALUES (?, ?, 5, 'action', '2.1⑤', '将来機能: バーコードスキャナ連携', 'Future: barcode scanner integration', NULL, NULL, 'FHNIXLOT', 0, 'PoC対象外', '想定表', 18, 19, '{}', ?)",
        (s5_id, flow_id, now),
    )

    # 5. Insert upstream Code Anchors (U2) & BD Mappings (U3)
    # S1 has valid anchor on HNIXLOT.cbl and mapping to bs2
    await db.execute(
        "INSERT INTO user_code_anchors (id, run_id, snapshot_id, step_id, rel_path, line_start, line_end, kind, valid, reason, created_at) "
        "VALUES (?, ?, ?, ?, 'HNIXLOT.cbl', 4, 4, 'llm_matched', 1, 'Valid code anchor', ?)",
        (f"uca:{new_id()}", run_id, snapshot_id, s1_id, now),
    )
    await db.execute(
        "INSERT INTO user_bd_mappings (id, run_id, user_step_id, bd_kind, bd_id, relation, confidence, reason, created_at) "
        "VALUES (?, ?, ?, 'step', ?, 'realizes', 0.9, 'Direct match', ?)",
        (f"ubm:{new_id()}", run_id, s1_id, bs2_id, now),
    )

    # S2 has valid code anchor on HNIXLOT.cbl:5, but upstream U3 wrongly mapped it to bs3
    await db.execute(
        "INSERT INTO user_code_anchors (id, run_id, snapshot_id, step_id, rel_path, line_start, line_end, kind, valid, reason, created_at) "
        "VALUES (?, ?, ?, ?, 'HNIXLOT.cbl', 5, 5, 'llm_matched', 1, 'Error routine code anchor', ?)",
        (f"uca:{new_id()}", run_id, snapshot_id, s2_id, now),
    )
    await db.execute(
        "INSERT INTO user_bd_mappings (id, run_id, user_step_id, bd_kind, bd_id, relation, confidence, reason, created_at) "
        "VALUES (?, ?, ?, 'step', ?, 'realizes', 0.5, 'Wrong upstream mapping', ?)",
        (f"ubm:{new_id()}", run_id, s2_id, bs3_id, now),
    )

    # S3 has mapping to bb1 + a valid code anchor on the F3:終了 (exit) panel line, which shows the
    # actual "terminate" behavior — the evidence needed to back a CONTRADICTED (BD asserts retry).
    await db.execute(
        "INSERT INTO user_bd_mappings (id, run_id, user_step_id, bd_kind, bd_id, relation, confidence, reason, created_at) "
        "VALUES (?, ?, ?, 'branch', ?, 'realizes', 0.8, 'Mapped to error branch', ?)",
        (f"ubm:{new_id()}", run_id, s3_id, bb1_id, now),
    )
    await db.execute(
        "INSERT INTO user_code_anchors (id, run_id, snapshot_id, step_id, rel_path, line_start, line_end, kind, valid, reason, created_at) "
        "VALUES (?, ?, ?, ?, 'FHNIXLOT.ipf', 5, 5, 'llm_matched', 1, 'F3 exit button anchor', ?)",
        (f"uca:{new_id()}", run_id, snapshot_id, s3_id, now),
    )

    # S4 has presentation flag artifact in align
    await db.execute(
        "INSERT INTO user_run_artifacts (id, run_id, doc_id, ref_id, payload, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (f"ura:{new_id()}", run_id, doc_id, f"align:{s4_id}", json.dumps({"presentation": True}), now),
    )

    step_ids = {"s1": s1_id, "s2": s2_id, "s3": s3_id, "s4": s4_id, "s5": s5_id}
    bd_ids = {"bf1": bf1_id, "bs1": bs1_id, "bs2": bs2_id, "bb1": bb1_id, "bf2": bf2_id, "bs3": bs3_id, "bb2": bb2_id}

    await db.commit()
    return doc_id, cluster_id, snapshot_id, flow_id, step_ids, bd_ids


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_corrector_overrides_mapper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Test 1: Verifier corrector overrides wrong upstream mapping -> BD_MISSING with valid citation."""
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(tmp_path / "data"))
    await init_db()
    db = get_db()

    doc_id, cluster_id, snapshot_id, flow_id, step_ids, bd_ids = await _setup_synthetic_verdict_fixture(db, tmp_path)

    # Mock provider response:
    # u1 (S1): COVERED, kept_bd_ids=[bd1] (bs2), citation in HNIXLOT.cbl:4
    # u2 (S2): BD_MISSING, kept_bd_ids=[], citation in HNIXLOT.cbl:5, corrected=True
    # u3 (S3): CONTRADICTED, kept_bd_ids=[bd1] (bb1), reason='BD asserts retry vs user exit', corrected=False
    # u4 (S4): UNVERIFIABLE, presentation claim
    async def _mock_stream(req: ChatRequest):
        results = [
            {
                "unit_id": "u1",
                "verdict": "COVERED",
                "divergence": None,
                "kept_bd_ids": ["bd1"],
                "citations": [{"rel_path": "HNIXLOT.cbl", "line_start": 4, "line_end": 4}],
                "corrected": False,
                "reason": "Supported by code and BD",
            },
            {
                "unit_id": "u2",
                "verdict": "BD_MISSING",
                "divergence": "behavioural",
                "kept_bd_ids": [],
                "citations": [{"rel_path": "HNIXLOT.cbl", "line_start": 5, "line_end": 5}],
                "corrected": True,
                "reason": "Upstream mapping was wrong; code implements 9000-ERROR-ROUTINE but BD does not reflect it.",
            },
            {
                "unit_id": "u3",
                "verdict": "CONTRADICTED",
                "divergence": "behavioural",
                "kept_bd_ids": ["bd1"],
                "citations": [{"rel_path": "FHNIXLOT.ipf", "line_start": 5, "line_end": 5}],
                "corrected": False,
                "reason": "BD bb1 describes retry whereas user step expects termination dialog.",
            },
            {
                "unit_id": "u4",
                "verdict": "UNVERIFIABLE",
                "divergence": None,
                "kept_bd_ids": [],
                "citations": [],
                "corrected": False,
                "reason": "Visual presentation attribute not verifiable in source code.",
            },
        ]
        yield {"type": "content", "text": json.dumps({"results": results})}

    monkeypatch.setattr(ProviderConfigService, "chat_stream_events", lambda self, req: _mock_stream(req))

    res = await run_user_flow_verdicts(
        db=db,
        doc_id=doc_id,
        cluster_id=cluster_id,
        snapshot_id=snapshot_id,
        run_id="run_test",
        provider_id="mock_prov",
    )

    # Verify S2 has verdict BD_MISSING and evidence_json records corrected=True and kept_bd_ids=[]
    async with db.execute("SELECT verdict, divergence, reason, evidence_json FROM user_verdicts WHERE ref_id = ?", (step_ids["s2"],)) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row["verdict"] == "BD_MISSING"
    assert row["divergence"] == "behavioural"
    ev = json.loads(row["evidence_json"])
    assert ev["corrected"] is True
    assert ev["kept_bd_ids"] == []
    assert len(ev["citations"]) == 1
    assert ev["citations"][0]["valid"] is True
    assert ev["citations"][0]["fetched_text"] is not None

    # Upstream user_bd_mappings for S2 still exists (unmodified proposal)
    async with db.execute("SELECT bd_id FROM user_bd_mappings WHERE user_step_id = ?", (step_ids["s2"],)) as cur:
        m_row = await cur.fetchone()
    assert m_row is not None
    assert m_row["bd_id"] == bd_ids["bs3"]

    await close_db()


@pytest.mark.asyncio
async def test_citation_gate_out_of_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Test 2: Verifier cites out-of-window line -> downgraded to UNVERIFIABLE + CITATION_OUT_OF_WINDOW."""
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(tmp_path / "data"))
    await init_db()
    db = get_db()

    doc_id, cluster_id, snapshot_id, flow_id, step_ids, bd_ids = await _setup_synthetic_verdict_fixture(db, tmp_path)

    # Mock provider response: cites line 999 (out of file/window) for S1
    async def _mock_stream(req: ChatRequest):
        results = [
            {
                "unit_id": "u1",
                "verdict": "COVERED",
                "divergence": None,
                "kept_bd_ids": ["bd1"],
                "citations": [{"rel_path": "HNIXLOT.cbl", "line_start": 999, "line_end": 999}],
                "corrected": False,
                "reason": "Cited out-of-bounds line",
            },
            {
                "unit_id": "u2",
                "verdict": "BD_MISSING",
                "divergence": "behavioural",
                "kept_bd_ids": [],
                "citations": [{"rel_path": "HNIXLOT.cbl", "line_start": 5, "line_end": 5}],
                "corrected": False,
                "reason": "OK",
            },
            {
                "unit_id": "u3",
                "verdict": "UNVERIFIABLE",
                "divergence": None,
                "kept_bd_ids": [],
                "citations": [],
                "corrected": False,
                "reason": "OK",
            },
            {
                "unit_id": "u4",
                "verdict": "UNVERIFIABLE",
                "divergence": None,
                "kept_bd_ids": [],
                "citations": [],
                "corrected": False,
                "reason": "OK",
            },
        ]
        yield {"type": "content", "text": json.dumps({"results": results})}

    monkeypatch.setattr(ProviderConfigService, "chat_stream_events", lambda self, req: _mock_stream(req))

    await run_user_flow_verdicts(
        db=db,
        doc_id=doc_id,
        cluster_id=cluster_id,
        snapshot_id=snapshot_id,
        run_id="run_test",
        provider_id="mock_prov",
    )

    async with db.execute("SELECT verdict, reason, evidence_json FROM user_verdicts WHERE ref_id = ?", (step_ids["s1"],)) as cur:
        row = await cur.fetchone()
    assert row["verdict"] == "UNVERIFIABLE"
    assert "CITATION_INVALID" in row["reason"] or "CITATION_OUT_OF_WINDOW" in row["reason"]

    await close_db()


@pytest.mark.asyncio
async def test_bd_missing_requires_citation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Test 3: Verifier returns BD_MISSING without citation -> downgraded to UNVERIFIABLE + NO_CITATION."""
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(tmp_path / "data"))
    await init_db()
    db = get_db()

    doc_id, cluster_id, snapshot_id, flow_id, step_ids, bd_ids = await _setup_synthetic_verdict_fixture(db, tmp_path)

    async def _mock_stream(req: ChatRequest):
        results = [
            {
                "unit_id": "u1",
                "verdict": "COVERED",
                "divergence": None,
                "kept_bd_ids": ["bd1"],
                "citations": [{"rel_path": "HNIXLOT.cbl", "line_start": 4, "line_end": 4}],
                "corrected": False,
                "reason": "Valid",
            },
            {
                "unit_id": "u2",
                "verdict": "BD_MISSING",
                "divergence": "behavioural",
                "kept_bd_ids": [],
                "citations": [],  # Missing citations!
                "corrected": False,
                "reason": "Claims missing without citations",
            },
            {
                "unit_id": "u3",
                "verdict": "UNVERIFIABLE",
                "divergence": None,
                "kept_bd_ids": [],
                "citations": [],
                "corrected": False,
                "reason": "OK",
            },
            {
                "unit_id": "u4",
                "verdict": "UNVERIFIABLE",
                "divergence": None,
                "kept_bd_ids": [],
                "citations": [],
                "corrected": False,
                "reason": "OK",
            },
        ]
        yield {"type": "content", "text": json.dumps({"results": results})}

    monkeypatch.setattr(ProviderConfigService, "chat_stream_events", lambda self, req: _mock_stream(req))

    await run_user_flow_verdicts(
        db=db,
        doc_id=doc_id,
        cluster_id=cluster_id,
        snapshot_id=snapshot_id,
        run_id="run_test",
        provider_id="mock_prov",
    )

    async with db.execute("SELECT verdict, reason FROM user_verdicts WHERE ref_id = ?", (step_ids["s2"],)) as cur:
        row = await cur.fetchone()
    assert row["verdict"] == "UNVERIFIABLE"
    assert "[NO_CITATION]" in row["reason"]

    await close_db()


@pytest.mark.asyncio
async def test_out_of_scope_prepass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Test 4: in_scope=0 step (S5) is never sent to LLM, immediately assigned OUT_OF_SCOPE."""
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(tmp_path / "data"))
    await init_db()
    db = get_db()

    doc_id, cluster_id, snapshot_id, flow_id, step_ids, bd_ids = await _setup_synthetic_verdict_fixture(db, tmp_path)

    called_units = []

    async def _mock_stream(req: ChatRequest):
        user_msg = json.loads(req.messages[1].content)
        for item in user_msg:
            called_units.append(item["unit_id"])
        results = [
            {"unit_id": f"u{i+1}", "verdict": "COVERED", "kept_bd_ids": [], "citations": [{"rel_path": "HNIXLOT.cbl", "line_start": 4, "line_end": 4}], "corrected": False, "reason": "OK"}
            for i in range(len(user_msg))
        ]
        yield {"type": "content", "text": json.dumps({"results": results})}

    monkeypatch.setattr(ProviderConfigService, "chat_stream_events", lambda self, req: _mock_stream(req))

    await run_user_flow_verdicts(
        db=db,
        doc_id=doc_id,
        cluster_id=cluster_id,
        snapshot_id=snapshot_id,
        run_id="run_test",
        provider_id="mock_prov",
    )

    # Exactly 4 in-scope steps were evaluated; S5 was never sent in batch
    assert len(called_units) == 4

    async with db.execute("SELECT verdict, divergence, reason FROM user_verdicts WHERE ref_id = ?", (step_ids["s5"],)) as cur:
        row = await cur.fetchone()
    assert row["verdict"] == "OUT_OF_SCOPE"
    assert row["divergence"] == "scope"
    assert "PoC対象外" in row["reason"]

    await close_db()


@pytest.mark.asyncio
async def test_bd_extra_verdict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Test 5: Unmapped BD unit (bb2) gets side='bd', verdict='BD_EXTRA'."""
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(tmp_path / "data"))
    await init_db()
    db = get_db()

    doc_id, cluster_id, snapshot_id, flow_id, step_ids, bd_ids = await _setup_synthetic_verdict_fixture(db, tmp_path)

    async def _mock_stream(req: ChatRequest):
        results = [
            {"unit_id": "u1", "verdict": "COVERED", "kept_bd_ids": ["bd1"], "citations": [{"rel_path": "HNIXLOT.cbl", "line_start": 4, "line_end": 4}], "corrected": False, "reason": "OK"},
            {"unit_id": "u2", "verdict": "UNVERIFIABLE", "kept_bd_ids": [], "citations": [], "corrected": False, "reason": "OK"},
            {"unit_id": "u3", "verdict": "UNVERIFIABLE", "kept_bd_ids": [], "citations": [], "corrected": False, "reason": "OK"},
            {"unit_id": "u4", "verdict": "UNVERIFIABLE", "kept_bd_ids": [], "citations": [], "corrected": False, "reason": "OK"},
        ]
        yield {"type": "content", "text": json.dumps({"results": results})}

    monkeypatch.setattr(ProviderConfigService, "chat_stream_events", lambda self, req: _mock_stream(req))

    await run_user_flow_verdicts(
        db=db,
        doc_id=doc_id,
        cluster_id=cluster_id,
        snapshot_id=snapshot_id,
        run_id="run_test",
        provider_id="mock_prov",
    )

    # bb2 is unreferenced -> BD_EXTRA
    async with db.execute("SELECT verdict, side, ref_kind FROM user_verdicts WHERE ref_id = ?", (bd_ids["bb2"],)) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row["side"] == "bd"
    assert row["verdict"] == "BD_EXTRA"

    await close_db()


@pytest.mark.asyncio
async def test_flow_rollup_verdict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Test 7: Flow rollup aggregates step verdicts and records flow_match=DIVERGENT (due to CONTRADICTED step)."""
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(tmp_path / "data"))
    await init_db()
    db = get_db()

    doc_id, cluster_id, snapshot_id, flow_id, step_ids, bd_ids = await _setup_synthetic_verdict_fixture(db, tmp_path)

    async def _mock_stream(req: ChatRequest):
        results = [
            {"unit_id": "u1", "verdict": "COVERED", "kept_bd_ids": ["bd1"], "citations": [{"rel_path": "HNIXLOT.cbl", "line_start": 4, "line_end": 4}], "corrected": False, "reason": "OK"},
            {"unit_id": "u2", "verdict": "BD_MISSING", "divergence": "behavioural", "kept_bd_ids": [], "citations": [{"rel_path": "HNIXLOT.cbl", "line_start": 5, "line_end": 5}], "corrected": False, "reason": "OK"},
            {"unit_id": "u3", "verdict": "CONTRADICTED", "divergence": "behavioural", "kept_bd_ids": ["bd1"], "citations": [{"rel_path": "FHNIXLOT.ipf", "line_start": 5, "line_end": 5}], "corrected": False, "reason": "Positive conflict"},
            {"unit_id": "u4", "verdict": "UNVERIFIABLE", "kept_bd_ids": [], "citations": [], "corrected": False, "reason": "OK"},
        ]
        yield {"type": "content", "text": json.dumps({"results": results})}

    monkeypatch.setattr(ProviderConfigService, "chat_stream_events", lambda self, req: _mock_stream(req))

    res = await run_user_flow_verdicts(
        db=db,
        doc_id=doc_id,
        cluster_id=cluster_id,
        snapshot_id=snapshot_id,
        run_id="run_test",
        provider_id="mock_prov",
    )

    assert res.flow_verdicts[flow_id] == "DIVERGENT"
    assert res.covered_steps == 1
    assert res.bd_missing_steps == 1
    assert res.contradicted_steps == 1
    assert res.unverifiable_steps == 1
    assert res.out_of_scope_steps == 1

    # Check stored flow-level verdict row in DB
    async with db.execute("SELECT verdict, ref_kind, side, evidence_json FROM user_verdicts WHERE ref_id = ? AND ref_kind = 'flow'", (flow_id,)) as cur:
        flow_v_row = await cur.fetchone()
    assert flow_v_row is not None
    assert flow_v_row["verdict"] == "DIVERGENT"
    assert flow_v_row["side"] == "user"
    flow_ev = json.loads(flow_v_row["evidence_json"])
    assert flow_ev["counts"]["COVERED"] == 1
    assert flow_ev["counts"]["BD_MISSING"] == 1
    assert flow_ev["counts"]["CONTRADICTED"] == 1
    assert flow_ev["counts"]["UNVERIFIABLE"] == 1
    assert flow_ev["counts"]["OUT_OF_SCOPE"] == 1

    await close_db()


@pytest.mark.asyncio
async def test_report_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Test 6: FastAPI GET /api/user-flow/report returns structured tree with English labels and citations."""
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(tmp_path / "data"))
    await init_db()
    db = get_db()

    doc_id, cluster_id, snapshot_id, flow_id, step_ids, bd_ids = await _setup_synthetic_verdict_fixture(db, tmp_path)

    async def _mock_stream(req: ChatRequest):
        results = [
            {"unit_id": "u1", "verdict": "COVERED", "kept_bd_ids": ["bd3"], "citations": [{"rel_path": "HNIXLOT.cbl", "line_start": 4, "line_end": 4}], "corrected": False, "reason": "Valid match"},
            {"unit_id": "u2", "verdict": "BD_MISSING", "divergence": "behavioural", "kept_bd_ids": [], "citations": [{"rel_path": "HNIXLOT.cbl", "line_start": 5, "line_end": 5}], "corrected": True, "reason": "Missing in BD"},
            {"unit_id": "u3", "verdict": "CONTRADICTED", "divergence": "behavioural", "kept_bd_ids": ["bd4"], "citations": [{"rel_path": "FHNIXLOT.ipf", "line_start": 5, "line_end": 5}], "corrected": False, "reason": "Conflict"},
            {"unit_id": "u4", "verdict": "UNVERIFIABLE", "kept_bd_ids": [], "citations": [], "corrected": False, "reason": "Visual"},
        ]
        yield {"type": "content", "text": json.dumps({"results": results})}

    monkeypatch.setattr(ProviderConfigService, "chat_stream_events", lambda self, req: _mock_stream(req))

    await run_user_flow_verdicts(
        db=db,
        doc_id=doc_id,
        cluster_id=cluster_id,
        snapshot_id=snapshot_id,
        run_id="run_test",
        provider_id="mock_prov",
    )

    # Publication contract (UP3-1): only a run whose completion marker names this
    # (doc, cluster, snapshot) is reportable.
    await db.execute(
        "INSERT INTO user_run_artifacts (id, run_id, doc_id, ref_id, payload, created_at) "
        "VALUES (?, 'run_test', ?, '__run_complete__', ?, ?)",
        (f"ura:{new_id()}", doc_id,
         json.dumps({"doc_id": doc_id, "run_id": "run_test", "cluster_id": cluster_id, "snapshot_id": snapshot_id}),
         utc_now_iso()),
    )
    await db.commit()

    # Test report via FastAPI TestClient
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/user-flow/report",
            params={"doc_id": doc_id, "cluster_id": cluster_id, "snapshot_id": snapshot_id},
        )
        assert resp.status_code == 200
        data = resp.json()

        assert "summary" in data
        assert data["summary"]["total_flows"] == 1
        assert data["summary"]["total_steps"] == 5
        assert data["summary"]["in_scope_steps"] == 4
        assert data["summary"]["flow_counts"]["DIVERGENT"] == 1

        flows = data["flows"]
        assert len(flows) == 1
        flow = flows[0]
        assert flow["name_en"] == "Coil Test Instruction"
        assert flow["flow_match"] == "DIVERGENT"

        steps = flow["steps"]
        assert len(steps) == 5

        # Check Step 1 (COVERED)
        s1 = next(s for s in steps if s["id"] == step_ids["s1"])
        assert s1["text_en"] == "Enter condition 1 (Coil)"
        assert s1["verdict"] == "COVERED"
        assert len(s1["citations"]) == 1
        assert s1["citations"][0]["fetched_text"] is not None
        assert "IF TEST-CODE = 'COIL'" in s1["citations"][0]["fetched_text"]
        assert len(s1["kept_bd_mappings"]) == 1
        assert s1["kept_bd_mappings"][0]["name"] == "Validate Lot Condition"

        # Check Step 2 (BD_MISSING)
        s2 = next(s for s in steps if s["id"] == step_ids["s2"])
        assert s2["verdict"] == "BD_MISSING"
        assert s2["corrected"] is True
        assert len(s2["citations"]) == 1
        assert "9000-ERROR-ROUTINE" in s2["citations"][0]["fetched_text"]

        # Check BD_EXTRA list
        assert len(data["bd_extra"]) >= 1
        extra_bb2 = next((b for b in data["bd_extra"] if b["bd_id"] == bd_ids["bb2"]), None)
        assert extra_bb2 is not None
        assert extra_bb2["verdict"] == "BD_EXTRA"

    await close_db()


@pytest.mark.asyncio
async def test_full_pipeline_run_user_flow_alignment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Test 8: Full pipeline run_user_flow_alignment runs fused align + Stage U4 verdicts."""
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(tmp_path / "data"))
    await init_db()
    db = get_db()

    doc_id, cluster_id, snapshot_id, flow_id, step_ids, bd_ids = await _setup_synthetic_verdict_fixture(db, tmp_path)

    # Run in offline mode (provider_id=None)
    run_res = await run_user_flow_alignment(
        db=db,
        doc_id=doc_id,
        cluster_id=cluster_id,
        snapshot_id=snapshot_id,
        provider_id=None,
    )

    assert isinstance(run_res, UserFlowRunResult)
    assert run_res.status == "ok"
    assert run_res.total_steps == 5
    assert run_res.in_scope_steps == 4

    # Verify verdicts were persisted
    async with db.execute("SELECT count(*) FROM user_verdicts WHERE doc_id = ?", (doc_id,)) as cur:
        cnt_row = await cur.fetchone()
    count = cnt_row[0] if isinstance(cnt_row, (tuple, list)) else cnt_row["count(*)"]
    # 5 steps + 1 flow + BD units
    assert count >= 6

    await close_db()
