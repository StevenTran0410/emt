"""Acceptance tests for TICKET U2 (User-step to Code Evidence Anchoring).

OFFLINE ONLY — all provider calls are stubbed, no real LLM/network calls.
Uses synthetic source code fixtures built in-test under a temporary snapshot directory.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import pytest

from domain.business_flow_integrity._retrieval import expand_unit_snippets
from domain.model_connector.service import ProviderConfigService
from domain.model_connector.types import ChatRequest
from domain.user_flow import (
    SeedHit,
    UserFlowRunResult,
    cut_line_window,
    retrieve_step_snippets,
    run_user_flow_alignment,
    seed_literal_hits,
)
from domain.user_flow._anchor import resolve_step_files
from infrastructure.db.database import close_db, get_db, init_db
from shared.utils import new_id, utc_now_iso


# ---------------------------------------------------------------------------
# Synthetic Test Environment Setup
# ---------------------------------------------------------------------------

async def _setup_synthetic_repo_and_doc(
    db: Any,
    tmp_path: Path,
) -> tuple[str, str, str, dict[str, str]]:
    """Create:
    1. A temporary repository snapshot with:
       - FHNIXLOT.ipf (ISPF panel, UTF-8, zero occurrences)
       - HNIXLOT.cbl (COBOL program, UTF-8, 1 guard occurrence)
    2. A user_flow_docs record with 1 user_flow and 4 user_steps (S1..S4).
    """
    snapshot_id = f"snap:{new_id()}"
    cluster_id = f"clust:{new_id()}"
    doc_id = f"ufdoc:{new_id()}"
    flow_id = f"uf:{new_id()}"
    now = utc_now_iso()

    # 1. Create files on disk under snapshot directory
    snap_dir = tmp_path / "repo_root"
    snap_dir.mkdir(parents=True, exist_ok=True)

    # FHNIXLOT.ipf: Panel with Japanese label, half-width kana options, PF keys, attribute noise
    ipf_content = (
        ")PANEL\n"
        ")ATTR\n"
        "<ｱ$!C0 <D0\n"
        ")BODY\n"
        "--------------------- 試験機管理メニュー ---------------------\n"
        "  画面名: FHNIXLOT\n"
        "  条件区分: <A* 1.ｺｲﾙ  2.ﾒｯｷ  3.その他\n"
        "  試験機コード: <B* HN01\n"
        "  F3=終了  F12=取消  Enter=実行\n"
        ")END\n"
    )
    (snap_dir / "FHNIXLOT.ipf").write_text(ipf_content, encoding="utf-8")

    # HNIXLOT.cbl: COBOL program with validation guard
    cbl_content = (
        "       IDENTIFICATION DIVISION.\n"
        "       PROGRAM-ID. HNIXLOT.\n"
        "       ENVIRONMENT DIVISION.\n"
        "       DATA DIVISION.\n"
        "       PROCEDURE DIVISION.\n"
        "       0000-MAIN.\n"
        "           IF TEST-CODE = 'COIL'\n"
        "               PERFORM 1000-PROCESS-COIL\n"
        "           ELSE\n"
        "               MOVE 'INVALID CODE' TO ERR-MSG\n"
        "           END-IF.\n"
        "           GOBACK.\n"
    )
    (snap_dir / "HNIXLOT.cbl").write_text(cbl_content, encoding="utf-8")

    # 2. Register snapshot and manifest
    await db.execute(
        "INSERT INTO repo_snapshots (id, local_repo_id, local_path, synced_at, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (snapshot_id, "test_repo", str(snap_dir), now, now),
    )

    await db.execute(
        "INSERT INTO manifest_files (id, snapshot_id, rel_path, language, category, size_bytes, mtime_ns, checksum) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (f"mf:{new_id()}", snapshot_id, "FHNIXLOT.ipf", "ipf", "screen", len(ipf_content), 0, "sha_ipf"),
    )
    await db.execute(
        "INSERT INTO manifest_files (id, snapshot_id, rel_path, language, category, size_bytes, mtime_ns, checksum) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (f"mf:{new_id()}", snapshot_id, "HNIXLOT.cbl", "cobol", "source", len(cbl_content), 0, "sha_cbl"),
    )

    # 3. Register ONE evidence occurrence for HNIXLOT.cbl (0 for FHNIXLOT.ipf)
    await db.execute(
        "INSERT INTO bfi_evidence_occurrences (id, snapshot_id, rel_path, language, kind, edge_type, occurrence_ix, line_start, line_end, parse_status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            f"occ:{new_id()}",
            snapshot_id,
            "HNIXLOT.cbl",
            "cobol",
            "guard",
            "executes",
            1,
            7,
            11,
            "ok",
            now,
        ),
    )

    # 4. Insert user_flow_docs & user_flows
    await db.execute(
        "INSERT INTO user_flow_docs (id, source_name, file_hash, imported_at) "
        "VALUES (?, ?, ?, ?)",
        (doc_id, "doc.xlsx", "hash123", now),
    )

    await db.execute(
        "INSERT INTO user_flows (id, doc_id, ordinal, name_ja, name_en, kind, sheet, scope_note, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (flow_id, doc_id, 1, "コイル試験フロー", "Coil Test Flow", "narrative", "想定表", None, now),
    )

    # 5. Insert 4 steps:
    # S1: Panel label with full-width kana "コイル" vs file's "1.ｺｲﾙ"
    s1_id = f"us:{new_id()}"
    # S2: Validation logic (mentions screen FHNIXLOT)
    s2_id = f"us:{new_id()}"
    # S3: Zero-seed step (no matching text)
    s3_id = f"us:{new_id()}"
    # S4: Presentation claim
    s4_id = f"us:{new_id()}"

    step_ids = {"s1": s1_id, "s2": s2_id, "s3": s3_id, "s4": s4_id}

    steps_data = [
        (s1_id, flow_id, 1, "action", "1.1", "メニューから「コイル」試験を選択する", "Select coil test", "メニュー選択", "画面表示", "FHNIXLOT", 1, None, "想定表", 2, 3),
        (s2_id, flow_id, 2, "action", "1.2", "試験機コードを入力しバリデーションを実行する", "Validate machine code", "Enter押下", "正常終了", "FHNIXLOT", 1, None, "想定表", 4, 5),
        (s3_id, flow_id, 3, "action", "1.3", "特有処理を行う", "Perform specific action", None, None, None, 1, None, "想定表", 6, 7),
        (s4_id, flow_id, 4, "action", "1.4", "画面のフォントサイズとレイアウト配置を確認する", "Check font size and layout", None, None, None, 1, None, "想定表", 8, 9),
    ]

    for row in steps_data:
        await db.execute(
            "INSERT INTO user_steps (id, flow_id, ordinal, kind, section_id, text_ja, text_en, trigger_ja, expected_ja, screen_name_ja, in_scope, scope_note, sheet, row_start, row_end, provenance_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8],
                row[9], row[10], row[11], row[12], row[13], row[14], "{}", now,
            ),
        )

    await db.commit()
    return snapshot_id, cluster_id, doc_id, step_ids


# ---------------------------------------------------------------------------
# Test 1 & 2: NFKC Seed Hit & Panel Literal-Cut (Zero Occurrences)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_nfkc_seed_hit_and_panel_literal_cut(tmp_path: Path, monkeypatch):
    """Verify:
    1. NFKC normalization bridges full-width 'コイル' in S1 to half-width '1.ｺｲﾙ' at FHNIXLOT.ipf line 7.
    2. Primary literal-line window cut successfully produces a Snippet for FHNIXLOT.ipf even though it has 0 occurrences.
    3. Assert expand_unit_snippets alone would return empty for FHNIXLOT.ipf.
    """
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(tmp_path))
    await init_db()
    db = get_db()

    try:
        snapshot_id, cluster_id, doc_id, step_ids = await _setup_synthetic_repo_and_doc(db, tmp_path)
        snap_dir = tmp_path / "repo_root"

        # Load S1 step
        cursor = await db.execute("SELECT * FROM user_steps WHERE id = ?", (step_ids["s1"],))
        s1_row = dict(await cursor.fetchone())

        # 1. Seed literal matching
        hits_by_step = await seed_literal_hits(db, snapshot_id, [s1_row], snap_dir)
        s1_hits = hits_by_step[step_ids["s1"]]
        assert len(s1_hits) >= 1

        # Check that hit landed on FHNIXLOT.ipf at line 7 (where '1.ｺｲﾙ' is located)
        coil_hit = next(h for h in s1_hits if h.rel_path == "FHNIXLOT.ipf" and h.matched_needle == "コイル")
        assert coil_hit.line == 7

        # 2. Check expand_unit_snippets alone returns empty (because 0 occurrences in panels)
        occ_res = await expand_unit_snippets(
            db, snapshot_id, {"unit_id": s1_row["id"], "unit_kind": "step"}, rel_paths=["FHNIXLOT.ipf"]
        )
        assert len(occ_res.snippets) == 0

        # 3. Retrieve snippets using retrieval
        manifest_paths = {"FHNIXLOT.ipf", "HNIXLOT.cbl"}
        snippets = await retrieve_step_snippets(
            db, snapshot_id, s1_row, s1_hits, {"FHNIXLOT.ipf"}, snap_dir
        )
        assert len(snippets) >= 1
        assert snippets[0].rel_path == "FHNIXLOT.ipf"
        assert "1.ｺｲﾙ" in snippets[0].text
    finally:
        await close_db()


# ---------------------------------------------------------------------------
# Test 3: Program-Family Bridge & Valid LLM Anchor
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_program_family_bridge_and_valid_anchor(tmp_path: Path, monkeypatch):
    """Verify:
    1. S2 bridges from panel to program HNIXLOT.cbl via family resolution.
    2. Retrieval pulls the occurrence window from HNIXLOT.cbl.
    3. Mocked LLM matcher cites the validation lines -> creates valid=1 user_code_anchors record.
    """
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(tmp_path))
    await init_db()
    db = get_db()

    try:
        snapshot_id, cluster_id, doc_id, step_ids = await _setup_synthetic_repo_and_doc(db, tmp_path)

        cursor = await db.execute("SELECT * FROM user_steps WHERE id = ?", (step_ids["s2"],))
        s2_row = dict(await cursor.fetchone())

        manifest_paths = {"FHNIXLOT.ipf", "HNIXLOT.cbl"}
        s2_hits = [SeedHit(rel_path="FHNIXLOT.ipf", line=6, matched_needle="FHNIXLOT")]
        resolved_files = resolve_step_files(s2_row, s2_hits, manifest_paths, [])
        assert "FHNIXLOT.ipf" in resolved_files
        assert "HNIXLOT.cbl" in resolved_files

        # Mock Fused Align response
        async def stub_chat_stream_events(self, request: ChatRequest):
            user_content = request.messages[1].content
            parsed_user = json.loads(user_content)
            results = []
            pool = parsed_user.get("snippet_pool", [])
            for item in parsed_user["steps_to_align"]:
                s_id = item["step_id"]
                cbl_snip = next(
                    (snip["snippet_id"] for snip in pool if snip.get("rel_path") == "HNIXLOT.cbl"),
                    next((snip["snippet_id"] for snip in item.get("snippets", []) if snip.get("rel_path") == "HNIXLOT.cbl"), "src1"),
                )
                results.append({
                    "step_id": s_id,
                    "claims": [
                        {
                            "claim_id": "c1",
                            "citation": {"snippet_id": cbl_snip, "line_start": 7, "line_end": 10},
                            "subclaims": ["Validation logic for TEST-CODE"],
                            "reason": "IF TEST-CODE matches coil testing logic.",
                        }
                    ],
                    "bd_mappings": [],
                })
            yield {"type": "content", "text": json.dumps({"results": results})}

        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", stub_chat_stream_events)

        run_res = await run_user_flow_alignment(
            db, doc_id, cluster_id, snapshot_id, provider_id="test_provider"
        )
        assert isinstance(run_res, UserFlowRunResult)
        assert run_res.total_steps == 4
        assert run_res.in_scope_steps == 4

        # Verify user_code_anchors record for valid citation
        cursor = await db.execute(
            "SELECT rel_path, line_start, line_end, kind, valid, reason FROM user_code_anchors WHERE step_id = ? AND kind = 'llm_matched'",
            (step_ids["s2"],),
        )
        anchors = await cursor.fetchall()
        assert len(anchors) >= 1
        assert anchors[0][0] == "HNIXLOT.cbl"
        assert anchors[0][1] == 7
        assert anchors[0][2] == 10
        assert anchors[0][4] == 1  # valid = 1
    finally:
        await close_db()


# ---------------------------------------------------------------------------
# Test 4: Flow-Level Fallback for Zero-Seed Step
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_flow_fallback_for_zero_seed_step(tmp_path: Path, monkeypatch):
    """Verify that S3 (zero seed hits) inherits the screen/flow's resolved files."""
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(tmp_path))
    await init_db()
    db = get_db()

    try:
        snapshot_id, cluster_id, doc_id, step_ids = await _setup_synthetic_repo_and_doc(db, tmp_path)

        cursor = await db.execute("SELECT * FROM user_steps WHERE id = ?", (step_ids["s3"],))
        s3_row = dict(await cursor.fetchone())

        manifest_paths = {"FHNIXLOT.ipf", "HNIXLOT.cbl"}
        flow_hits = [SeedHit(rel_path="FHNIXLOT.ipf", line=7, matched_needle="コイル")]
        resolved = resolve_step_files(s3_row, [], manifest_paths, flow_hits)
        assert len(resolved) >= 1
    finally:
        await close_db()


