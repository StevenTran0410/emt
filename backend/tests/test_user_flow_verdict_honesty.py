"""Unit tests for TICKET UP3-3: Verdict & Reporting Honesty (Codex R1.7, R1.8, R2, R5).

Tests:
1. Two admissible COVERED bases:
   (i) own_citation: valid code citation supports step.
   (ii) bd_verdict: kept BD mapping with stored MATCH/PARTIAL verdict, no code citation needed.
2. Citation-valid but kept_bd_ids empty -> NOT COVERED (becomes BD_MISSING or UNVERIFIABLE).
3. Hallucinated / unknown kept_bd_ids / basis_bd_id rejected at parse time (closed schema).
4. BD-side three-state: COVERED vs BD_UNMAPPED (matched parent flow) vs BD_EXTRA (unmatched parent flow).
5. Branch coverage rollup: non-SUCCESS branches in matched flows with 0 incoming steps flagged as is_user_flow_gap.
6. CONTRADICTED and BD_MISSING without valid citations fuse to UNVERIFIABLE.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
import pytest
import aiosqlite

from domain.user_flow._verdict import (
    LLMCitation,
    LLMVerdictBatchResponse,
    LLMVerdictItem,
    StepVerdictContext,
    evaluate_verdict_batch,
    run_user_flow_verdicts,
)
from domain.user_flow._anchor import Snippet
from domain.user_flow._mapping import BDContext, BDUnit
from domain.user_flow._queries import get_user_flow_report
from infrastructure.db.database.migrations import _MIGRATIONS


@pytest.fixture
async def mem_db():
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    for m in _MIGRATIONS:
        await db.executescript(m["sql"])
    await db.commit()
    yield db
    await db.close()


def test_verdict_prompt_states_the_two_bases_and_bd_missing_rule():
    """The dead-Rule-4 defect was a PROMPT defect: the model never proposed citation-free COVERED."""
    from domain.user_flow._verdict import _VERDICT_SYSTEM_PROMPT as p

    assert "You may ADOPT valid upstream anchor citations" in p
    assert "(i) `own_citation`" in p
    assert "(ii) `bd_verdict`" in p
    assert "no code citation is required" in p
    assert "NO candidate BD unit fits, BD_MISSING is the expected answer (NOT UNVERIFIABLE)" in p
    assert "bd_candidates" in p  # the candidate registry, not just the mapper's proposals
    assert "basis_bd_id" in p
    assert "Output schema" in p


def _login_step_ctx() -> StepVerdictContext:
    return StepVerdictContext(
        user_step_id="step_1",
        step_alias="u1",
        flow_id="f1",
        ordinal=1,
        kind="action",
        section_id="sec1",
        text_ja="ログインボタンをクリックする",
        text_en="Click login button",
        trigger_ja=None,
        expected_ja=None,
        screen_name_ja=None,
        in_scope=True,
        scope_note=None,
        presentation=False,
        snippets=[],
        snippet_id_map={},
        anchor_proposals=[],
        bd_proposals=[],
        bd_candidates=[{"bd_id": "b_step_1", "bd_alias": "bd1", "name": "Login Step"}],
        bd_alias_map={"bd1": {"bd_id": "b_step_1"}},
        bd_id_to_alias={"b_step_1": "bd1"},
    )


@pytest.mark.asyncio
async def test_registry_alias_accepted_and_remapped(monkeypatch):
    """A kept_bd_id from the shown registry survives and is remapped to the real BD unit id."""
    ctx = _login_step_ctx()

    async def _mock_stream(req):
        yield {"type": "content", "text": json.dumps({"results": [
            {"unit_id": "u1", "verdict": "COVERED", "kept_bd_ids": ["bd1"], "basis_bd_id": "bd1",
             "citations": [], "reason": "OK"}
        ]})}

    monkeypatch.setattr(
        "domain.model_connector.service.ProviderConfigService.chat_stream_events",
        lambda self, req: _mock_stream(req),
    )

    results, raw, retries, _lat = await evaluate_verdict_batch([ctx], provider_id="mock_prov")
    assert results["step_1"].kept_bd_ids == ["b_step_1"]
    assert results["step_1"].basis_bd_id == "b_step_1"
    assert retries == 0


@pytest.mark.asyncio
async def test_hallucinated_kept_bd_id_is_rejected_and_retried(monkeypatch):
    """An id outside the shown registry raises inside the parse validator, so the ladder retries
    and (both attempts bad) the batch yields nothing — a hallucinated mapping is never persisted."""
    ctx = _login_step_ctx()
    attempts = {"n": 0}

    async def _mock_stream(req):
        attempts["n"] += 1
        yield {"type": "content", "text": json.dumps({"results": [
            {"unit_id": "u1", "verdict": "COVERED", "kept_bd_ids": ["bd999"], "citations": [],
             "reason": "Hallucinated"}
        ]})}

    monkeypatch.setattr(
        "domain.model_connector.service.ProviderConfigService.chat_stream_events",
        lambda self, req: _mock_stream(req),
    )

    results, raw, retries, _lat = await evaluate_verdict_batch([ctx], provider_id="mock_prov")
    assert results == {}
    assert attempts["n"] == 2, "the closed-registry ValueError must drive the reasoning ladder retry"

    # Same for a hallucinated basis_bd_id.
    attempts["n"] = 0

    async def _mock_stream_basis(req):
        attempts["n"] += 1
        yield {"type": "content", "text": json.dumps({"results": [
            {"unit_id": "u1", "verdict": "COVERED", "kept_bd_ids": ["bd1"], "basis_bd_id": "bd42",
             "citations": [], "reason": "Hallucinated basis"}
        ]})}

    monkeypatch.setattr(
        "domain.model_connector.service.ProviderConfigService.chat_stream_events",
        lambda self, req: _mock_stream_basis(req),
    )
    results2, _raw2, _retries2, _lat2 = await evaluate_verdict_batch([ctx], provider_id="mock_prov")
    assert results2 == {}
    assert attempts["n"] == 2


@pytest.mark.asyncio
async def test_verdict_fusion_bases_and_bd_three_state(mem_db: aiosqlite.Connection, monkeypatch):
    """Test full verdict pipeline with basis (ii) BD verdict, citation+empty kept -> BD_MISSING, and BD 3-state."""
    now = "2026-08-18T10:00:00Z"
    doc_id = "doc_test_verdict"
    cluster_id = "cluster_test"
    snapshot_id = "snap_test"
    run_id = "run_test_v1"

    # Setup DB state
    await mem_db.execute(
        "INSERT INTO user_flow_docs (id, source_name, file_hash, imported_at) VALUES (?, ?, ?, ?)",
        (doc_id, "test.xlsx", "hash1", now),
    )
    await mem_db.execute(
        "INSERT INTO user_flows (id, doc_id, ordinal, name_ja, name_en, kind, sheet, scope_note, created_at) VALUES (?, ?, 1, 'ログイン', 'Login Flow', 'flow', 'Sheet1', '', ?)",
        ("uf1", doc_id, now),
    )
    await mem_db.execute(
        "INSERT INTO user_activities (id, flow_id, ordinal, name_ja, name_en, summary_en, member_step_ids_json, sheet_span_json, step_count, origin, created_at) "
        "VALUES ('act1', 'uf1', 1, '認証', 'Authentication', 'Perform user auth', '[\"s1\", \"s2\", \"s3\"]', '[]', 3, 'llm', ?)",
        (now,),
    )

    # 3 Steps:
    # s1: Has valid citation + kept BD mapping -> COVERED via basis (i) own_citation
    # s2: NO code citation, but kept BD unit whose BD<->code verdict is MATCH -> COVERED via basis (ii) bd_verdict
    # s3: Valid citation, but kept_bd_ids is empty -> Converted to BD_MISSING
    await mem_db.execute(
        "INSERT INTO user_steps (id, flow_id, ordinal, kind, text_ja, text_en, in_scope, sheet, row_start, row_end, created_at) VALUES "
        "('s1', 'uf1', 1, 'action', 'ID入力', 'Enter ID', 1, 'Sheet1', 1, 2, ?), "
        "('s2', 'uf1', 2, 'action', 'PW入力', 'Enter Password', 1, 'Sheet1', 3, 4, ?), "
        "('s3', 'uf1', 3, 'action', '追加オプション', 'Extra Option', 1, 'Sheet1', 5, 6, ?)",
        (now, now, now),
    )

    # Mock resolve_citation and retrieve_step_snippets
    from domain.business_flow_integrity._citation import ResolvedCitation

    async def mock_resolve_citation(db, snapshot_id, rel_path, line_start, line_end):
        if rel_path == "AUTH.cbl" and 1 <= line_start <= line_end <= 10:
            return ResolvedCitation(
                valid=True,
                rel_path=rel_path,
                line_start=line_start,
                line_end=line_end,
                fetched_text=f"PERFORM LINE {line_start}",
                source_sha256="sha_auth",
                reject_reason=None,
            )
        return ResolvedCitation(
            valid=False,
            rel_path=rel_path,
            line_start=line_start,
            line_end=line_end,
            fetched_text=None,
            source_sha256=None,
            reject_reason="NOT_IN_MANIFEST",
        )

    async def mock_retrieve_step_snippets(db, snapshot_id, s, s_hits, screen_files, local_p):
        return [
            Snippet(
                rel_path="AUTH.cbl",
                line_start=1,
                line_end=10,
                text="000100 PERFORM CHECK-ID.\n000200 PERFORM CHECK-PW.\n000400 PERFORM EXTRA-OPT.\n",
                source_sha256="sha_auth",
                occurrence_id=None,
                parse_status="ok",
            )
        ]

    monkeypatch.setattr("domain.user_flow._verdict.resolve_citation", mock_resolve_citation)
    monkeypatch.setattr("domain.user_flow._verdict.retrieve_step_snippets", mock_retrieve_step_snippets)

    # BD Context data:
    # Flow 1 (bf1): Matched in Tier-1
    #   - Unit bd_step_1: mapped by s1 (stored verdict MATCH)
    #   - Unit bd_step_2: mapped by s2 (stored verdict MATCH)
    #   - Unit bd_step_3: NOT mapped by any step -> should become BD_UNMAPPED
    #   - Unit bd_branch_1: non-SUCCESS branch in bf1, 0 incoming -> should be user_flow_gap in branch_coverage
    # Flow 2 (bf2): NOT matched in Tier-1
    #   - Unit bd_step_4: in unmatched flow -> should become BD_EXTRA
    await mem_db.execute(
        "INSERT INTO bd_business_flows (id, cluster_id, doc_id, sub_ix, block_key, name, description, ordinal, origin, created_at) VALUES "
        "('bf1', ?, 'doc1', 1, 'blk1', 'BD Login Flow', 'BD Login Flow Desc', 1, 'declared', ?), "
        "('bf2', ?, 'doc1', 2, 'blk2', 'BD Admin Flow', 'BD Admin Flow Desc', 2, 'declared', ?)",
        (cluster_id, now, cluster_id, now),
    )
    await mem_db.execute(
        "INSERT INTO bd_business_steps (id, flow_id, name, functionality, ordinal, source_node_ids, created_at) VALUES "
        "('bd_step_1', 'bf1', 'Check ID', 'Validate user ID', 1, '[]', ?), "
        "('bd_step_2', 'bf1', 'Check PW', 'Validate password', 2, '[]', ?), "
        "('bd_step_3', 'bf1', 'Log Login', 'Write audit log', 3, '[]', ?), "
        "('bd_step_4', 'bf2', 'Admin Init', 'Initialize admin', 1, '[]', ?)",
        (now, now, now, now),
    )
    await mem_db.execute(
        "INSERT INTO bd_business_branches (id, flow_id, branch_kind, guard_description, source_step_id, target_step_id, source_edge_ids, created_at) VALUES "
        "('bd_branch_1', 'bf1', 'ERROR', 'ID invalid error branch', 'bd_step_1', 'bd_step_2', '[]', ?)",
        (now,),
    )
    for u_id, u_kind, v_name in [
        ("bd_step_1", "step", "MATCH"),
        ("bd_step_2", "step", "MATCH"),
        ("bd_step_3", "step", "MATCH"),
        ("bd_step_4", "step", "MATCH"),
        ("bd_branch_1", "branch", "MATCH"),
    ]:
        await mem_db.execute(
            "INSERT INTO business_unit_verdicts (id, cluster_id, snapshot_id, unit_id, unit_kind, mapping_status, mapping_method, route_segment_json, verdict, guard_verdict, ai_bucket, reason, evidence_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'mapped', 'llm', '[]', ?, 'MATCH', 'confirmed', 'Code confirms behavior', '[]', ?)",
            (f"buv:{u_id}", cluster_id, snapshot_id, u_id, u_kind, v_name, now),
        )

    # Tier-1 Match: act1 matched to bf1 (FULLY)
    await mem_db.execute(
        "INSERT INTO user_activity_matches (id, run_id, activity_id, bd_flow_id, match_status, confidence, reason, created_at) VALUES "
        "('am1', ?, 'act1', 'bf1', 'FULLY', 0.95, 'Login matches', ?)",
        (run_id, now),
    )

    # User BD Mappings:
    # s1 maps to bd_step_1
    # s2 maps to bd_step_2
    # s3 maps to nothing / unmapped
    await mem_db.execute(
        "INSERT INTO user_bd_mappings (id, run_id, user_step_id, bd_kind, bd_id, relation, confidence, reason, created_at) VALUES "
        "('m1', ?, 's1', 'step', 'bd_step_1', 'realizes', 0.9, 'ID check', ?), "
        "('m2', ?, 's2', 'step', 'bd_step_2', 'realizes', 0.9, 'PW check', ?)",
        (run_id, now, run_id, now),
    )

    # User Code Anchors:
    # s1 has valid anchor on line 2
    # s3 has valid anchor on line 4
    await mem_db.execute(
        "INSERT INTO user_code_anchors (id, run_id, snapshot_id, step_id, rel_path, line_start, line_end, kind, valid, reason, created_at) VALUES "
        "('a1', ?, ?, 's1', 'AUTH.cbl', 2, 2, 'llm_matched', 1, 'PERFORM CHECK-ID', ?), "
        "('a3', ?, ?, 's3', 'AUTH.cbl', 4, 4, 'llm_matched', 1, 'PERFORM EXTRA-OPT', ?)",
        (run_id, snapshot_id, now, run_id, snapshot_id, now),
    )

    # Mock call_with_reasoning_ladder to test evaluate_verdict_batch validation + alias remapping
    async def mock_ladder(build_req, parse_fn, first_effort="low", label="", provider_id=""):
        mock_json_resp = json.dumps({
            "results": [
                {
                    "unit_id": "u1",
                    "verdict": "COVERED",
                    "kept_bd_ids": ["bd2"],
                    "citations": [{"rel_path": "AUTH.cbl", "line_start": 2, "line_end": 2}],
                    "reason": "s1 confirmed in code and BD",
                },
                {
                    "unit_id": "u2",
                    "verdict": "COVERED",
                    "kept_bd_ids": ["bd3"],
                    "basis_bd_id": "bd3",
                    "citations": [],
                    "reason": "s2 confirmed via matching BD verdict",
                },
                {
                    "unit_id": "u3",
                    "verdict": "BD_MISSING",
                    "divergence": "behavioural",
                    "kept_bd_ids": [],
                    "citations": [{"rel_path": "AUTH.cbl", "line_start": 4, "line_end": 4}],
                    "reason": "s3 found in code but no candidate BD unit describes it",
                },
            ]
        })
        parsed = parse_fn(mock_json_resp)
        return parsed, mock_json_resp

    monkeypatch.setattr("domain.user_flow._verdict.call_with_reasoning_ladder", mock_ladder)

    # Run verdicts
    summary = await run_user_flow_verdicts(
        db=mem_db,
        doc_id=doc_id,
        cluster_id=cluster_id,
        snapshot_id=snapshot_id,
        provider_id="mock_provider",
        run_id=run_id,
    )

    assert summary.covered_steps == 2
    assert summary.bd_missing_steps == 1
    assert summary.step_verdicts["s1"] == "COVERED"
    assert summary.step_verdicts["s2"] == "COVERED"
    assert summary.step_verdicts["s3"] == "BD_MISSING"

    # Recorded basis: s1 by its own citation, s2 by the kept BD unit's MATCH verdict.
    async with mem_db.execute(
        "SELECT ref_id, evidence_json FROM user_verdicts WHERE run_id = ? AND side = 'user' AND ref_kind = 'step'",
        (run_id,),
    ) as cur:
        step_ev = {r[0]: json.loads(r[1]) for r in await cur.fetchall()}
    assert step_ev["s1"]["basis"] == "own_citation"
    assert step_ev["s1"]["basis_bd_id"] is None
    assert step_ev["s2"]["basis"] == "bd_verdict"
    assert step_ev["s2"]["basis_bd_id"] == "bd_step_2"
    assert step_ev["s2"]["kept_bd_ids"] == ["bd_step_2"]
    assert step_ev["s3"]["basis"] == "own_citation"

    # Verify BD-side 3-state verdicts
    async with mem_db.execute(
        "SELECT ref_id, verdict, divergence, reason FROM user_verdicts WHERE run_id = ? AND side = 'bd' ORDER BY ref_id",
        (run_id,),
    ) as cur:
        bd_v_rows = {r[0]: dict(r) for r in await cur.fetchall()}

    assert bd_v_rows["bd_step_1"]["verdict"] == "COVERED"
    assert bd_v_rows["bd_step_2"]["verdict"] == "COVERED"
    assert bd_v_rows["bd_step_3"]["verdict"] == "BD_UNMAPPED"
    assert bd_v_rows["bd_step_4"]["verdict"] == "BD_EXTRA"
    assert bd_v_rows["bd_branch_1"]["verdict"] == "BD_UNMAPPED"
    assert bd_v_rows["bf1"]["verdict"] == "BD_UNMAPPED"
    assert bd_v_rows["bf2"]["verdict"] == "BD_EXTRA"

    await mem_db.execute(
        "INSERT INTO user_run_artifacts (id, run_id, doc_id, ref_id, payload, created_at) "
        "VALUES ('ura_done', ?, ?, '__run_complete__', ?, ?)",
        (run_id, doc_id,
         json.dumps({"doc_id": doc_id, "run_id": run_id, "cluster_id": cluster_id, "snapshot_id": snapshot_id}), now),
    )
    await mem_db.commit()

    # Verify Report API
    report = await get_user_flow_report(
        db=mem_db,
        doc_id=doc_id,
        cluster_id=cluster_id,
        snapshot_id=snapshot_id,
        run_id=run_id,
    )

    assert report["summary"]["bd_extra_count"] == 2  # bf2 + bd_step_4
    assert report["summary"]["bd_unmapped_count"] == 3  # bf1 + bd_step_3 + bd_branch_1
    assert len(report["bd_extra"]) == 2
    assert len(report["bd_unmapped"]) == 3

    # Verify Branch Coverage Rollup in report
    branch_cov = report["branch_coverage"]
    assert len(branch_cov) == 1
    assert branch_cov[0]["branch_id"] == "bd_branch_1"
    assert branch_cov[0]["incoming_step_count"] == 0
    assert branch_cov[0]["is_user_flow_gap"] is True

    # Verify step kept_bd_mappings contains ONLY verifier-kept mappings (no fallback)
    step_s3 = next(s for s in report["flows"][0]["steps"] if s["id"] == "s3")
    assert step_s3["verdict"] == "BD_MISSING"
    assert len(step_s3["kept_bd_mappings"]) == 0

    step_s1 = next(s for s in report["flows"][0]["steps"] if s["id"] == "s1")
    assert step_s1["verdict"] == "COVERED"
    assert len(step_s1["kept_bd_mappings"]) == 1
    assert step_s1["kept_bd_mappings"][0]["bd_id"] == "bd_step_1"


# ---------------------------------------------------------------------------
# Shared minimal world: 1 user flow + N steps, 1 BD flow with 1 step + 1 ERROR branch
# ---------------------------------------------------------------------------

async def _seed_minimal_world(
    db: aiosqlite.Connection,
    *,
    doc_id: str,
    cluster_id: str,
    snapshot_id: str,
    run_id: str,
    step_ids: list[str],
    bd_step_verdict: str = "MATCH",
    bd_branch_verdict: str = "MATCH",
    tier1_status: str = "FULLY",
) -> None:
    now = "2026-08-18T10:00:00Z"
    await db.execute("INSERT INTO user_flow_docs (id, source_name, file_hash, imported_at) VALUES (?, 'w.xlsx', ?, ?)", (doc_id, doc_id, now))
    await db.execute("INSERT INTO user_flows (id, doc_id, ordinal, name_ja, name_en, kind, sheet, scope_note, created_at) VALUES ('uf1', ?, 1, 'F', 'Flow', 'flow', 'S', '', ?)", (doc_id, now))
    await db.execute(
        "INSERT INTO user_activities (id, flow_id, ordinal, name_ja, name_en, summary_en, member_step_ids_json, sheet_span_json, step_count, origin, created_at) "
        "VALUES ('act1', 'uf1', 1, 'A', 'Activity', 'sum', ?, '[]', ?, 'llm', ?)",
        (json.dumps(step_ids), len(step_ids), now),
    )
    for i, sid in enumerate(step_ids, start=1):
        await db.execute(
            "INSERT INTO user_steps (id, flow_id, ordinal, kind, text_ja, text_en, in_scope, sheet, row_start, row_end, created_at) "
            "VALUES (?, 'uf1', ?, 'action', ?, ?, 1, 'S', ?, ?, ?)",
            (sid, i, f"手順{i}", f"Step {i}", i, i, now),
        )

    await db.execute(
        "INSERT INTO bd_business_flows (id, cluster_id, doc_id, sub_ix, block_key, name, description, ordinal, origin, created_at) "
        "VALUES ('bf1', ?, 'doc1', 1, 'blk1', 'BD Flow', 'Desc', 1, 'declared', ?)",
        (cluster_id, now),
    )
    await db.execute(
        "INSERT INTO bd_business_steps (id, flow_id, name, functionality, ordinal, source_node_ids, created_at) "
        "VALUES ('bd_step_1', 'bf1', 'Check ID', 'Validate user ID', 1, '[]', ?)",
        (now,),
    )
    await db.execute(
        "INSERT INTO bd_business_branches (id, flow_id, branch_kind, guard_description, source_step_id, source_edge_ids, created_at) "
        "VALUES ('bd_branch_1', 'bf1', 'ERROR', 'ID invalid error branch', 'bd_step_1', '[]', ?)",
        (now,),
    )
    for u_id, u_kind, v in [("bd_step_1", "step", bd_step_verdict), ("bd_branch_1", "branch", bd_branch_verdict)]:
        await db.execute(
            "INSERT INTO business_unit_verdicts (id, cluster_id, snapshot_id, unit_id, unit_kind, mapping_status, mapping_method, route_segment_json, verdict, guard_verdict, ai_bucket, reason, evidence_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'mapped', 'llm', '[]', ?, 'MATCH', 'confirmed', 'reason', '[]', ?)",
            (f"buv:{u_id}", cluster_id, snapshot_id, u_id, u_kind, v, now),
        )

    await db.execute(
        "INSERT INTO user_activity_matches (id, run_id, activity_id, bd_flow_id, match_status, confidence, reason, created_at) "
        "VALUES ('am1', ?, 'act1', ?, ?, 0.9, 'r', ?)",
        (run_id, "bf1" if tier1_status in ("FULLY", "PARTIAL") else None, tier1_status, now),
    )
    await db.execute(
        "INSERT INTO user_run_artifacts (id, run_id, doc_id, ref_id, payload, created_at) VALUES ('ura1', ?, ?, '__run_complete__', ?, ?)",
        (run_id, doc_id,
         json.dumps({"doc_id": doc_id, "run_id": run_id, "cluster_id": cluster_id, "snapshot_id": snapshot_id}), now),
    )
    await db.commit()


def _ladder_returning(results: list[dict[str, Any]]):
    async def _mock_ladder(build_req, parse_fn, first_effort="low", label="", provider_id=""):
        raw = json.dumps({"results": results})
        return parse_fn(raw), raw
    return _mock_ladder


@pytest.mark.asyncio
async def test_bd_verdict_basis_rejected_when_stored_verdict_is_not_match(mem_db, monkeypatch):
    """Basis (ii) is a deterministic gate check: a kept unit whose stored verdict is BROKEN/UNKNOWN
    cannot carry COVERED without an own citation."""
    doc_id, cluster_id, snapshot_id, run_id = "doc_neg", "cl_neg", "snap_neg", "run_neg"
    await _seed_minimal_world(
        mem_db, doc_id=doc_id, cluster_id=cluster_id, snapshot_id=snapshot_id,
        run_id=run_id, step_ids=["s1"], bd_step_verdict="BROKEN",
    )
    await mem_db.execute(
        "INSERT INTO user_bd_mappings (id, run_id, user_step_id, bd_kind, bd_id, relation, confidence, reason, created_at) "
        "VALUES ('m1', ?, 's1', 'step', 'bd_step_1', 'realizes', 0.9, 'r', '2026-08-18T10:00:00Z')",
        (run_id,),
    )
    await mem_db.commit()

    monkeypatch.setattr(
        "domain.user_flow._verdict.call_with_reasoning_ladder",
        _ladder_returning([
            {"unit_id": "u1", "verdict": "COVERED", "kept_bd_ids": ["bd2"], "basis_bd_id": "bd2",
             "citations": [], "reason": "claims BD covers it"}
        ]),
    )

    summary = await run_user_flow_verdicts(
        db=mem_db, doc_id=doc_id, cluster_id=cluster_id, snapshot_id=snapshot_id,
        provider_id="mock_provider", run_id=run_id,
    )

    assert summary.step_verdicts["s1"] == "UNVERIFIABLE"
    assert summary.covered_steps == 0
    async with mem_db.execute(
        "SELECT reason, evidence_json FROM user_verdicts WHERE run_id = ? AND ref_id = 's1' AND ref_kind = 'step'",
        (run_id,),
    ) as cur:
        row = await cur.fetchone()
    assert "[BASIS_BD_VERDICT_BROKEN]" in row[0]
    assert json.loads(row[1])["basis"] is None

    # The BD unit must NOT be reported as covered by a rejected basis.
    async with mem_db.execute(
        "SELECT verdict FROM user_verdicts WHERE run_id = ? AND side = 'bd' AND ref_id = 'bd_step_1'",
        (run_id,),
    ) as cur:
        assert (await cur.fetchone())[0] == "BD_UNMAPPED"


@pytest.mark.asyncio
async def test_citation_without_kept_bd_is_unverifiable_not_bd_missing(mem_db, monkeypatch):
    """A COVERED with empty kept_bd_ids is a malformed answer. Only the model's own explicit
    BD_MISSING may enter the BD_MISSING gate — the fusion must never manufacture the finding."""
    doc_id, cluster_id, snapshot_id, run_id = "doc_nokept", "cl_nk", "snap_nk", "run_nk"
    await _seed_minimal_world(
        mem_db, doc_id=doc_id, cluster_id=cluster_id, snapshot_id=snapshot_id,
        run_id=run_id, step_ids=["s1"],
    )

    from domain.business_flow_integrity._citation import ResolvedCitation

    async def _ok_citation(db, snap, rel_path, ls, le):
        return ResolvedCitation(valid=True, rel_path=rel_path, line_start=ls, line_end=le,
                                fetched_text="CODE", source_sha256="sha", reject_reason=None)

    async def _snips(db, snap, s, s_hits, screen_files, local_p):
        return [Snippet(rel_path="A.cbl", line_start=1, line_end=10, text="CODE",
                        source_sha256="sha", occurrence_id=None, parse_status="ok")]

    monkeypatch.setattr("domain.user_flow._verdict.resolve_citation", _ok_citation)
    monkeypatch.setattr("domain.user_flow._verdict.retrieve_step_snippets", _snips)
    monkeypatch.setattr(
        "domain.user_flow._verdict.call_with_reasoning_ladder",
        _ladder_returning([
            {"unit_id": "u1", "verdict": "COVERED", "kept_bd_ids": [],
             "citations": [{"rel_path": "A.cbl", "line_start": 2, "line_end": 2}],
             "reason": "code supports it"}
        ]),
    )

    summary = await run_user_flow_verdicts(
        db=mem_db, doc_id=doc_id, cluster_id=cluster_id, snapshot_id=snapshot_id,
        provider_id="mock_provider", run_id=run_id,
    )
    assert summary.step_verdicts["s1"] == "UNVERIFIABLE"
    assert summary.bd_missing_steps == 0

    async with mem_db.execute(
        "SELECT reason FROM user_verdicts WHERE run_id = ? AND side = 'user' AND ref_id = 's1'", (run_id,)
    ) as cur:
        assert "[NO_COVERAGE_BEARING_KEPT_BD]" in (await cur.fetchone())[0]


@pytest.mark.asyncio
async def test_related_mapping_cannot_carry_coverage(mem_db, monkeypatch):
    """`related` is non-coverage-bearing: keeping one may not make the step or the BD unit COVERED."""
    doc_id, cluster_id, snapshot_id, run_id = "doc_rel", "cl_rel", "snap_rel", "run_rel"
    await _seed_minimal_world(
        mem_db, doc_id=doc_id, cluster_id=cluster_id, snapshot_id=snapshot_id,
        run_id=run_id, step_ids=["s1"],
    )
    await mem_db.execute(
        "INSERT INTO user_bd_mappings (id, run_id, user_step_id, bd_kind, bd_id, relation, confidence, reason, created_at) "
        "VALUES ('m1', ?, 's1', 'step', 'bd_step_1', 'related', 0.9, 'same screen', '2026-08-18T10:00:00Z')",
        (run_id,),
    )
    await mem_db.commit()

    monkeypatch.setattr(
        "domain.user_flow._verdict.call_with_reasoning_ladder",
        _ladder_returning([
            {"unit_id": "u1", "verdict": "COVERED", "kept_bd_ids": ["bd2"], "basis_bd_id": "bd2",
             "citations": [], "reason": "related unit kept"}
        ]),
    )

    summary = await run_user_flow_verdicts(
        db=mem_db, doc_id=doc_id, cluster_id=cluster_id, snapshot_id=snapshot_id,
        provider_id="mock_provider", run_id=run_id,
    )
    assert summary.step_verdicts["s1"] == "UNVERIFIABLE"

    async with mem_db.execute(
        "SELECT verdict FROM user_verdicts WHERE run_id = ? AND side = 'bd' AND ref_id = 'bd_step_1'", (run_id,)
    ) as cur:
        assert (await cur.fetchone())[0] == "BD_UNMAPPED"


@pytest.mark.asyncio
async def test_verifier_added_kept_mapping_is_persisted_for_the_report(mem_db, monkeypatch):
    """A BD unit the mapper never proposed but the verifier keeps must reach user_bd_mappings, or
    the step would render zero kept mappings while the BD side claims COVERED."""
    doc_id, cluster_id, snapshot_id, run_id = "doc_add", "cl_add", "snap_add", "run_add"
    await _seed_minimal_world(
        mem_db, doc_id=doc_id, cluster_id=cluster_id, snapshot_id=snapshot_id,
        run_id=run_id, step_ids=["s1"],
    )  # no user_bd_mappings rows at all

    monkeypatch.setattr(
        "domain.user_flow._verdict.call_with_reasoning_ladder",
        _ladder_returning([
            {"unit_id": "u1", "verdict": "COVERED", "kept_bd_ids": ["bd2"], "basis_bd_id": "bd2",
             "citations": [], "reason": "mapper missed this unit"}
        ]),
    )

    summary = await run_user_flow_verdicts(
        db=mem_db, doc_id=doc_id, cluster_id=cluster_id, snapshot_id=snapshot_id,
        provider_id="mock_provider", run_id=run_id,
    )
    assert summary.step_verdicts["s1"] == "COVERED"

    async with mem_db.execute(
        "SELECT id, bd_id, relation, confidence, reason FROM user_bd_mappings WHERE run_id = ?", (run_id,)
    ) as cur:
        rows = [tuple(r) for r in await cur.fetchall()]
    assert len(rows) == 1
    assert rows[0][0].startswith("ubmv:")
    assert rows[0][1] == "bd_step_1"
    assert rows[0][2] == "realizes"
    assert rows[0][3] is None, "a synthesized mapping must not invent a confidence score"
    assert rows[0][4].startswith("[verifier-added]")

    report = await get_user_flow_report(db=mem_db, doc_id=doc_id, cluster_id=cluster_id, snapshot_id=snapshot_id)
    step = report["flows"][0]["steps"][0]
    assert step["verdict"] == "COVERED"
    assert [m["bd_id"] for m in step["kept_bd_mappings"]] == ["bd_step_1"]

    # Re-judging the same run replaces the synthesized row instead of duplicating it.
    await run_user_flow_verdicts(
        db=mem_db, doc_id=doc_id, cluster_id=cluster_id, snapshot_id=snapshot_id,
        provider_id="mock_provider", run_id=run_id,
    )
    async with mem_db.execute("SELECT COUNT(*) FROM user_bd_mappings WHERE run_id = ?", (run_id,)) as cur:
        assert (await cur.fetchone())[0] == 1


@pytest.mark.asyncio
async def test_guard_class_flag_reaches_the_report(mem_db, monkeypatch):
    """A kept-but-uncorroborated member_of must stay visible: the gate cannot reject it, so the
    report has to carry the review flag next to the mapping."""
    doc_id, cluster_id, snapshot_id, run_id = "doc_gc", "cl_gc", "snap_gc", "run_gc"
    await _seed_minimal_world(
        mem_db, doc_id=doc_id, cluster_id=cluster_id, snapshot_id=snapshot_id,
        run_id=run_id, step_ids=["s1"],
    )
    await mem_db.execute(
        "INSERT INTO user_bd_mappings (id, run_id, user_step_id, bd_kind, bd_id, relation, confidence, reason, created_at) "
        "VALUES ('m1', ?, 's1', 'branch', 'bd_branch_1', 'member_of', 0.9, 'confident spray', '2026-08-18T10:00:00Z')",
        (run_id,),
    )
    await mem_db.execute(
        "INSERT INTO user_run_artifacts (id, run_id, doc_id, ref_id, payload, created_at) VALUES ('ali_gc', ?, ?, 'align:s1', ?, '2026-08-18T10:00:00Z')",
        (run_id, doc_id, json.dumps({
            "user_step_id": "s1",
            "mappings": [{"bd_id": "bd_branch_1", "relation": "member_of", "guard_class_flag": "UNCORROBORATED_GUARD_CLASS"}],
        })),
    )
    await mem_db.commit()

    monkeypatch.setattr(
        "domain.user_flow._verdict.call_with_reasoning_ladder",
        _ladder_returning([
            {"unit_id": "u1", "verdict": "COVERED", "kept_bd_ids": ["bd3"], "basis_bd_id": "bd3",
             "citations": [], "reason": "kept"}
        ]),
    )
    await run_user_flow_verdicts(
        db=mem_db, doc_id=doc_id, cluster_id=cluster_id, snapshot_id=snapshot_id,
        provider_id="mock_provider", run_id=run_id,
    )

    report = await get_user_flow_report(db=mem_db, doc_id=doc_id, cluster_id=cluster_id, snapshot_id=snapshot_id)
    kept = report["flows"][0]["steps"][0]["kept_bd_mappings"]
    assert len(kept) == 1
    assert kept[0]["bd_id"] == "bd_branch_1"
    assert kept[0]["guard_class_flag"] == "UNCORROBORATED_GUARD_CLASS"
    assert kept[0]["coverage_bearing"] is True


@pytest.mark.asyncio
async def test_verifier_reuses_the_mappers_persisted_registry(mem_db, monkeypatch):
    """The verifier must judge the SAME catalog under the SAME aliases the mapper saw — otherwise
    'no candidate fits' is a judgment about a different list."""
    doc_id, cluster_id, snapshot_id, run_id = "doc_reg", "cl_reg", "snap_reg", "run_reg"
    await _seed_minimal_world(
        mem_db, doc_id=doc_id, cluster_id=cluster_id, snapshot_id=snapshot_id,
        run_id=run_id, step_ids=["s1"],
    )
    # The mapper recorded a DELIBERATELY narrow, reordered registry: branch first, step second.
    await mem_db.execute(
        "INSERT INTO user_run_artifacts (id, run_id, doc_id, ref_id, payload, created_at) VALUES ('ali1', ?, ?, ?, ?, ?)",
        (run_id, doc_id, "align:s1", json.dumps({
            "user_step_id": "s1",
            "presentation": False,
            "bd_registry": [
                {"bd_alias": "bd1", "bd_id": "bd_branch_1", "bd_kind": "branch",
                 "name": "ERROR branch of BD Flow: Check ID → End", "flow_name": "BD Flow",
                 "functionality": "ID invalid error branch"},
                {"bd_alias": "bd2", "bd_id": "bd_step_1", "bd_kind": "step",
                 "name": "Check ID", "flow_name": "BD Flow", "functionality": "Validate user ID"},
            ],
        }), "2026-08-18T10:00:00Z"),
    )
    await mem_db.commit()

    seen: dict[str, Any] = {}

    async def _capture_ladder(build_req, parse_fn, first_effort="low", label="", provider_id=""):
        seen["payload"] = json.loads(build_req("low").messages[1].content)
        raw = json.dumps({"results": [
            {"unit_id": "u1", "verdict": "COVERED", "kept_bd_ids": ["bd1"], "basis_bd_id": "bd1",
             "citations": [], "reason": "instance of the guard"}
        ]})
        return parse_fn(raw), raw

    monkeypatch.setattr("domain.user_flow._verdict.call_with_reasoning_ladder", _capture_ladder)

    summary = await run_user_flow_verdicts(
        db=mem_db, doc_id=doc_id, cluster_id=cluster_id, snapshot_id=snapshot_id,
        provider_id="mock_provider", run_id=run_id,
    )

    cands = seen["payload"][0]["bd_candidates"]
    assert [c["bd_alias"] for c in cands] == ["bd1", "bd2"]
    # Registry order is the mapper's, not load_bd_context's (which puts flow/step before branch),
    # and the flow unit the mapper excluded is absent.
    assert [c["bd_id"] for c in cands] == ["bd_branch_1", "bd_step_1"]
    assert all(c["bd_code_verdict"] == "MATCH" for c in cands)
    assert cands[0]["bd_code_verdict_reason"] == "reason"

    # bd1 resolved through the mapper's alias, so the branch (not the step) is what got covered.
    assert summary.step_verdicts["s1"] == "COVERED"
    async with mem_db.execute(
        "SELECT verdict FROM user_verdicts WHERE run_id = ? AND side = 'bd' AND ref_id = 'bd_branch_1'", (run_id,)
    ) as cur:
        assert (await cur.fetchone())[0] == "COVERED"


@pytest.mark.asyncio
async def test_verifier_payload_carries_code_verdict_evidence(mem_db, monkeypatch):
    """Basis (ii) is blind without the evidence behind the stored MATCH/PARTIAL verdict."""
    doc_id, cluster_id, snapshot_id, run_id = "doc_ev", "cl_ev", "snap_ev", "run_ev"
    await _seed_minimal_world(
        mem_db, doc_id=doc_id, cluster_id=cluster_id, snapshot_id=snapshot_id,
        run_id=run_id, step_ids=["s1"],
    )
    await mem_db.execute(
        "UPDATE business_unit_verdicts SET evidence_json = ? WHERE unit_id = 'bd_step_1'",
        (json.dumps([{"rel_path": "AUTH.cbl", "line_start": 10, "line_end": 12, "text": "IF USER-ID = SPACES"}]),),
    )
    await mem_db.commit()

    seen: dict[str, Any] = {}

    async def _capture_ladder(build_req, parse_fn, first_effort="low", label="", provider_id=""):
        seen["payload"] = json.loads(build_req("low").messages[1].content)
        raw = json.dumps({"results": [
            {"unit_id": "u1", "verdict": "UNVERIFIABLE", "kept_bd_ids": [], "citations": [], "reason": "x"}
        ]})
        return parse_fn(raw), raw

    monkeypatch.setattr("domain.user_flow._verdict.call_with_reasoning_ladder", _capture_ladder)
    await run_user_flow_verdicts(
        db=mem_db, doc_id=doc_id, cluster_id=cluster_id, snapshot_id=snapshot_id,
        provider_id="mock_provider", run_id=run_id,
    )

    cand = next(c for c in seen["payload"][0]["bd_candidates"] if c["bd_id"] == "bd_step_1")
    assert cand["bd_code_verdict_evidence"] == ["AUTH.cbl:10-12 IF USER-ID = SPACES"]


@pytest.mark.asyncio
async def test_tier1_unresolved_keeps_bd_units_out_of_both_lists(mem_db, monkeypatch):
    """UNRESOLVED tier-1 is not evidence of absence: no BD_EXTRA, no BD_UNMAPPED — only unassessed."""
    doc_id, cluster_id, snapshot_id, run_id = "doc_unres", "cl_unres", "snap_unres", "run_unres"
    await _seed_minimal_world(
        mem_db, doc_id=doc_id, cluster_id=cluster_id, snapshot_id=snapshot_id,
        run_id=run_id, step_ids=["s1"], tier1_status="UNRESOLVED",
    )

    summary = await run_user_flow_verdicts(
        db=mem_db, doc_id=doc_id, cluster_id=cluster_id, snapshot_id=snapshot_id,
        provider_id=None, run_id=run_id, skipped_step_ids={"s1"},
    )

    assert summary.bd_extra_count == 0
    assert summary.bd_unmapped_count == 0
    assert summary.bd_unassessed_count == 3  # bf1 + bd_step_1 + bd_branch_1

    report = await get_user_flow_report(
        db=mem_db, doc_id=doc_id, cluster_id=cluster_id, snapshot_id=snapshot_id,
    )
    assert report["run_id"] == run_id
    assert report["bd_extra"] == []
    assert report["bd_unmapped"] == []
    assert report["flows"][0]["activities"][0]["match_status"] == "UNRESOLVED"


@pytest.mark.asyncio
async def test_branch_rollup_counts_all_kept_member_of_cases(mem_db, monkeypatch):
    """N distinct user failure cases mapping onto ONE error branch roll up as 'covered by N'."""
    doc_id, cluster_id, snapshot_id, run_id = "doc_br", "cl_br", "snap_br", "run_br"
    step_ids = ["s1", "s2", "s3"]
    await _seed_minimal_world(
        mem_db, doc_id=doc_id, cluster_id=cluster_id, snapshot_id=snapshot_id,
        run_id=run_id, step_ids=step_ids,
    )
    for i, sid in enumerate(step_ids, start=1):
        await mem_db.execute(
            "INSERT INTO user_bd_mappings (id, run_id, user_step_id, bd_kind, bd_id, relation, confidence, reason, created_at) "
            "VALUES (?, ?, ?, 'branch', 'bd_branch_1', 'member_of', 0.6, 'error case', '2026-08-18T10:00:00Z')",
            (f"m{i}", run_id, sid),
        )
    await mem_db.commit()

    # bd1=bf1, bd2=bd_step_1, bd3=bd_branch_1 (load_bd_context alias order).
    monkeypatch.setattr(
        "domain.user_flow._verdict.call_with_reasoning_ladder",
        _ladder_returning([
            {"unit_id": f"u{i}", "verdict": "COVERED", "kept_bd_ids": ["bd3"], "basis_bd_id": "bd3",
             "citations": [], "reason": "instance of the guard class"}
            for i in range(1, 4)
        ]),
    )

    summary = await run_user_flow_verdicts(
        db=mem_db, doc_id=doc_id, cluster_id=cluster_id, snapshot_id=snapshot_id,
        provider_id="mock_provider", run_id=run_id,
    )
    assert summary.covered_steps == 3

    report = await get_user_flow_report(
        db=mem_db, doc_id=doc_id, cluster_id=cluster_id, snapshot_id=snapshot_id,
    )
    branch_cov = report["branch_coverage"]
    assert len(branch_cov) == 1
    assert branch_cov[0]["branch_id"] == "bd_branch_1"
    assert branch_cov[0]["incoming_step_count"] == 3
    assert sorted(branch_cov[0]["incoming_step_ids"]) == step_ids
    assert branch_cov[0]["is_user_flow_gap"] is False

    # The branch is BD-side COVERED; the sibling BD step in the same matched flow is BD_UNMAPPED.
    async with mem_db.execute(
        "SELECT ref_id, verdict FROM user_verdicts WHERE run_id = ? AND side = 'bd'", (run_id,)
    ) as cur:
        bd_v = {r[0]: r[1] for r in await cur.fetchall()}
    assert bd_v["bd_branch_1"] == "COVERED"
    assert bd_v["bd_step_1"] == "BD_UNMAPPED"
