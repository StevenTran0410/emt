"""Unit tests for Stage U3 (User-step to BD-unit Mapping n:m).

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
    load_bd_context,
    map_user_steps_to_bd,
)
from infrastructure.db.database import close_db, get_db, init_db
from shared.utils import new_id, utc_now_iso


# ---------------------------------------------------------------------------
# Synthetic Test Environment Setup
# ---------------------------------------------------------------------------

async def _setup_synthetic_repo_and_bd_and_doc(
    db: Any,
    tmp_path: Path,
) -> tuple[str, str, str, dict[str, str], dict[str, str], list[dict[str, Any]]]:
    """Create synthetic BD flows/steps/branches and user steps."""
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
        "VALUES (?, ?, 'Validate Plating Lot', 'Validates plating condition category 2 parameters.', 1, '[]', 30, 40, ?)",
        (bs3_id, bf2_id, now),
    )
    # Flow 2 - Branch 2: Plating Error (NO user step will map to this -> Candidate BD_EXTRA)
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

    # 3. Seed business_unit_verdicts
    await db.execute(
        "INSERT INTO business_unit_verdicts (id, cluster_id, snapshot_id, unit_id, unit_kind, mapping_status, mapping_method, route_segment_json, verdict, guard_verdict, ai_bucket, reason, evidence_json, created_at) "
        "VALUES (?, ?, ?, ?, 'step', 'mapped', 'llm', '[]', 'MATCH', 'MATCH', 'confirmed', 'IF TEST-CODE = COIL verified at HNIXLOT.cbl:4', '[]', ?)",
        (f"buv:{new_id()}", cluster_id, snapshot_id, bs2_id, now),
    )
    await db.execute(
        "INSERT INTO business_unit_verdicts (id, cluster_id, snapshot_id, unit_id, unit_kind, mapping_status, mapping_method, route_segment_json, verdict, guard_verdict, ai_bucket, reason, evidence_json, created_at) "
        "VALUES (?, ?, ?, ?, 'branch', 'mapped', 'llm', '[]', 'MATCH', 'MATCH', 'confirmed', 'ELSE branch at HNIXLOT.cbl:5', '[]', ?)",
        (f"buv:{new_id()}", cluster_id, snapshot_id, bb1_id, now),
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
    us1_id = f"us:1_{new_id()}"  # -> bs2 (Validate Lot Condition)
    us2_id = f"us:2_{new_id()}"  # -> bf1 & bf2 (1:n multi-flow mapping)
    us3_id = f"us:3_{new_id()}"  # -> [] (Unmapped user step -> Candidate BD_MISSING)
    us4_id = f"us:4_{new_id()}"  # -> bb1 (Invalid Condition Error)

    user_steps_data = [
        (us1_id, 1, "action", "2.1①", "条件区分『1.ｺｲﾙ』を入力してEnterキーを押下する", "Input condition 1 (Coil) and press Enter", "Enter押下", "次画面遷移", "FHNIXLOT", 1),
        (us2_id, 2, "action", "2.1②", "全試験機共通のエントリ処理を開始する", "Start common entry processing across test machines", "処理開始", "エントリ完了", "FHNIXLOT", 1),
        (us3_id, 3, "expectation", "2.1③", "物理バーコードタグを卓上プリンタで印刷する", "Print physical barcode tag on desktop printer", "自動印刷", "タグ発行", "FHNIXLOT", 1),
        (us4_id, 4, "error_rule", "2.1④", "条件区分に不正値を入力した場合はエラーメッセージを表示する", "Display error message if invalid condition code is entered", "不正入力", "エラー表示", "FHNIXLOT", 1),
    ]

    in_scope_steps: list[dict[str, Any]] = []
    for s_id, ord_val, kind, sec_id, t_ja, t_en, trig, exp, scr, in_scope in user_steps_data:
        await db.execute(
            "INSERT INTO user_steps (id, flow_id, ordinal, kind, section_id, text_ja, text_en, trigger_ja, expected_ja, screen_name_ja, in_scope, scope_note, sheet, row_start, row_end, provenance_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, '想定表', ?, ?, '{}', ?)",
            (s_id, flow_id, ord_val, kind, sec_id, t_ja, t_en, trig, exp, scr, in_scope, ord_val * 5, ord_val * 5 + 2, now),
        )
        in_scope_steps.append({
            "id": s_id,
            "flow_id": flow_id,
            "ordinal": ord_val,
            "kind": kind,
            "section_id": sec_id,
            "text_ja": t_ja,
            "text_en": t_en,
            "trigger_ja": trig,
            "expected_ja": exp,
            "screen_name_ja": scr,
            "in_scope": in_scope,
            "sheet": "想定表",
            "row_start": ord_val * 5,
            "row_end": ord_val * 5 + 2,
        })

    user_step_ids = {
        "us1": us1_id,
        "us2": us2_id,
        "us3": us3_id,
        "us4": us4_id,
    }

    await db.commit()
    return snapshot_id, cluster_id, doc_id, bd_ids, user_step_ids, in_scope_steps


# ---------------------------------------------------------------------------
# Test 1: n:m Round-Trip Mapping
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_nm_mapping_round_trip(tmp_path: Path, monkeypatch):
    """Verify n:m mapping persistence using map_user_steps_to_bd."""
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(tmp_path))
    await init_db()
    db = get_db()

    try:
        snapshot_id, cluster_id, doc_id, bd_ids, us_ids, in_scope_steps = await _setup_synthetic_repo_and_bd_and_doc(db, tmp_path)

        bd_ctx = await load_bd_context(db, cluster_id, snapshot_id)
        bs2_alias = bd_ctx.id_to_alias[bd_ids["bs2"]]
        bf1_alias = bd_ctx.id_to_alias[bd_ids["bf1"]]
        bf2_alias = bd_ctx.id_to_alias[bd_ids["bf2"]]
        bb1_alias = bd_ctx.id_to_alias[bd_ids["bb1"]]

        async def mapper_stub(self, request: ChatRequest):
            user_content = request.messages[1].content
            parsed_user = json.loads(user_content)
            results = []
            for item in parsed_user["steps_to_map"]:
                u_id = item["unit_id"]
                if u_id == "u1":
                    results.append({
                        "unit_id": u_id,
                        "mappings": [{"bd_id": bs2_alias, "relation": "realizes", "confidence": 0.95, "reason": "Validates lot condition"}],
                    })
                elif u_id == "u2":
                    results.append({
                        "unit_id": u_id,
                        "mappings": [
                            {"bd_id": bf1_alias, "relation": "realizes", "confidence": 0.9, "reason": "Coil entry"},
                            {"bd_id": bf2_alias, "relation": "partial", "confidence": 0.75, "reason": "Plating partial"},
                        ],
                    })
                elif u_id == "u3":
                    results.append({"unit_id": u_id, "mappings": []})
                elif u_id == "u4":
                    results.append({
                        "unit_id": u_id,
                        "mappings": [{"bd_id": bb1_alias, "relation": "realizes", "confidence": 0.98, "reason": "Error branch"}],
                    })
            yield {"type": "content", "text": json.dumps({"results": results})}

        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", mapper_stub)

        mapped_count, batches = await map_user_steps_to_bd(
            db, doc_id, cluster_id, snapshot_id, "run1", in_scope_steps, provider_id="test_provider"
        )
        assert mapped_count == 3

        # Query user_bd_mappings
        async with db.execute("SELECT user_step_id, bd_kind, bd_id, relation FROM user_bd_mappings ORDER BY user_step_id, bd_id") as cur:
            rows = await cur.fetchall()

        assert len(rows) == 4
        us1_maps = [r for r in rows if r[0] == us_ids["us1"]]
        assert len(us1_maps) == 1
        assert us1_maps[0][2] == bd_ids["bs2"]

    finally:
        await close_db()


# ---------------------------------------------------------------------------
# Test 2: Unknown Alias Dropped (MAPPER_UNKNOWN_BD)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_alias_dropped(tmp_path: Path, monkeypatch):
    """Verify unknown alias bd999 is dropped and recorded as MAPPER_UNKNOWN_BD."""
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(tmp_path))
    await init_db()
    db = get_db()

    try:
        snapshot_id, cluster_id, doc_id, bd_ids, us_ids, in_scope_steps = await _setup_synthetic_repo_and_bd_and_doc(db, tmp_path)
        bd_ctx = await load_bd_context(db, cluster_id, snapshot_id)
        bs2_alias = bd_ctx.id_to_alias[bd_ids["bs2"]]

        async def mapper_stub(self, request: ChatRequest):
            user_content = request.messages[1].content
            parsed_user = json.loads(user_content)
            results = []
            for item in parsed_user["steps_to_map"]:
                u_id = item["unit_id"]
                if u_id == "u1":
                    results.append({
                        "unit_id": u_id,
                        "mappings": [
                            {"bd_id": bs2_alias, "relation": "realizes", "confidence": 0.9, "reason": "Valid"},
                            {"bd_id": "bd999", "relation": "related", "confidence": 0.5, "reason": "Unknown"},
                        ],
                    })
                else:
                    results.append({"unit_id": u_id, "mappings": []})
            yield {"type": "content", "text": json.dumps({"results": results})}

        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", mapper_stub)

        mapped_count, batches = await map_user_steps_to_bd(
            db, doc_id, cluster_id, snapshot_id, "run1", in_scope_steps, provider_id="test_provider"
        )
        assert mapped_count == 1

        async with db.execute("SELECT bd_id FROM user_bd_mappings WHERE user_step_id = ?", (us_ids["us1"],)) as cur:
            rows = await cur.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == bd_ids["bs2"]

    finally:
        await close_db()


# ---------------------------------------------------------------------------
# Test 3: Alias Coverage Enforcement (Retry Ladder & Fallback)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_alias_coverage_enforcement(tmp_path: Path, monkeypatch):
    """Verify that malformed/duplicate alias triggers retry ladder."""
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(tmp_path))
    await init_db()
    db = get_db()

    try:
        snapshot_id, cluster_id, doc_id, bd_ids, us_ids, in_scope_steps = await _setup_synthetic_repo_and_bd_and_doc(db, tmp_path)
        bd_ctx = await load_bd_context(db, cluster_id, snapshot_id)
        bs2_alias = bd_ctx.id_to_alias[bd_ids["bs2"]]

        attempts = 0

        async def mapper_stub(self, request: ChatRequest):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                bad_results = [
                    {"unit_id": "u1", "mappings": []},
                    {"unit_id": "u1", "mappings": []},
                ]
                yield {"type": "content", "text": json.dumps({"results": bad_results})}
            else:
                user_content = request.messages[1].content
                parsed_user = json.loads(user_content)
                recovered = [
                    {
                        "unit_id": item["unit_id"],
                        "mappings": [{"bd_id": bs2_alias, "relation": "realizes", "confidence": 0.9, "reason": "Recovered"}]
                        if item["unit_id"] == "u1" else [],
                    }
                    for item in parsed_user["steps_to_map"]
                ]
                yield {"type": "content", "text": json.dumps({"results": recovered})}

        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", mapper_stub)

        mapped_count, batches = await map_user_steps_to_bd(
            db, doc_id, cluster_id, snapshot_id, "run1", in_scope_steps, provider_id="test_provider"
        )
        assert mapped_count == 1
        assert attempts == 2

    finally:
        await close_db()


# ---------------------------------------------------------------------------
# Test 4: BD_EXTRA and BD_MISSING Candidates in Run Summary
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bd_extra_and_bd_missing_candidates(tmp_path: Path, monkeypatch):
    """Verify run summary records unmapped BD units (BD_EXTRA) and unmapped steps (BD_MISSING)."""
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(tmp_path))
    await init_db()
    db = get_db()

    try:
        snapshot_id, cluster_id, doc_id, bd_ids, us_ids, in_scope_steps = await _setup_synthetic_repo_and_bd_and_doc(db, tmp_path)
        bd_ctx = await load_bd_context(db, cluster_id, snapshot_id)
        bs2_alias = bd_ctx.id_to_alias[bd_ids["bs2"]]

        async def mapper_stub(self, request: ChatRequest):
            user_content = request.messages[1].content
            parsed_user = json.loads(user_content)
            results = [
                {
                    "unit_id": item["unit_id"],
                    "mappings": [{"bd_id": bs2_alias, "relation": "realizes", "confidence": 0.95, "reason": "Matches bs2"}]
                    if item["unit_id"] == "u1" else [],
                }
                for item in parsed_user["steps_to_map"]
            ]
            yield {"type": "content", "text": json.dumps({"results": results})}

        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", mapper_stub)

        await map_user_steps_to_bd(
            db, doc_id, cluster_id, snapshot_id, "run1", in_scope_steps, provider_id="test_provider"
        )

        async with db.execute(
            "SELECT payload FROM user_run_artifacts WHERE doc_id = ? AND ref_id = '__run_summary__'",
            (doc_id,),
        ) as cur:
            summary = json.loads((await cur.fetchone())[0])

        assert summary["total_user_steps"] == 4
        assert summary["total_mappings"] == 1
        assert us_ids["us4"] in summary["unmapped_user_step_ids"]
        assert bd_ids["bb2"] in summary["unmapped_bd_unit_ids"]

    finally:
        await close_db()


# ---------------------------------------------------------------------------
# Test 5: Catalog in System Message Discipline
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_catalog_in_system_message_discipline(tmp_path: Path, monkeypatch):
    """Verify system message contains full BD catalog, and user message contains steps batch."""
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(tmp_path))
    await init_db()
    db = get_db()

    try:
        snapshot_id, cluster_id, doc_id, bd_ids, us_ids, in_scope_steps = await _setup_synthetic_repo_and_bd_and_doc(db, tmp_path)

        captured_requests: list[ChatRequest] = []

        async def mapper_stub(self, request: ChatRequest):
            captured_requests.append(request)
            user_content = request.messages[1].content
            parsed_user = json.loads(user_content)
            results = [{"unit_id": item["unit_id"], "mappings": []} for item in parsed_user["steps_to_map"]]
            yield {"type": "content", "text": json.dumps({"results": results})}

        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", mapper_stub)

        await map_user_steps_to_bd(
            db, doc_id, cluster_id, snapshot_id, "run1", in_scope_steps, provider_id="test_provider"
        )

        assert len(captured_requests) >= 1
        sys_msg = captured_requests[0].messages[0].content
        user_msg = captured_requests[0].messages[1].content

        assert "TECHNICAL BD FLOWS AND UNITS CATALOG:" in sys_msg
        assert "steps_to_map" in user_msg

    finally:
        await close_db()


# ---------------------------------------------------------------------------
# Test 6: Offline Execution Mode (provider_id=None)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_offline_mode(tmp_path: Path):
    """Verify offline mode with provider_id=None."""
    tmp_dir = tmp_path / "offline_db"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    import os
    os.environ["CODESPECTRA_DATA_DIR"] = str(tmp_dir)

    await init_db()
    db = get_db()

    try:
        snapshot_id, cluster_id, doc_id, bd_ids, us_ids, in_scope_steps = await _setup_synthetic_repo_and_bd_and_doc(db, tmp_dir)

        mapped_count, batches = await map_user_steps_to_bd(
            db, doc_id, cluster_id, snapshot_id, "run1", in_scope_steps, provider_id=None
        )

        assert mapped_count == 0
        assert batches == 0

        async with db.execute(
            "SELECT payload FROM user_run_artifacts WHERE doc_id = ? AND ref_id = ?",
            (doc_id, f"mapping:{us_ids['us1']}"),
        ) as cur:
            payload = json.loads((await cur.fetchone())[0])

        assert payload["offline"] is True

    finally:
        await close_db()