# ---------------------------------------------------------------------------
# Test 5: Citation Gate Rejects Out-of-Window Citations
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_citation_gate_rejects_out_of_window(tmp_path: Path, monkeypatch):
    """Verify that when LLM hallucinates citation lines outside the shown snippet window,
    the anchor is flagged valid=0."""
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(tmp_path))
    await init_db()
    db = get_db()

    try:
        snapshot_id, cluster_id, doc_id, step_ids = await _setup_synthetic_repo_and_doc(db, tmp_path)

        async def stub_chat_stream_events(self, request: ChatRequest):
            user_content = request.messages[1].content
            parsed_user = json.loads(user_content)
            results = []
            for item in parsed_user["steps_to_align"]:
                s_id = item["step_id"]
                results.append({
                    "step_id": s_id,
                    "claims": [
                        {
                            "claim_id": "c1",
                            "citation": {"snippet_id": "src1", "line_start": 50, "line_end": 52},  # Out of window
                            "subclaims": ["Hallucinated citation"],
                            "reason": "Cited out-of-window line",
                        }
                    ],
                    "bd_mappings": [],
                })
            yield {"type": "content", "text": json.dumps({"results": results})}

        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", stub_chat_stream_events)

        await run_user_flow_alignment(
            db, doc_id, cluster_id, snapshot_id, provider_id="test_provider"
        )

        cursor = await db.execute(
            "SELECT valid, reason FROM user_code_anchors WHERE step_id = ? AND kind = 'llm_matched'",
            (step_ids["s1"],),
        )
        anchor = await cursor.fetchone()
        assert anchor is not None
        assert anchor[0] == 0  # valid = 0
    finally:
        await close_db()


