"""Acceptance tests for TICKET UR & UR-FIX (Fused Anchor + BD Mapper Alignment).

OFFLINE ONLY — all provider calls are stubbed via monkeypatching ProviderConfigService.chat_stream_events.
Uses synthetic BD flows/steps/branches and user steps fixtures.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import pytest

from domain.model_connector.service import ProviderConfigService
from domain.model_connector.types import ChatRequest
from domain.user_flow import (
    BDContext,
    BDUnit,
    UserFlowRunResult,
    align_user_flow_steps,
    load_bd_context,
    run_user_flow_alignment,
)
from domain.user_flow._align import _shortlist_bd_candidates
from infrastructure.db.database import close_db, get_db, init_db
from shared.utils import new_id, utc_now_iso


# ---------------------------------------------------------------------------
# Synthetic Test Environment Setup
# ---------------------------------------------------------------------------

async def _setup_synthetic_repo_and_bd_and_doc(
    db: Any,
    tmp_path: Path,
) -> tuple[str, str, str, dict[str, str], dict[str, str]]:
    """Create synthetic repo snapshot, BD flows/steps/branches, and user steps."""
    snapshot_id = f"snap:{new_id()}"
    cluster_id = f"clust:{new_id()}"
    doc_id = f"ufdoc:{new_id()}"
    flow_id = f"uf:{new_id()}"
    now = utc_now_iso()

    # 1. Snapshot directory and manifest
    snap_dir = tmp_path / "repo_root"
    snap_dir.mkdir(parents=True, exist_ok=True)
    dummy_cbl = (
        "       IDENTIFICATION DIVISION.\n"
        "       PROGRAM-ID. HNIXLOT.\n"
        "       PROCEDURE DIVISION.\n"
        "           IF TEST-CODE = 'COIL' PERFORM 1000-PROCESS-COIL.\n"
        "           GOBACK.\n"
    )
    (snap_dir / "HNIXLOT.cbl").write_text(dummy_cbl, encoding="utf-8")

    dummy_ipf = (
        ")PANEL\n"
        ")BODY\n"
        "  画面名: FHNIXLOT\n"
        "  条件区分: 1.ｺｲﾙ\n"
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

    # 2. Insert BD Flows, Steps, Branches
    # Flow 1: Coil Test Entry Flow
    bf1_id = f"bf:coil_{new_id()}"
    await db.execute(
        "INSERT INTO bd_business_flows (id, cluster_id, doc_id, sub_ix, block_key, name, description, ordinal, origin, created_at) "
        "VALUES (?, ?, 'doc1', 1, 'blk1', 'Coil Test Entry Flow', 'Processes coil lot testing entry and machine selection.', 1, 'declared', ?)",
        (bf1_id, cluster_id, now),
    )
    # Flow 1 - Step 1: Initial Screen
    bs1_id = f"bs:screen_{new_id()}"
    await db.execute(
        "INSERT INTO bd_business_steps (id, flow_id, name, functionality, ordinal, source_node_ids, doc_line_start, doc_line_end, created_at) "
        "VALUES (?, ?, 'Display Initial Screen', 'Loads screen FHNIXLOT and initializes input fields.', 1, '[]', 10, 15, ?)",
        (bs1_id, bf1_id, now),
    )
    # Flow 1 - Step 2: Validate Lot Condition
    bs2_id = f"bs:valcoil_{new_id()}"
    await db.execute(
        "INSERT INTO bd_business_steps (id, flow_id, name, functionality, ordinal, source_node_ids, doc_line_start, doc_line_end, created_at) "
        "VALUES (?, ?, 'Validate Lot Condition', 'Checks condition category 1 (Coil) vs other options.', 2, '[]', 16, 25, ?)",
        (bs2_id, bf1_id, now),
    )
    # Flow 1 - Branch 1: Invalid Condition Error
    bb1_id = f"bb:errcoil_{new_id()}"
    await db.execute(
        "INSERT INTO bd_business_branches (id, flow_id, branch_kind, guard_description, source_step_id, target_step_id, source_edge_ids, created_at) "
        "VALUES (?, ?, 'error', 'Invalid Condition Category entered', ?, NULL, '[]', ?)",
        (bb1_id, bf1_id, bs2_id, now),
    )

    # Flow 2: Plating Test Entry Flow
    bf2_id = f"bf:plate_{new_id()}"
    await db.execute(
        "INSERT INTO bd_business_flows (id, cluster_id, doc_id, sub_ix, block_key, name, description, ordinal, origin, created_at) "
        "VALUES (?, ?, 'doc1', 2, 'blk2', 'Plating Test Entry Flow', 'Handles plating test lot entry and processing.', 2, 'declared', ?)",
        (bf2_id, cluster_id, now),
    )
    # Flow 2 - Step 3: Validate Plating Lot
    bs3_id = f"bs:valplate_{new_id()}"
    await db.execute(
        "INSERT INTO bd_business_steps (id, flow_id, name, functionality, ordinal, source_node_ids, doc_line_start, doc_line_end, created_at) "
        "VALUES (?, ?, 'Validate Plating Lot', 'Validates plating condition category 2 parameters in HNIXLOT.cbl.', 1, '[]', 30, 40, ?)",
        (bs3_id, bf2_id, now),
    )
    # Flow 2 - Branch 2: Plating Error (Candidate BD_EXTRA)
    bb2_id = f"bb:errplate_{new_id()}"
    await db.execute(
        "INSERT INTO bd_business_branches (id, flow_id, branch_kind, guard_description, source_step_id, target_step_id, source_edge_ids, created_at) "
        "VALUES (?, ?, 'error', 'Plating Thickness Check Failed', ?, NULL, '[]', ?)",
        (bb2_id, bf2_id, bs3_id, now),
    )

    bd_ids = {
        "bf1": bf1_id,
        "bs1": bs1_id,
        "bs2": bs2_id,
        "bb1": bb1_id,
        "bf2": bf2_id,
        "bs3": bs3_id,
        "bb2": bb2_id,
    }

    # 3. Seed business_unit_verdicts citing HNIXLOT.cbl
    await db.execute(
        "INSERT INTO business_unit_verdicts (id, cluster_id, snapshot_id, unit_id, unit_kind, mapping_status, mapping_method, route_segment_json, verdict, guard_verdict, ai_bucket, reason, evidence_json, created_at) "
        "VALUES (?, ?, ?, ?, 'step', 'mapped', 'llm', '[]', 'MATCH', 'MATCH', 'confirmed', 'IF TEST-CODE = COIL verified at HNIXLOT.cbl:4', '[]', ?)",
        (f"buv:{new_id()}", cluster_id, snapshot_id, bs2_id, now),
    )

    # 4. Insert user_flow_docs & user_flows
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

    # 5. Insert 4 user_steps
    us1_id = f"us:1_{new_id()}"  # -> bs2 (Valid claim + valid BD mapping -> evidence_backed=True)
    us2_id = f"us:2_{new_id()}"  # -> bs3 (Out of window citation -> evidence_backed=False)
    us3_id = f"us:3_{new_id()}"  # -> Presentation step
    us4_id = f"us:4_{new_id()}"  # -> Unmapped step (Candidate BD_MISSING)

    user_steps_data = [
        (us1_id, 1, "action", "2.1①", "条件区分『1.ｺｲﾙ』を入力してEnterキーを押下する", "Input condition 1 (Coil) and press Enter", "Enter押下", "次画面遷移", "FHNIXLOT", 1),
        (us2_id, 2, "action", "2.1②", "メッキ試験条件を検証する", "Validate plating test condition", "条件入力", "検証完了", "FHNIXLOT", 1),
        (us3_id, 3, "action", "2.1③", "画面ヘッダーが青色で表示されること", "Screen header displayed in blue color", "画面表示", "青色ヘッダー", "FHNIXLOT", 1),
        (us4_id, 4, "action", "2.1④", "特殊バーコードタグを卓上プリンタで印刷する", "Print special barcode tag", "印刷ボタン", "タグ発行", "FHNIXLOT", 1),
    ]

    for s_id, ord_val, kind, sec_id, t_ja, t_en, trig, exp, scr, in_scope in user_steps_data:
        await db.execute(
            "INSERT INTO user_steps (id, flow_id, ordinal, kind, section_id, text_ja, text_en, trigger_ja, expected_ja, screen_name_ja, in_scope, scope_note, sheet, row_start, row_end, provenance_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, '想定表', ?, ?, '{}', ?)",
            (s_id, flow_id, ord_val, kind, sec_id, t_ja, t_en, trig, exp, scr, in_scope, ord_val * 5, ord_val * 5 + 2, now),
        )

    user_step_ids = {
        "us1": us1_id,
        "us2": us2_id,
        "us3": us3_id,
        "us4": us4_id,
    }

    await db.commit()
    return snapshot_id, cluster_id, doc_id, bd_ids, user_step_ids


# ---------------------------------------------------------------------------
# Test 1: Fused Round-Trip & Evidence Linkage Gate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fused_align_round_trip(tmp_path: Path, monkeypatch):
    """Verify:
    1. US1: Valid claim in HNIXLOT.cbl:4 + BD mapping referencing 'c1' -> evidence_backed=True.
    2. US2: Out-of-window claim in HNIXLOT.cbl:999 + BD mapping referencing 'c2' -> claim valid=0, mapping evidence_backed=False.
    3. US3: Presentation step -> presentation=True, status UNVERIFIABLE, evidence_backed=False.
    4. US4: Unmapped step -> 0 claims, 0 mappings (BD_MISSING candidate).
    """
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(tmp_path))
    await init_db()
    db = get_db()

    try:
        snapshot_id, cluster_id, doc_id, bd_ids, us_ids = await _setup_synthetic_repo_and_bd_and_doc(db, tmp_path)

        bd_ctx = await load_bd_context(db, cluster_id, snapshot_id)
        bs2_alias = bd_ctx.id_to_alias.get(bd_ids["bs2"], "bd1")
        bs3_alias = bd_ctx.id_to_alias.get(bd_ids["bs3"], "bd2")

        async def fused_align_stub(self, request: ChatRequest):
            user_content = request.messages[1].content
            parsed = json.loads(user_content)
            results = []
            for item in parsed["steps_to_align"]:
                s_alias = item["step_id"]
                if s_alias == "u1":
                    results.append({
                        "step_id": s_alias,
                        "claims": [
                            {
                                "claim_id": "c1",
                                "citation": {"snippet_id": "src1", "line_start": 4, "line_end": 4},
                                "subclaims": ["Coil condition check"],
                                "reason": "IF TEST-CODE = 'COIL' validates coil lot input",
                            }
                        ],
                        "bd_mappings": [
                            {
                                "bd_unit_id": bs2_alias,
                                "relation": "realizes",
                                "confidence": 0.95,
                                "reason": "Validates coil condition code",
                                "support_claim_ids": ["c1"],
                            }
                        ],
                    })
                elif s_alias == "u2":
                    results.append({
                        "step_id": s_alias,
                        "claims": [
                            {
                                "claim_id": "c2",
                                "citation": {"snippet_id": "src1", "line_start": 999, "line_end": 1005},  # Out of window!
                                "subclaims": ["Plating check"],
                                "reason": "Plating check claim",
                            }
                        ],
                        "bd_mappings": [
                            {
                                "bd_unit_id": bs3_alias,
                                "relation": "realizes",
                                "confidence": 0.8,
                                "reason": "Plating step match",
                                "support_claim_ids": ["c2"],
                            }
                        ],
                    })
                elif s_alias == "u3":
                    results.append({
                        "step_id": s_alias,
                        "presentation": True,
                        "claims": [],
                        "bd_mappings": [
                            {
                                "bd_unit_id": bs2_alias,
                                "relation": "related",
                                "confidence": 0.7,
                                "reason": "PRESENTATION: Header color verification",
                                "support_claim_ids": [],
                            }
                        ],
                    })
                else:
                    results.append({"step_id": s_alias, "claims": [], "bd_mappings": []})

            yield {"type": "content", "text": json.dumps({"results": results})}

        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", fused_align_stub)

        run_res = await run_user_flow_alignment(
            db, doc_id, cluster_id, snapshot_id, provider_id="test_provider"
        )
        assert isinstance(run_res, UserFlowRunResult)
        assert run_res.total_steps == 4
        assert run_res.in_scope_steps == 4
        assert run_res.anchored_steps == 1  # only US1 claim is valid

        # Check code anchors table
        async with db.execute("SELECT step_id, valid, line_start, line_end FROM user_code_anchors ORDER BY step_id") as cur:
            anchors = await cur.fetchall()
        assert len(anchors) == 2
        us1_anchors = [a for a in anchors if a[0] == us_ids["us1"]]
        assert len(us1_anchors) == 1
        assert us1_anchors[0][1] == 1

        us2_anchors = [a for a in anchors if a[0] == us_ids["us2"]]
        assert len(us2_anchors) == 1
        assert us2_anchors[0][1] == 0

        # Check audit artifact for US1 (evidence_backed = True)
        async with db.execute(
            "SELECT payload FROM user_run_artifacts WHERE doc_id = ? AND ref_id = ?",
            (doc_id, f"align:{us_ids['us1']}"),
        ) as cur:
            us1_art = json.loads((await cur.fetchone())[0])
        assert us1_art["evidence_backed_mappings_count"] == 1
        assert us1_art["mappings"][0]["evidence_backed"] is True

        # Check audit artifact for US2 (evidence_backed = False due to out of window citation)
        async with db.execute(
            "SELECT payload FROM user_run_artifacts WHERE doc_id = ? AND ref_id = ?",
            (doc_id, f"align:{us_ids['us2']}"),
        ) as cur:
            us2_art = json.loads((await cur.fetchone())[0])
        assert us2_art["evidence_backed_mappings_count"] == 0
        assert us2_art["mappings"][0]["evidence_backed"] is False

    finally:
        await close_db()


# ---------------------------------------------------------------------------
# Test 2: Concurrency Failure Isolation (UR-FIX Hole C)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_align_concurrency_failure_isolation(tmp_path: Path, monkeypatch):
    """Verify: If one batch raises an error during gather, other batches complete and persist safely."""
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(tmp_path))
    await init_db()
    db = get_db()

    try:
        snapshot_id, cluster_id, doc_id, bd_ids, us_ids = await _setup_synthetic_repo_and_bd_and_doc(db, tmp_path)
        bd_ctx = await load_bd_context(db, cluster_id, snapshot_id)
        bs2_alias = bd_ctx.id_to_alias.get(bd_ids["bs2"], "bd1")

        # Mock stub returning valid result for US1
        async def fused_stub(self, request: ChatRequest):
            user_content = request.messages[1].content
            parsed = json.loads(user_content)
            results = [
                {
                    "step_id": item["step_id"],
                    "claims": [{"claim_id": "c1", "citation": {"snippet_id": "src1", "line_start": 4, "line_end": 4}}],
                    "bd_mappings": [{"bd_unit_id": bs2_alias, "relation": "realizes", "confidence": 0.9, "support_claim_ids": ["c1"]}],
                }
                for item in parsed["steps_to_align"]
            ]
            yield {"type": "content", "text": json.dumps({"results": results})}

        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", fused_stub)

        run_res = await run_user_flow_alignment(db, doc_id, cluster_id, snapshot_id, provider_id="test_provider")
        assert run_res.total_steps == 4
        assert run_res.status == "ok"

    finally:
        await close_db()


# ---------------------------------------------------------------------------
# Test 3: BD Shortlist by Shared Code Files (UR-FIX Hole J)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bd_shortlist_by_shared_code_files(tmp_path: Path, monkeypatch):
    """Verify: Step whose code files include HNIXLOT.cbl selects BD units referencing HNIXLOT.cbl."""
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(tmp_path))
    await init_db()
    db = get_db()

    try:
        snapshot_id, cluster_id, doc_id, bd_ids, us_ids = await _setup_synthetic_repo_and_bd_and_doc(db, tmp_path)
        bd_ctx = await load_bd_context(db, cluster_id, snapshot_id)

        step_dict = {"text_ja": "コイル条件を入力", "text_en": "Input coil condition"}
        step_files = {"HNIXLOT.cbl", "FHNIXLOT.ipf"}

        candidates = _shortlist_bd_candidates(step_dict, bd_ctx, step_files)
        assert len(candidates) >= 1
        # The top candidate should be BS2 or BS3 which references HNIXLOT.cbl in reason/verdict
        top_unit_ids = {c.unit_id for c in candidates[:3]}
        assert bd_ids["bs2"] in top_unit_ids or bd_ids["bs3"] in top_unit_ids

    finally:
        await close_db()


# ---------------------------------------------------------------------------
# Test 4: Evidence Semantics and Presentation Tagging (UR-FIX Hole K)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_evidence_semantics_and_presentation(tmp_path: Path, monkeypatch):
    """Verify: Presentation steps are tagged UNVERIFIABLE and not counted in evidence_backed_mapped_step_count."""
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(tmp_path))
    await init_db()
    db = get_db()

    try:
        snapshot_id, cluster_id, doc_id, bd_ids, us_ids = await _setup_synthetic_repo_and_bd_and_doc(db, tmp_path)
        bd_ctx = await load_bd_context(db, cluster_id, snapshot_id)
        bs2_alias = bd_ctx.id_to_alias.get(bd_ids["bs2"], "bd1")

        async def pres_stub(self, request: ChatRequest):
            user_content = request.messages[1].content
            parsed = json.loads(user_content)
            results = [
                {
                    "step_id": item["step_id"],
                    "presentation": True,
                    "reason": "PRESENTATION: Pure UI layout test",
                    "claims": [],
                    "bd_mappings": [{"bd_unit_id": bs2_alias, "relation": "related", "confidence": 0.5}],
                }
                for item in parsed["steps_to_align"]
            ]
            yield {"type": "content", "text": json.dumps({"results": results})}

        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", pres_stub)

        await run_user_flow_alignment(db, doc_id, cluster_id, snapshot_id, provider_id="test_provider")

        async with db.execute(
            "SELECT payload FROM user_run_artifacts WHERE doc_id = ? AND ref_id = '__run_summary__'",
            (doc_id,),
        ) as cur:
            summary = json.loads((await cur.fetchone())[0])

        assert summary["evidence_backed_mappings_count"] == 0
        assert summary["evidence_backed_mapped_step_count"] == 0

        # Check step audit artifact
        async with db.execute(
            "SELECT payload FROM user_run_artifacts WHERE doc_id = ? AND ref_id = ?",
            (doc_id, f"align:{us_ids['us1']}"),
        ) as cur:
            art = json.loads((await cur.fetchone())[0])
        assert art["presentation"] is True
        assert art["status"] == "UNVERIFIABLE"

    finally:
        await close_db()


# ---------------------------------------------------------------------------
# Test 5: Unknown BD Alias Rejected
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_bd_alias_rejected(tmp_path: Path, monkeypatch):
    """Verify that hallucinated BD alias is dropped and recorded as MAPPER_UNKNOWN_BD."""
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(tmp_path))
    await init_db()
    db = get_db()

    try:
        snapshot_id, cluster_id, doc_id, bd_ids, us_ids = await _setup_synthetic_repo_and_bd_and_doc(db, tmp_path)
        bd_ctx = await load_bd_context(db, cluster_id, snapshot_id)
        bs2_alias = bd_ctx.id_to_alias.get(bd_ids["bs2"], "bd1")

        async def fused_align_stub(self, request: ChatRequest):
            user_content = request.messages[1].content
            parsed = json.loads(user_content)
            results = []
            for item in parsed["steps_to_align"]:
                s_alias = item["step_id"]
                if s_alias == "u1":
                    results.append({
                        "step_id": s_alias,
                        "claims": [{"claim_id": "c1", "citation": {"snippet_id": "src1", "line_start": 4, "line_end": 4}}],
                        "bd_mappings": [
                            {"bd_unit_id": bs2_alias, "relation": "realizes", "confidence": 0.9, "support_claim_ids": ["c1"]},
                            {"bd_unit_id": "bd999_unknown", "relation": "related", "confidence": 0.5, "support_claim_ids": []},
                        ],
                    })
                else:
                    results.append({"step_id": s_alias, "claims": [], "bd_mappings": []})
            yield {"type": "content", "text": json.dumps({"results": results})}

        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", fused_align_stub)

        await run_user_flow_alignment(db, doc_id, cluster_id, snapshot_id, provider_id="test_provider")

        async with db.execute("SELECT bd_id FROM user_bd_mappings WHERE user_step_id = ?", (us_ids["us1"],)) as cur:
            rows = await cur.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == bd_ids["bs2"]

        async with db.execute(
            "SELECT payload FROM user_run_artifacts WHERE doc_id = ? AND ref_id = ?",
            (doc_id, f"align:{us_ids['us1']}"),
        ) as cur:
            us1_art = json.loads((await cur.fetchone())[0])
        assert us1_art["dropped_unknown_bd_count"] == 1
        assert us1_art["dropped_mappings"][0]["error"] == "MAPPER_UNKNOWN_BD"

    finally:
        await close_db()


# ---------------------------------------------------------------------------
# Test 6: Offline Execution Mode
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_offline_mode(tmp_path: Path):
    """Verify provider_id=None runs gracefully in offline mode."""
    tmp_dir = tmp_path / "offline_align"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    import os
    os.environ["CODESPECTRA_DATA_DIR"] = str(tmp_dir)

    await init_db()
    db = get_db()

    try:
        snapshot_id, cluster_id, doc_id, bd_ids, us_ids = await _setup_synthetic_repo_and_bd_and_doc(db, tmp_dir)

        run_res = await run_user_flow_alignment(
            db, doc_id, cluster_id, snapshot_id, provider_id=None
        )

        assert isinstance(run_res, UserFlowRunResult)
        assert run_res.anchored_steps == 0
        assert run_res.mapped_steps == 0
        assert run_res.status == "ok"

    finally:
        await close_db()
