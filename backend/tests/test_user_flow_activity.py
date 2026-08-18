"""Offline unit tests for Phase U2: Activity Condensation + Two-Tier Verification (TICKET U-P2).

Tests:
1. Condensation round-trip: LLM#5 groups multi-sheet flow steps; exact coverage gate passes.
2. Coverage gate & fallback: Missing alias in reply triggers retry and fallback path without lost steps.
3. Tier-1 Matcher: Batch evaluation with FULLY, PARTIAL, and NONE verdicts (NONE stored with bd_flow_id=NULL).
4. Gate: Steps of NONE activity skip Tier-2 align/verdict and receive UNVERIFIABLE/TIER1_NONE_MATCH; matched steps run with rescoped BD catalog.
5. Activity rollup, report JSON structure, and graph adapter activity nodes.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import pytest

from domain.model_connector.service import ProviderConfigService
from domain.model_connector.types import ChatRequest
from domain.user_flow import (
    UserFlowRunResult,
    condense_flow_activities,
    get_user_flow_graph,
    get_user_flow_report,
    match_user_activities_to_bd_flows,
    run_user_flow_alignment,
    run_user_flow_verdicts,
)
from infrastructure.db.database import close_db, get_db, init_db
from shared.utils import new_id, utc_now_iso


# ---------------------------------------------------------------------------
# Fixture & Synthetic Database Setup
# ---------------------------------------------------------------------------

@pytest.fixture
async def app_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Provide isolated in-memory test database with full migrations."""
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(tmp_path / "data"))
    await init_db()
    db = get_db()
    yield db
    await close_db()


async def _setup_synthetic_activity_fixture(
    db: Any,
    tmp_path: Path,
) -> tuple[str, str, str, str, list[dict[str, Any]], list[dict[str, Any]]]:
    """Create synthetic snapshot, BD flows (BF1, BF2), user flow with 8 steps across 2 sheets."""
    snapshot_id = f"snap:{new_id()}"
    cluster_id = f"clust:{new_id()}"
    doc_id = f"ufdoc:{new_id()}"
    flow_id = f"uf:{new_id()}"
    now = utc_now_iso()

    # 1. Snapshot directory and source files
    snap_dir = tmp_path / "repo_root"
    snap_dir.mkdir(parents=True, exist_ok=True)
    cbl_text = "       IDENTIFICATION DIVISION.\n       PROGRAM-ID. TESTACT.\n       PROCEDURE DIVISION.\n           DISPLAY 'OK'.\n           GOBACK.\n"
    (snap_dir / "TESTACT.cbl").write_text(cbl_text, encoding="utf-8")

    await db.execute(
        "INSERT INTO repo_snapshots (id, local_repo_id, local_path, synced_at, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (snapshot_id, "test_repo", str(snap_dir), now, now),
    )
    await db.execute(
        "INSERT INTO manifest_files (id, snapshot_id, rel_path, language, category, size_bytes, mtime_ns, checksum) "
        "VALUES (?, ?, 'TESTACT.cbl', 'cobol', 'source', ?, 0, 'dummy_sha')",
        (f"mf:{new_id()}", snapshot_id, len(cbl_text)),
    )

    # 2. Document & Flow
    await db.execute(
        "INSERT INTO user_flow_docs (id, source_name, file_hash, imported_at) "
        "VALUES (?, ?, ?, ?)",
        (doc_id, "test_scenario.xlsx", "hash123", now),
    )
    await db.execute(
        "INSERT INTO user_flows (id, doc_id, ordinal, name_ja, name_en, kind, sheet, scope_note, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (flow_id, doc_id, 1, "コイル処理フロー", "Coil Processing Flow", "normal", "想定表", None, now),
    )

    # 3. 8 Steps across 2 sheets: Sheet 1 (s1-s4), Sheet 2 (s5-s8)
    steps_data = []
    for i in range(1, 9):
        s_id = f"step:{new_id()}"
        sheet = "想定表" if i <= 4 else "ホスト_エラー1"
        sec = "2.1①" if i <= 2 else ("2.1②" if i <= 4 else "4.1")
        kind = "action" if i in (1, 3, 5, 7) else "expectation"
        r_start = i * 10 if i <= 4 else (i - 4) * 10
        r_end = r_start + 5
        text_en = f"Step {i} Description in English"
        text_ja = f"ステップ {i} 日本語"

        await db.execute(
            "INSERT INTO user_steps (id, flow_id, ordinal, kind, section_id, text_ja, text_en, trigger_ja, expected_ja, screen_name_ja, in_scope, scope_note, sheet, row_start, row_end, provenance_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (s_id, flow_id, i, kind, sec, text_ja, text_en, "クリック", "画面表示", "FHNIXLOT", 1, None, sheet, r_start, r_end, "{}", now),
        )
        steps_data.append({
            "id": s_id,
            "flow_id": flow_id,
            "ordinal": i,
            "kind": kind,
            "section_id": sec,
            "text_en": text_en,
            "text_ja": text_ja,
            "trigger_ja": "クリック",
            "expected_ja": "画面表示",
            "screen_name_ja": "FHNIXLOT",
            "in_scope": 1,
            "scope_note": None,
            "sheet": sheet,
            "row_start": r_start,
            "row_end": r_end,
        })

    # 4. BD Business Flows (BF1: Coil Process, BF2: Error Handler)
    bf1_id = f"bf:{new_id()}"
    bf2_id = f"bf:{new_id()}"

    await db.execute(
        "INSERT INTO bd_business_flows (id, cluster_id, doc_id, sub_ix, block_key, name, description, ordinal, origin, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (bf1_id, cluster_id, "bddoc:1", 0, "blk1", "Process Coil Lot", "Processes coil lot criteria", 1, "llm", now),
    )
    await db.execute(
        "INSERT INTO bd_business_flows (id, cluster_id, doc_id, sub_ix, block_key, name, description, ordinal, origin, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (bf2_id, cluster_id, "bddoc:1", 0, "blk2", "Handle Host Errors", "Handles mainframe error responses", 2, "llm", now),
    )

    bs1_id = f"bstep:{new_id()}"
    await db.execute(
        "INSERT INTO bd_business_steps (id, flow_id, name, functionality, ordinal, source_node_ids, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (bs1_id, bf1_id, "Validate Lot", "Validates coil lot condition in code", 1, "[]", now),
    )

    bd_flows = [
        {"id": bf1_id, "name": "Process Coil Lot", "description": "Processes coil lot criteria"},
        {"id": bf2_id, "name": "Handle Host Errors", "description": "Handles mainframe error responses"},
    ]

    await db.commit()
    return snapshot_id, cluster_id, doc_id, flow_id, steps_data, bd_flows