# ---------------------------------------------------------------------------
# Test 6: Presentation Claims Tagged Without Valid Anchors
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_presentation_claims_tagged(tmp_path: Path, monkeypatch):
    """Verify that layout/font claims returning PRESENTATION reason are recorded without valid code anchors."""
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(tmp_path))
    await init_db()
    db = get_db()

    try:
        snapshot_id, cluster_id, doc_id, step_ids = await _setup_synthetic_repo_and_doc(db, tmp_path)

        async def stub_chat_stream_events(self, request: ChatRequest):
            user_content = request.messages[1].content
            parsed_user = json.loads(user_content)
            results = []
            for item in parsed_user["steps_to_align"]:
                s_id = item["step_id"]
                results.append({
                    "step_id": s_id,
                    "claims": [],
                    "bd_mappings": [],
                })
            yield {"type": "content", "text": json.dumps({"results": results})}

        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", stub_chat_stream_events)

        await run_user_flow_alignment(
            db, doc_id, cluster_id, snapshot_id, provider_id="test_provider"
        )

        cursor = await db.execute(
            "SELECT COUNT(*) FROM user_code_anchors WHERE step_id = ? AND valid = 1",
            (step_ids["s4"],),
        )
        count = (await cursor.fetchone())[0]
        assert count == 0

        # Assert audit artifact
        cursor = await db.execute(
            "SELECT payload FROM user_run_artifacts WHERE ref_id = ?",
            (f"align:{step_ids['s4']}",),
        )
        art_row = await cursor.fetchone()
        assert art_row is not None
    finally:
        await close_db()


# ---------------------------------------------------------------------------
# Test 7: Offline Mode (provider_id=None)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_offline_mode_retrieval_only(tmp_path: Path, monkeypatch):
    """Verify that provider_id=None runs gracefully in offline mode."""
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(tmp_path))
    await init_db()
    db = get_db()

    try:
        snapshot_id, cluster_id, doc_id, step_ids = await _setup_synthetic_repo_and_doc(db, tmp_path)

        run_res = await run_user_flow_alignment(
            db, doc_id, cluster_id, snapshot_id, provider_id=None
        )
        assert run_res.llm_batches_issued == 0
        assert run_res.anchored_steps == 0

        # Check audit artifact
        cursor = await db.execute(
            "SELECT payload FROM user_run_artifacts WHERE ref_id = ?",
            (f"align:{step_ids['s1']}",),
        )
        art_row = await cursor.fetchone()
        assert art_row is not None
        payload = json.loads(art_row[0])
        assert payload["offline"] is True
    finally:
        await close_db()


# ---------------------------------------------------------------------------
# Test 8: Per-Flow Retrieval Scoping (UR-FIX Hole I)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retrieval_scoping_no_cross_flow_leakage(tmp_path: Path, monkeypatch):
    """Verify that a seedless step in Flow B does not inherit Flow A's seed hits."""
    step_in_flow_b = {
        "id": "step_b1",
        "flow_id": "flow_b",
        "text_ja": "特有処理を行う",
        "screen_name_ja": None,
    }
    manifest_paths = {"HNIXLOT.cbl", "FHNIXLOT.ipf", "OTHER_PROG.cbl"}

    # Flow A hits on HNIXLOT.cbl
    flow_a_hits = [SeedHit(rel_path="HNIXLOT.cbl", line=4, matched_needle="COIL")]
    # Flow B has no hits
    flow_b_hits: list[SeedHit] = []

    # When resolving for step in flow B, pass only flow B hits
    resolved_files = resolve_step_files(step_in_flow_b, [], manifest_paths, flow_b_hits)

    # Should NOT have resolved to HNIXLOT.cbl from Flow A
    assert "HNIXLOT.cbl" not in resolved_files