# ---------------------------------------------------------------------------
# Test 1: LLM#5 Condensation Round-trip & Exact Coverage
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_activity_condensation_roundtrip(app_db: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Mocked LLM#5 groups 8 steps into 2 activities with exact coverage."""
    snap_id, clust_id, doc_id, flow_id, steps, _ = await _setup_synthetic_activity_fixture(app_db, tmp_path)

    llm_payload = {
        "activities": [
            {
                "name_en": "Verify Coil Menu Options",
                "name_ja": "コイルメニュー検証",
                "summary_en": "Checks coil menu options and lot selection.",
                "member_step_aliases": ["s1", "s2", "s3", "s4"],
                "ordinal": 1,
            },
            {
                "name_en": "Validate Host Error Conditions",
                "name_ja": "ホストエラー条件検証",
                "summary_en": "Tests condition-matrix errors returned by host.",
                "member_step_aliases": ["s5", "s6", "s7", "s8"],
                "ordinal": 2,
            },
        ]
    }

    async def _mock_chat(self, req: ChatRequest, model_id: str | None = None):
        yield {"type": "content", "text": json.dumps(llm_payload)}

    monkeypatch.setattr(ProviderConfigService, "chat_stream_events", _mock_chat)

    flow_row = {"id": flow_id, "name_en": "Coil Flow", "name_ja": "コイルフロー", "sheet": "想定表"}
    activities, artifact = await condense_flow_activities(app_db, flow_row, steps, provider_id="prov:test")

    assert len(activities) == 2
    assert artifact["origin"] == "llm"
    assert activities[0]["name_en"] == "Verify Coil Menu Options"
    assert activities[0]["step_count"] == 4
    assert activities[1]["step_count"] == 4

    # Remapped step IDs match real step IDs
    member_ids_0 = json.loads(activities[0]["member_step_ids_json"])
    assert member_ids_0 == [s["id"] for s in steps[:4]]
    member_ids_1 = json.loads(activities[1]["member_step_ids_json"])
    assert member_ids_1 == [s["id"] for s in steps[4:]]


# ---------------------------------------------------------------------------
# Test 2: Condensation Fallback on Incomplete Step Coverage
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_activity_condensation_fallback(app_db: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """LLM reply drops step alias 's8' -> triggers fallback path, zero steps lost."""
    snap_id, clust_id, doc_id, flow_id, steps, _ = await _setup_synthetic_activity_fixture(app_db, tmp_path)

    # Incomplete reply missing s8
    bad_payload = {
        "activities": [
            {
                "name_en": "Incomplete Activity",
                "summary_en": "Missing s8",
                "member_step_aliases": ["s1", "s2", "s3", "s4", "s5", "s6", "s7"],
                "ordinal": 1,
            }
        ]
    }

    async def _mock_chat(self, req: ChatRequest, model_id: str | None = None):
        yield {"type": "content", "text": json.dumps(bad_payload)}

    monkeypatch.setattr(ProviderConfigService, "chat_stream_events", _mock_chat)

    flow_row = {"id": flow_id, "name_en": "Coil Flow", "sheet": "想定表"}
    activities, artifact = await condense_flow_activities(app_db, flow_row, steps, provider_id="prov:test")

    assert artifact["origin"] == "fallback"
    # Deterministic fallback groups by (sheet, section)
    all_member_ids = []
    for act in activities:
        assert act["origin"] == "fallback"
        all_member_ids.extend(json.loads(act["member_step_ids_json"]))

    # All 8 steps preserved
    assert len(all_member_ids) == 8
    assert set(all_member_ids) == {s["id"] for s in steps}


# ---------------------------------------------------------------------------
# Test 3: LLM#6 Tier-1 Activity Matcher (FULLY, PARTIAL, NONE)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tier1_activity_matcher(app_db: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Batch LLM#6 matches Act 1 to BF1 (FULLY) and Act 2 as NONE."""
    snap_id, clust_id, doc_id, flow_id, steps, bd_flows = await _setup_synthetic_activity_fixture(app_db, tmp_path)
    bf1_id = bd_flows[0]["id"]

    act1_id = f"act:{new_id()}"
    act2_id = f"act:{new_id()}"
    activities = [
        {
            "id": act1_id,
            "flow_id": flow_id,
            "ordinal": 1,
            "name_en": "Process Coil",
            "summary_en": "Process coil operations",
            "member_step_ids_json": json.dumps([steps[0]["id"], steps[1]["id"]]),
        },
        {
            "id": act2_id,
            "flow_id": flow_id,
            "ordinal": 2,
            "name_en": "Custom Special Feature",
            "summary_en": "Special client feature not in spec",
            "member_step_ids_json": json.dumps([steps[4]["id"], steps[5]["id"]]),
        },
    ]

    tier1_payload = {
        "matches": [
            {
                "activity_alias": "a1",
                "bd_flow_alias": "bf1",
                "match_status": "FULLY",
                "confidence": 0.95,
                "reason": "Matches coil process flow directly",
            },
            {
                "activity_alias": "a2",
                "bd_flow_alias": None,
                "match_status": "NONE",
                "confidence": 0.90,
                "reason": "Client custom feature, absent from BD specs",
            },
        ]
    }

    async def _mock_chat(self, req: ChatRequest, model_id: str | None = None):
        yield {"type": "content", "text": json.dumps(tier1_payload)}

    monkeypatch.setattr(ProviderConfigService, "chat_stream_events", _mock_chat)

    run_id = f"run:{new_id()}"
    matches, artifact = await match_user_activities_to_bd_flows(
        app_db, doc_id, clust_id, activities, steps, provider_id="prov:test", run_id=run_id
    )

    assert len(matches) == 2
    m1 = next(m for m in matches if m["activity_id"] == act1_id)
    assert m1["match_status"] == "FULLY"
    assert m1["bd_flow_id"] == bf1_id

    m2 = next(m for m in matches if m["activity_id"] == act2_id)
    assert m2["match_status"] == "NONE"
    assert m2["bd_flow_id"] is None


# ---------------------------------------------------------------------------
# Test 4: Tier-1 Gating & Tier-2 Rescoped Alignment
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tier1_gating_skips_tier2_for_none_activity(
    app_db: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """NONE-matched activity steps skip Tier-2 align and receive UNVERIFIABLE / TIER1_NONE_MATCH."""
    snap_id, clust_id, doc_id, flow_id, steps, bd_flows = await _setup_synthetic_activity_fixture(app_db, tmp_path)
    now = utc_now_iso()

    # Pre-insert 2 activities: Act 1 (steps 1..4), Act 2 (steps 5..8)
    act1_id = f"act:{new_id()}"
    act2_id = f"act:{new_id()}"

    await app_db.execute(
        "INSERT INTO user_activities (id, flow_id, ordinal, name_en, name_ja, summary_en, member_step_ids_json, sheet_span_json, step_count, action_count, error_rule_count, expectation_count, origin, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (act1_id, flow_id, 1, "Coil Processing", "コイル処理", "Processes coil", json.dumps([s["id"] for s in steps[:4]]), "[\"想定表\"]", 4, 2, 0, 2, "llm", now),
    )
    await app_db.execute(
        "INSERT INTO user_activities (id, flow_id, ordinal, name_en, name_ja, summary_en, member_step_ids_json, sheet_span_json, step_count, action_count, error_rule_count, expectation_count, origin, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (act2_id, flow_id, 2, "Custom Unmatched", "未一致", "Unmatched feature", json.dumps([s["id"] for s in steps[4:]]), "[\"ホスト_エラー1\"]", 4, 2, 0, 2, "llm", now),
    )
    await app_db.commit()

    tier1_response = {
        "matches": [
            {"activity_alias": "a1", "bd_flow_alias": "bf1", "match_status": "FULLY", "confidence": 0.9, "reason": "Matches coil flow"},
            {"activity_alias": "a2", "bd_flow_alias": None, "match_status": "NONE", "confidence": 0.9, "reason": "No BD match"},
        ]
    }

    tier2_align_response = {
        "steps": [
            {
                "step_alias": "u1",
                "claims": [
                    {
                        "claim_id": "c1",
                        "citation": {"snippet_id": "src1", "line_start": 1, "line_end": 5},
                        "reason": "Found code",
                    }
                ],
                "bd_mappings": [{"target_alias": "bd1", "relation": "realizes", "confidence": 0.9}],
            }
        ]
    }

    tier2_verdict_response = {
        "verdicts": [
            {
                "step_alias": "u1",
                "verdict": "COVERED",
                "divergence": None,
                "reason": "Confirmed in code and spec",
                "corrected": False,
                "kept_bd_ids": ["bd1"],
                "citations": [{"rel_path": "TESTACT.cbl", "line_start": 1, "line_end": 5}],
            }
        ]
    }

    call_count = {"tier1": 0, "align": 0, "verdict": 0}

    async def _mock_chat(self, req: ChatRequest, model_id: str | None = None):
        user_msg = req.messages[1].content if len(req.messages) > 1 else ""
        if "BD FLOWS CATALOG" in user_msg:
            call_count["tier1"] += 1
            yield {"type": "content", "text": json.dumps(tier1_response)}
        elif "STEP TO EVALUATE" in user_msg or "u1" in user_msg:
            call_count["align"] += 1
            yield {"type": "content", "text": json.dumps(tier2_align_response)}
        elif "UPSTREAM PROPOSALS" in user_msg or "anchor_result" in user_msg:
            call_count["verdict"] += 1
            yield {"type": "content", "text": json.dumps(tier2_verdict_response)}
        else:
            yield {"type": "content", "text": json.dumps({})}

    monkeypatch.setattr(ProviderConfigService, "chat_stream_events", _mock_chat)

    res: UserFlowRunResult = await run_user_flow_alignment(
        db=app_db,
        doc_id=doc_id,
        cluster_id=clust_id,
        snapshot_id=snap_id,
        provider_id="prov:test",
    )

    # 4 steps in Act 1 evaluated; 4 steps in Act 2 skipped!
    assert res.tier2_steps_run == 4
    assert res.tier2_steps_skipped == 4
    assert res.matched_fully == 1
    assert res.matched_none == 1

    # Check database verdicts for skipped steps
    async with app_db.execute(
        "SELECT ref_id, verdict, reason FROM user_verdicts WHERE doc_id = ? AND ref_kind = 'step'",
        (doc_id,),
    ) as cur:
        v_rows = {r[0]: (r[1], r[2]) for r in await cur.fetchall()}

    for s in steps[4:]:
        v, rsn = v_rows[s["id"]]
        assert v == "UNVERIFIABLE"
        assert "TIER1_NONE_MATCH" in rsn

    # Regression guard: BD-side verdicts must enumerate the FULL catalog even when tier-2 is
    # scoped to matched flows — the unmatched flow bf2 must still surface as BD_EXTRA.
    bf2_id = bd_flows[1]["id"]
    async with app_db.execute(
        "SELECT verdict FROM user_verdicts WHERE doc_id = ? AND side = 'bd' AND ref_id = ?",
        (doc_id, bf2_id),
    ) as cur:
        bd2_row = await cur.fetchone()
    assert bd2_row is not None, "unmatched BD flow missing from BD-side verdicts (scoped catalog leaked into BD_EXTRA enumeration)"
    assert bd2_row[0] == "BD_EXTRA"


# ---------------------------------------------------------------------------
# Test 5: Activity Rollup, Report JSON, and Graph Adapter
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_activity_rollup_report_and_graph(
    app_db: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Verifies activity rollup persistence, report JSON nesting, and graph adapter activity nodes."""
    snap_id, clust_id, doc_id, flow_id, steps, bd_flows = await _setup_synthetic_activity_fixture(app_db, tmp_path)
    now = utc_now_iso()
    run_id = f"run:{new_id()}"

    act1_id = f"act:{new_id()}"
    await app_db.execute(
        "INSERT INTO user_activities (id, flow_id, ordinal, name_en, name_ja, summary_en, member_step_ids_json, sheet_span_json, step_count, action_count, error_rule_count, expectation_count, origin, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (act1_id, flow_id, 1, "Coil Activity 1", "コイル1", "Coil Summary", json.dumps([s["id"] for s in steps[:4]]), "[\"想定表\"]", 4, 2, 0, 2, "llm", now),
    )
    await app_db.execute(
        "INSERT INTO user_activity_matches (id, run_id, activity_id, bd_flow_id, match_status, confidence, reason, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (f"m:{new_id()}", run_id, act1_id, bd_flows[0]["id"], "FULLY", 0.95, "Matches", now),
    )

    # Insert COVERED verdicts for member steps
    for s in steps[:4]:
        await app_db.execute(
            "INSERT INTO user_verdicts (id, run_id, doc_id, cluster_id, snapshot_id, side, ref_id, ref_kind, verdict, divergence, reason, evidence_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f"uv:{new_id()}", run_id, doc_id, clust_id, snap_id, "user", s["id"], "step", "COVERED", None, "Covered", "{}", now),
        )
    # Publication contract (UP3-1): only a run whose completion marker names this
    # (doc, cluster, snapshot) is reportable.
    await app_db.execute(
        "INSERT INTO user_run_artifacts (id, run_id, doc_id, ref_id, payload, created_at) "
        "VALUES (?, ?, ?, '__run_complete__', ?, ?)",
        (f"ura:{new_id()}", run_id, doc_id,
         json.dumps({"doc_id": doc_id, "run_id": run_id, "cluster_id": clust_id, "snapshot_id": snap_id}), now),
    )
    await app_db.commit()

    # 1. Test Report Query
    rep = await get_user_flow_report(app_db, doc_id, clust_id, snap_id)
    assert rep["summary"]["total_activities"] == 1
    assert "activity_counts" in rep["summary"]
    assert len(rep["flows"]) == 1
    flow_rep = rep["flows"][0]
    assert "activities" in flow_rep
    assert len(flow_rep["activities"]) == 1
    assert flow_rep["activities"][0]["name_en"] == "Coil Activity 1"
    assert flow_rep["activities"][0]["match_status"] == "FULLY"

    # 2. Test Graph Query
    graph_res = await get_user_flow_graph(app_db, doc_id)
    assert "business_flows" in graph_res
    g_flow = graph_res["business_flows"][0]
    # Default Level is Activity Nodes
    assert len(g_flow["steps"]) == 1
    assert g_flow["steps"][0]["id"] == f"act:{act1_id}"
    assert g_flow["steps"][0]["kind"] == "activity"
    assert "leaf_steps" in g_flow
    assert len(g_flow["leaf_steps"]) == 8


# ---------------------------------------------------------------------------
# TICKET U-P2-FIX Test 1 (FIX 2): tier-2 BD catalog scoped to the matched flow
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tier2_bd_catalog_scoped_to_matched_flow(
    app_db: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """FIX 2: with two activities FULLY-matched to two different BD flows, each step's BD
    catalog shown to the align stage must contain ONLY units of ITS OWN matched flow, not the
    other fixture flow's units (the miss that let the tier-2 rescope wiring slip through)."""
    snap_id, clust_id, doc_id, flow_id, steps, bd_flows = await _setup_synthetic_activity_fixture(app_db, tmp_path)
    now = utc_now_iso()

    act1_id = f"act:{new_id()}"
    act2_id = f"act:{new_id()}"
    await app_db.execute(
        "INSERT INTO user_activities (id, flow_id, ordinal, name_en, name_ja, summary_en, member_step_ids_json, sheet_span_json, step_count, action_count, error_rule_count, expectation_count, origin, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (act1_id, flow_id, 1, "Coil Processing", "コイル処理", "Processes coil", json.dumps([s["id"] for s in steps[:4]]), "[\"想定表\"]", 4, 2, 0, 2, "llm", now),
    )
    await app_db.execute(
        "INSERT INTO user_activities (id, flow_id, ordinal, name_en, name_ja, summary_en, member_step_ids_json, sheet_span_json, step_count, action_count, error_rule_count, expectation_count, origin, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (act2_id, flow_id, 2, "Host Error Handling", "エラー処理", "Handles host errors", json.dumps([s["id"] for s in steps[4:]]), "[\"ホスト_エラー1\"]", 4, 2, 0, 2, "llm", now),
    )
    await app_db.commit()

    # Both activities FULLY-matched, each to a DIFFERENT BD flow, so tier-2 runs for all 8 steps.
    tier1_response = {
        "matches": [
            {"activity_alias": "a1", "bd_flow_alias": "bf1", "match_status": "FULLY", "confidence": 0.9, "reason": "Matches coil flow"},
            {"activity_alias": "a2", "bd_flow_alias": "bf2", "match_status": "FULLY", "confidence": 0.9, "reason": "Matches error flow"},
        ]
    }

    captured_align_payloads: list[dict[str, Any]] = []

    async def _mock_chat(self, req: ChatRequest, model_id: str | None = None):
        user_msg = req.messages[1].content if len(req.messages) > 1 else ""
        if "BD FLOWS CATALOG" in user_msg:
            yield {"type": "content", "text": json.dumps(tier1_response)}
        elif '"steps_to_align"' in user_msg:
            payload = json.loads(user_msg)
            captured_align_payloads.append(payload)
            results = [
                {"step_id": step["step_id"], "presentation": False, "claims": [], "bd_mappings": []}
                for step in payload["steps_to_align"]
            ]
            yield {"type": "content", "text": json.dumps({"results": results})}
        else:
            # Verdict stage (or anything else): no upstream mappings/claims to correct.
            yield {"type": "content", "text": json.dumps({"results": []})}

    monkeypatch.setattr(ProviderConfigService, "chat_stream_events", _mock_chat)

    await run_user_flow_alignment(
        db=app_db,
        doc_id=doc_id,
        cluster_id=clust_id,
        snapshot_id=snap_id,
        provider_id="prov:test",
    )

    assert captured_align_payloads, "align stage should have been invoked with tier-2 rescoped steps"

    bf1_names = {"Process Coil Lot", "Validate Lot"}
    bf2_names = {"Handle Host Errors"}

    seen_aliases: set[str] = set()
    for payload in captured_align_payloads:
        for step_entry in payload["steps_to_align"]:
            seen_aliases.add(step_entry["step_id"])
            shown_names = {c["name"] for c in step_entry["bd_candidates"]}
            # Every step_entry must show units of exactly one flow's catalog, never both.
            if shown_names & bf1_names:
                assert not (shown_names & bf2_names), (
                    f"step {step_entry['step_id']}: BF1-scoped step leaked BF2 units: {shown_names}"
                )
            elif shown_names & bf2_names:
                assert not (shown_names & bf1_names), (
                    f"step {step_entry['step_id']}: BF2-scoped step leaked BF1 units: {shown_names}"
                )

    assert len(seen_aliases) == 8


@pytest.mark.asyncio
async def test_tier1_total_failure_is_unresolved_end_to_end(
    app_db: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """UP3-2 A3 + UP3-3 C1: a failed tier-1 must never be reported as the finding 'NONE'.
    Rows persist as UNRESOLVED, skipped steps say TIER1_UNRESOLVED, and no BD unit is BD_EXTRA."""
    snap_id, clust_id, doc_id, flow_id, steps, bd_flows = await _setup_synthetic_activity_fixture(app_db, tmp_path)
    now = utc_now_iso()

    act1_id = f"act:{new_id()}"
    await app_db.execute(
        "INSERT INTO user_activities (id, flow_id, ordinal, name_en, name_ja, summary_en, member_step_ids_json, sheet_span_json, step_count, action_count, error_rule_count, expectation_count, origin, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (act1_id, flow_id, 1, "Coil Processing", "コイル処理", "Processes coil", json.dumps([s["id"] for s in steps]), "[\"想定表\"]", 8, 4, 0, 4, "llm", now),
    )
    await app_db.commit()

    async def _mock_chat(self, req: ChatRequest, model_id: str | None = None):
        user_msg = req.messages[1].content if len(req.messages) > 1 else ""
        if "BD FLOWS CATALOG" in user_msg:
            # Both ladder attempts return an unusable payload -> honest failure state.
            yield {"type": "content", "text": json.dumps({"matches": []})}
        else:
            yield {"type": "content", "text": json.dumps({"results": []})}

    monkeypatch.setattr(ProviderConfigService, "chat_stream_events", _mock_chat)

    res: UserFlowRunResult = await run_user_flow_alignment(
        db=app_db, doc_id=doc_id, cluster_id=clust_id, snapshot_id=snap_id, provider_id="prov:test",
    )

    async with app_db.execute(
        "SELECT match_status, reason FROM user_activity_matches WHERE run_id = ?", (res.run_id,)
    ) as cur:
        m_rows = await cur.fetchall()
    assert [r[0] for r in m_rows] == ["UNRESOLVED"]
    assert m_rows[0][1] == "LLM_NO_RESPONSE"

    assert res.tier2_steps_run == 0
    assert res.tier2_steps_skipped == 8
    # An unresolved activity is counted apart from a genuine NONE: it is not a finding.
    assert res.matched_unresolved == 1
    assert res.matched_none == 0

    async with app_db.execute(
        "SELECT reason FROM user_verdicts WHERE run_id = ? AND side = 'user' AND ref_kind = 'step'",
        (res.run_id,),
    ) as cur:
        reasons = [r[0] for r in await cur.fetchall()]
    assert len(reasons) == 8
    assert all("TIER1_UNRESOLVED" in r for r in reasons)
    assert not any("TIER1_NONE_MATCH" in r for r in reasons)

    # No BD unit may be called BD_EXTRA off the back of a failed tier-1.
    async with app_db.execute(
        "SELECT verdict, COUNT(*) FROM user_verdicts WHERE run_id = ? AND side = 'bd' GROUP BY verdict",
        (res.run_id,),
    ) as cur:
        bd_counts = {r[0]: r[1] for r in await cur.fetchall()}
    assert "BD_EXTRA" not in bd_counts
    assert bd_counts.get("UNRESOLVED", 0) > 0


@pytest.mark.asyncio
async def test_tier1_mixed_pair_statuses_drive_gate_and_scope(
    app_db: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """UP3-2 A6: an activity with FULLY + PARTIAL pairs is gated by its BEST status (runs tier-2)
    and its candidate scope is the UNION of both matched flows; a NONE activity is still skipped."""
    snap_id, clust_id, doc_id, flow_id, steps, bd_flows = await _setup_synthetic_activity_fixture(app_db, tmp_path)
    now = utc_now_iso()

    act1_id = f"act:{new_id()}"
    act2_id = f"act:{new_id()}"
    await app_db.execute(
        "INSERT INTO user_activities (id, flow_id, ordinal, name_en, name_ja, summary_en, member_step_ids_json, sheet_span_json, step_count, action_count, error_rule_count, expectation_count, origin, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (act1_id, flow_id, 1, "Coil Processing", "コイル処理", "Processes coil", json.dumps([s["id"] for s in steps[:4]]), "[\"想定表\"]", 4, 2, 0, 2, "llm", now),
    )
    await app_db.execute(
        "INSERT INTO user_activities (id, flow_id, ordinal, name_en, name_ja, summary_en, member_step_ids_json, sheet_span_json, step_count, action_count, error_rule_count, expectation_count, origin, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (act2_id, flow_id, 2, "Host Error Handling", "エラー処理", "Handles host errors", json.dumps([s["id"] for s in steps[4:]]), "[\"ホスト_エラー1\"]", 4, 2, 0, 2, "llm", now),
    )
    await app_db.commit()

    tier1_response = {
        "matches": [
            {"activity_alias": "a1", "bd_flow_alias": "bf2", "match_status": "PARTIAL", "confidence": 0.6, "reason": "Partial overlap"},
            {"activity_alias": "a1", "bd_flow_alias": "bf1", "match_status": "FULLY", "confidence": 0.9, "reason": "Matches coil flow"},
            {"activity_alias": "a2", "bd_flow_alias": None, "match_status": "NONE", "confidence": 0.9, "reason": "No BD match"},
        ]
    }

    captured_align_payloads: list[dict[str, Any]] = []

    async def _mock_chat(self, req: ChatRequest, model_id: str | None = None):
        user_msg = req.messages[1].content if len(req.messages) > 1 else ""
        if "BD FLOWS CATALOG" in user_msg:
            yield {"type": "content", "text": json.dumps(tier1_response)}
        elif '"steps_to_align"' in user_msg:
            payload = json.loads(user_msg)
            captured_align_payloads.append(payload)
            results = [
                {"step_id": step["step_id"], "presentation": False, "claims": [], "bd_mappings": []}
                for step in payload["steps_to_align"]
            ]
            yield {"type": "content", "text": json.dumps({"results": results})}
        else:
            yield {"type": "content", "text": json.dumps({"results": []})}

    monkeypatch.setattr(ProviderConfigService, "chat_stream_events", _mock_chat)

    res: UserFlowRunResult = await run_user_flow_alignment(
        db=app_db,
        doc_id=doc_id,
        cluster_id=clust_id,
        snapshot_id=snap_id,
        provider_id="prov:test",
    )

    # Best-status gate: act1 (FULLY over PARTIAL) runs, act2 (NONE) is skipped.
    assert res.tier2_steps_run == 4
    assert res.tier2_steps_skipped == 4
    # Run totals count each activity once by its best status.
    assert res.matched_fully == 1
    assert res.matched_partial == 0
    assert res.matched_none == 1

    # Both pairs persisted for act1 with their own statuses.
    async with app_db.execute(
        "SELECT bd_flow_id, match_status FROM user_activity_matches WHERE run_id = ? AND activity_id = ?",
        (res.run_id, act1_id),
    ) as cur:
        pair_rows = {r[0]: r[1] for r in await cur.fetchall()}
    assert pair_rows == {bd_flows[0]["id"]: "FULLY", bd_flows[1]["id"]: "PARTIAL"}

    # Scope is the union of BOTH positively matched flows.
    shown_names: set[str] = set()
    for payload in captured_align_payloads:
        for step_entry in payload["steps_to_align"]:
            shown_names.update(c["name"] for c in step_entry["bd_candidates"])
    assert "Process Coil Lot" in shown_names
    assert "Handle Host Errors" in shown_names


# ---------------------------------------------------------------------------
# TICKET U-P2-FIX Test 2 (FIX 3): ladder retry recovers from bad coverage
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_activity_condensation_retry_recovers_from_bad_coverage(
    app_db: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """FIX 3: attempt 1 has bad coverage (missing s8) -> _parse_and_validate now RAISES so the
    ladder retries; attempt 2 is valid -> the LLM result (not the deterministic fallback) is used."""
    snap_id, clust_id, doc_id, flow_id, steps, _ = await _setup_synthetic_activity_fixture(app_db, tmp_path)

    bad_payload = {
        "activities": [
            {
                "name_en": "Incomplete Activity",
                "summary_en": "Missing s8",
                "member_step_aliases": ["s1", "s2", "s3", "s4", "s5", "s6", "s7"],
                "ordinal": 1,
            }
        ]
    }
    good_payload = {
        "activities": [
            {
                "name_en": "Complete Activity",
                "summary_en": "All steps present",
                "member_step_aliases": [f"s{i}" for i in range(1, 9)],
                "ordinal": 1,
            }
        ]
    }

    call_count = {"n": 0}

    async def _mock_chat(self, req: ChatRequest, model_id: str | None = None):
        call_count["n"] += 1
        payload = bad_payload if call_count["n"] == 1 else good_payload
        yield {"type": "content", "text": json.dumps(payload)}

    monkeypatch.setattr(ProviderConfigService, "chat_stream_events", _mock_chat)

    flow_row = {"id": flow_id, "name_en": "Coil Flow", "sheet": "想定表"}
    activities, artifact = await condense_flow_activities(app_db, flow_row, steps, provider_id="prov:test")

    assert call_count["n"] == 2, "ladder should retry exactly once after the bad-coverage attempt"
    assert artifact["origin"] == "llm"
    assert len(activities) == 1
    assert activities[0]["name_en"] == "Complete Activity"
    member_ids = json.loads(activities[0]["member_step_ids_json"])
    assert set(member_ids) == {s["id"] for s in steps}


# ---------------------------------------------------------------------------
# TICKET U-P2-FIX Test 3 (FIX 1): ChatRequest shape (json_mode + max_completion_tokens)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_activity_stages_request_shape(
    app_db: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """FIX 1: both LLM#5 condensation and LLM#6 tier-1 matching must build a ChatRequest using
    REAL ChatRequest fields (json_mode, max_completion_tokens) instead of the silently-dropped
    response_format/max_tokens fields, otherwise JSON mode never reaches the wire and the token
    budget collapses to the pydantic default (2048)."""
    snap_id, clust_id, doc_id, flow_id, steps, bd_flows = await _setup_synthetic_activity_fixture(app_db, tmp_path)
    captured: list[ChatRequest] = []

    async def _mock_chat(self, req: ChatRequest, model_id: str | None = None):
        captured.append(req)
        user_msg = req.messages[1].content if len(req.messages) > 1 else ""
        if "STEP REGISTRY" in user_msg:
            yield {
                "type": "content",
                "text": json.dumps({
                    "activities": [
                        {
                            "name_en": "A",
                            "summary_en": "s",
                            "member_step_aliases": [f"s{i}" for i in range(1, 9)],
                            "ordinal": 1,
                        }
                    ]
                }),
            }
        else:
            yield {
                "type": "content",
                "text": json.dumps({
                    "matches": [
                        {"activity_alias": "a1", "bd_flow_alias": None, "match_status": "NONE", "confidence": 0.5, "reason": "r"}
                    ]
                }),
            }

    monkeypatch.setattr(ProviderConfigService, "chat_stream_events", _mock_chat)

    flow_row = {"id": flow_id, "name_en": "Coil Flow", "sheet": "想定表"}
    await condense_flow_activities(app_db, flow_row, steps, provider_id="prov:test")

    act = {
        "id": f"act:{new_id()}",
        "flow_id": flow_id,
        "ordinal": 1,
        "name_en": "A",
        "summary_en": "s",
        "member_step_ids_json": json.dumps([steps[0]["id"]]),
    }
    await match_user_activities_to_bd_flows(
        app_db, doc_id, clust_id, [act], steps, provider_id="prov:test", run_id=f"run:{new_id()}"
    )

    assert len(captured) == 2
    for req in captured:
        assert req.json_mode is True
        assert req.max_completion_tokens == 25000
        assert req.temperature == 0.0


# ---------------------------------------------------------------------------
# TICKET U-P2-FIX Test 4 (FIX 5): duplicate tier-1 alias rejected, no duplicate rows
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tier1_duplicate_alias_rejected(
    app_db: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """FIX 5: a reply listing the same activity_alias twice must be rejected (exact coverage),
    not silently accepted as a subset match. Both ladder attempts get the same duplicate reply,
    so it exhausts to the deterministic UNRESOLVED fallback with exactly one row (no duplicates)."""
    snap_id, clust_id, doc_id, flow_id, steps, bd_flows = await _setup_synthetic_activity_fixture(app_db, tmp_path)

    act1_id = f"act:{new_id()}"
    activities = [
        {
            "id": act1_id,
            "flow_id": flow_id,
            "ordinal": 1,
            "name_en": "Process Coil",
            "summary_en": "Process coil operations",
            "member_step_ids_json": json.dumps([steps[0]["id"], steps[1]["id"]]),
        },
    ]

    # a1 listed twice with duplicate (activity, flow) pair: rejected.
    duplicate_payload = {
        "matches": [
            {"activity_alias": "a1", "bd_flow_alias": "bf1", "match_status": "FULLY", "confidence": 0.9, "reason": "first"},
            {"activity_alias": "a1", "bd_flow_alias": "bf1", "match_status": "FULLY", "confidence": 0.9, "reason": "dup"},
        ]
    }

    async def _mock_chat(self, req: ChatRequest, model_id: str | None = None):
        yield {"type": "content", "text": json.dumps(duplicate_payload)}

    monkeypatch.setattr(ProviderConfigService, "chat_stream_events", _mock_chat)

    run_id = f"run:{new_id()}"
    matches, artifact = await match_user_activities_to_bd_flows(
        app_db, doc_id, clust_id, activities, steps, provider_id="prov:test", run_id=run_id
    )

    assert len(matches) == 1
    assert matches[0]["activity_id"] == act1_id
    assert matches[0]["match_status"] == "UNRESOLVED"
    assert matches[0]["reason"] == "LLM_NO_RESPONSE"

    async with app_db.execute(
        "SELECT COUNT(*) FROM user_activity_matches WHERE run_id = ?", (run_id,)
    ) as cur:
        row = await cur.fetchone()
    assert row[0] == 1
