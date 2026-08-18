"""Unit tests for Phase UP3-2: Matching recall, per-pair tier-1, branch identity, shortlist overhaul, decoupled semantic mapping."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import pytest

from domain.model_connector.service import ProviderConfigService
from domain.model_connector.types import ChatRequest
from domain.user_flow._activity_match import (
    LLMTier1MatchResponse,
    _evaluate_activity_batch,
    _load_cluster_bd_flows,
    match_user_activities_to_bd_flows,
)
from domain.user_flow._mapping import BDContext, BDUnit, load_bd_context
from domain.user_flow._align import (
    _shortlist_bd_candidates,
    align_user_flow_steps,
    evaluate_align_batch,
    StepAlignContext,
)
from shared.utils import new_id, utc_now_iso


@pytest.fixture
def memory_db_schema():
    """Create in-memory SQLite schema for testing."""
    import aiosqlite
    from infrastructure.db.database.migrations import _MIGRATIONS

    async def _make_db():
        db = await aiosqlite.connect(":memory:")
        db.row_factory = aiosqlite.Row
        for m in _MIGRATIONS:
            await db.executescript(m["sql"])
        await db.commit()
        return db

    return _make_db


# ---------------------------------------------------------------------------
# Test A: Tier-1 Validator & Invariants (Codex R4)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tier1_validator_invariants(monkeypatch: pytest.MonkeyPatch):
    """Test all validator invariants for LLM#6 Tier-1 per-pair match response."""
    batch_activities = [
        {"id": "act1", "name_en": "Activity 1", "summary_en": "Summary 1", "member_step_ids_json": "[]"},
        {"id": "act2", "name_en": "Activity 2", "summary_en": "Summary 2", "member_step_ids_json": "[]"},
    ]
    bd_flows = [
        {"id": "bf1", "name": "Flow 1", "description": "Desc 1", "steps": [], "branches": []},
        {"id": "bf2", "name": "Flow 2", "description": "Desc 2", "steps": [], "branches": []},
    ]
    steps_by_id = {}

    async def _run_eval(mock_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        async def _mock_stream(req: ChatRequest):
            yield {"type": "content", "text": json.dumps({"matches": mock_results})}

        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", lambda self, req: _mock_stream(req))
        matches, art = await _evaluate_activity_batch(batch_activities, bd_flows, steps_by_id, provider_id="mock_prov")
        return matches

    # 1. Valid N:M pairs with mixed FULLY and PARTIAL for a1, and NONE for a2
    valid_res = [
        {"activity_alias": "a1", "bd_flow_alias": "bf1", "match_status": "FULLY", "confidence": 0.9, "reason": "Exact match"},
        {"activity_alias": "a1", "bd_flow_alias": "bf2", "match_status": "PARTIAL", "confidence": 0.6, "reason": "Overlap"},
        {"activity_alias": "a2", "bd_flow_alias": None, "match_status": "NONE", "confidence": 0.95, "reason": "No match"},
    ]
    res1 = await _run_eval(valid_res)
    assert len(res1) == 3
    a1_matches = [m for m in res1 if m["activity_id"] == "act1"]
    assert len(a1_matches) == 2
    assert {m["match_status"] for m in a1_matches} == {"FULLY", "PARTIAL"}
    a2_matches = [m for m in res1 if m["activity_id"] == "act2"]
    assert len(a2_matches) == 1
    assert a2_matches[0]["match_status"] == "NONE"
    assert a2_matches[0]["bd_flow_id"] is None

    # 2. Missing activity alias in response -> ladder retries and falls back to UNRESOLVED with LLM_NO_RESPONSE
    missing_act_res = [
        {"activity_alias": "a1", "bd_flow_alias": "bf1", "match_status": "FULLY", "confidence": 0.9, "reason": "OK"}
    ]
    res2 = await _run_eval(missing_act_res)
    assert len(res2) == 2
    assert all(m["match_status"] == "UNRESOLVED" for m in res2)
    assert all(m["reason"] == "LLM_NO_RESPONSE" for m in res2)

    # 3. Unknown activity alias -> UNRESOLVED fallback
    unknown_act_res = [
        {"activity_alias": "a1", "bd_flow_alias": "bf1", "match_status": "FULLY", "confidence": 0.9},
        {"activity_alias": "a99", "bd_flow_alias": "bf2", "match_status": "FULLY", "confidence": 0.9},
    ]
    res3 = await _run_eval(unknown_act_res)
    assert all(m["match_status"] == "UNRESOLVED" for m in res3)

    # 4. Duplicate (activity, flow) pair -> UNRESOLVED fallback
    dup_pair_res = [
        {"activity_alias": "a1", "bd_flow_alias": "bf1", "match_status": "FULLY", "confidence": 0.9},
        {"activity_alias": "a1", "bd_flow_alias": "bf1", "match_status": "PARTIAL", "confidence": 0.7},
        {"activity_alias": "a2", "bd_flow_alias": None, "match_status": "NONE", "confidence": 0.9},
    ]
    res4 = await _run_eval(dup_pair_res)
    assert all(m["match_status"] == "UNRESOLVED" for m in res4)

    # 5. Activity has both NONE and positive match -> UNRESOLVED fallback
    conflict_res = [
        {"activity_alias": "a1", "bd_flow_alias": "bf1", "match_status": "FULLY", "confidence": 0.9},
        {"activity_alias": "a1", "bd_flow_alias": None, "match_status": "NONE", "confidence": 0.9},
        {"activity_alias": "a2", "bd_flow_alias": None, "match_status": "NONE", "confidence": 0.9},
    ]
    res5 = await _run_eval(conflict_res)
    assert all(m["match_status"] == "UNRESOLVED" for m in res5)

    # 6. Positive match with null bd_flow_alias -> UNRESOLVED fallback
    null_pos_res = [
        {"activity_alias": "a1", "bd_flow_alias": None, "match_status": "FULLY", "confidence": 0.9},
        {"activity_alias": "a2", "bd_flow_alias": None, "match_status": "NONE", "confidence": 0.9},
    ]
    res6 = await _run_eval(null_pos_res)
    assert all(m["match_status"] == "UNRESOLVED" for m in res6)

    # 7. Unknown bd_flow_alias -> UNRESOLVED fallback
    unknown_flow_res = [
        {"activity_alias": "a1", "bd_flow_alias": "bf99", "match_status": "FULLY", "confidence": 0.9},
        {"activity_alias": "a2", "bd_flow_alias": None, "match_status": "NONE", "confidence": 0.9},
    ]
    res7 = await _run_eval(unknown_flow_res)
    assert all(m["match_status"] == "UNRESOLVED" for m in res7)

    # 8. Out-of-range confidence -> rejected by the schema -> UNRESOLVED fallback
    bad_conf_res = [
        {"activity_alias": "a1", "bd_flow_alias": "bf1", "match_status": "FULLY", "confidence": 1.7},
        {"activity_alias": "a2", "bd_flow_alias": None, "match_status": "NONE", "confidence": 0.9},
    ]
    res8 = await _run_eval(bad_conf_res)
    assert all(m["match_status"] == "UNRESOLVED" for m in res8)

    # 9. Order independence: the same pairs in reverse order produce the same persisted set
    shuffled = list(reversed(valid_res))
    res9 = await _run_eval(shuffled)
    assert {(m["activity_id"], m["bd_flow_id"], m["match_status"]) for m in res9} == {
        (m["activity_id"], m["bd_flow_id"], m["match_status"]) for m in res1
    }


@pytest.mark.asyncio
async def test_tier1_partial_batch_failure_isolates_to_that_batch(memory_db_schema, monkeypatch: pytest.MonkeyPatch):
    """A schema-invalid reply for ONE batch must not turn the other batch's activities UNRESOLVED."""
    db = await memory_db_schema()
    cluster_id = "clust_batches"
    now = utc_now_iso()

    bf_id = f"bf:{new_id()}"
    await db.execute(
        "INSERT INTO bd_business_flows (id, cluster_id, doc_id, sub_ix, block_key, name, description, ordinal, origin, created_at) "
        "VALUES (?, ?, 'doc1', 1, 'blk1', 'Flow One', 'Desc', 1, 'declared', ?)",
        (bf_id, cluster_id, now),
    )
    await db.commit()

    # 7 activities -> two batches (BATCH_SIZE = 5): [A1..A5] and [A6, A7].
    activities = [
        {"id": f"act{i}", "name_en": f"Activity{i}", "summary_en": "", "member_step_ids_json": "[]"}
        for i in range(1, 8)
    ]

    async def _mock_stream(req: ChatRequest):
        prompt = req.messages[1].content
        if "Activity6" in prompt:
            # Second batch: reply omits a2 entirely -> set-cover violation on every attempt.
            payload = {"matches": [
                {"activity_alias": "a1", "bd_flow_alias": "bf1", "match_status": "FULLY", "confidence": 0.9, "reason": "ok"}
            ]}
        else:
            payload = {"matches": [
                {"activity_alias": f"a{i}", "bd_flow_alias": "bf1", "match_status": "FULLY", "confidence": 0.9, "reason": "ok"}
                for i in range(1, 6)
            ]}
        yield {"type": "content", "text": json.dumps(payload)}

    monkeypatch.setattr(ProviderConfigService, "chat_stream_events", lambda self, req: _mock_stream(req))

    rows, artifact = await match_user_activities_to_bd_flows(
        db=db, doc_id="doc1", cluster_id=cluster_id, activities=activities,
        steps=[], provider_id="mock_prov", run_id="run_batches",
    )

    by_act = {r["activity_id"]: r for r in rows}
    for i in range(1, 6):
        assert by_act[f"act{i}"]["match_status"] == "FULLY"
        assert by_act[f"act{i}"]["bd_flow_id"] == bf_id
    for i in (6, 7):
        assert by_act[f"act{i}"]["match_status"] == "UNRESOLVED"
        assert by_act[f"act{i}"]["reason"] == "LLM_NO_RESPONSE"

    await db.close()


# ---------------------------------------------------------------------------
# Test B: Catalog Enrichment (Job name & Step/Branch Roster)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_catalog_roster_enrichment(memory_db_schema, monkeypatch: pytest.MonkeyPatch):
    """BD flow step functionality with job name (HNDM001N) appears in loaded catalog roster."""
    db = await memory_db_schema()
    cluster_id = "clust_enrich"
    now = utc_now_iso()

    bf_id = f"bf:{new_id()}"
    await db.execute(
        "INSERT INTO bd_business_flows (id, cluster_id, doc_id, sub_ix, block_key, name, description, ordinal, origin, created_at) "
        "VALUES (?, ?, 'doc1', 1, 'blk1', 'Launch Processing Flow', 'Launches batch job.', 1, 'declared', ?)",
        (bf_id, cluster_id, now),
    )
    bs_id = f"bs:{new_id()}"
    await db.execute(
        "INSERT INTO bd_business_steps (id, flow_id, name, functionality, ordinal, source_node_ids, created_at) "
        "VALUES (?, ?, 'Submit Job', 'Executes JCL HNDM001N on mainframe', 1, '[]', ?)",
        (bs_id, bf_id, now),
    )
    bb_id = f"bb:{new_id()}"
    await db.execute(
        "INSERT INTO bd_business_branches (id, flow_id, branch_kind, guard_description, source_step_id, source_edge_ids, created_at) "
        "VALUES (?, ?, 'error', 'Dataset allocation failure', ?, '[]', ?)",
        (bb_id, bf_id, bs_id, now),
    )
    await db.commit()

    flows = await _load_cluster_bd_flows(db, cluster_id)
    assert len(flows) == 1
    f = flows[0]
    assert len(f["steps"]) == 1
    assert "HNDM001N" in f["steps"][0]["functionality"]
    assert len(f["branches"]) == 1
    assert "allocation failure" in f["branches"][0]["guard_description"]

    # The roster must actually reach the prompt: the job name is what makes bf matchable.
    seen: dict[str, str] = {}

    async def _mock_stream(req: ChatRequest):
        seen["user_prompt"] = req.messages[1].content
        yield {
            "type": "content",
            "text": json.dumps({"matches": [
                {"activity_alias": "a1", "bd_flow_alias": "bf1", "match_status": "FULLY", "confidence": 0.9, "reason": "job name"}
            ]}),
        }

    monkeypatch.setattr(ProviderConfigService, "chat_stream_events", lambda self, req: _mock_stream(req))

    batch_acts = [{"id": "a1", "name_en": "Batch Launch", "summary_en": "Launch HNDM001N", "member_step_ids_json": "[]"}]
    res, art = await _evaluate_activity_batch(batch_acts, flows, {}, provider_id="mock_prov")
    catalog = seen["user_prompt"]
    assert "HNDM001N" in catalog, "step roster (with job name) missing from the tier-1 catalog line"
    assert "Steps: Submit Job" in catalog
    assert "Non-SUCCESS branches: [error] Dataset allocation failure" in catalog
    assert res[0]["match_status"] == "FULLY"
    await db.close()


@pytest.mark.asyncio
async def test_missing_provider_is_unresolved_but_empty_catalog_is_none():
    """NONE asserts 'the specification has no such flow' — a finding. Only an empty BD catalog
    earns it; a run with no provider never asked the question and must stay UNRESOLVED."""
    acts = [{"id": "act1", "name_en": "A", "summary_en": "", "member_step_ids_json": "[]"}]
    flows = [{"id": "bf1", "name": "Flow 1", "description": "d", "steps": [], "branches": []}]

    offline, art_offline = await _evaluate_activity_batch(acts, flows, {}, provider_id=None)
    assert offline[0]["match_status"] == "UNRESOLVED"
    assert offline[0]["reason"] == "NO_PROVIDER"
    assert art_offline["reason"] == "NO_PROVIDER"

    empty_catalog, art_empty = await _evaluate_activity_batch(acts, [], {}, provider_id="mock_prov")
    assert empty_catalog[0]["match_status"] == "NONE"
    assert empty_catalog[0]["reason"] == "no_bd_flows_in_cluster"


# ---------------------------------------------------------------------------
# Test C: Branch Identity & Context Model (Task B)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_branch_identity_payload_rendering(memory_db_schema):
    """BDUnit for branches carries flow_name, source_step_name, target_step_name."""
    db = await memory_db_schema()
    cluster_id = "clust_branch"
    now = utc_now_iso()

    bf_id = f"bf:{new_id()}"
    await db.execute(
        "INSERT INTO bd_business_flows (id, cluster_id, doc_id, sub_ix, block_key, name, description, ordinal, origin, created_at) "
        "VALUES (?, ?, 'doc1', 1, 'blk1', 'Coil Entry Flow', 'Coil test.', 1, 'declared', ?)",
        (bf_id, cluster_id, now),
    )
    bs1_id = f"bs:s1_{new_id()}"
    await db.execute(
        "INSERT INTO bd_business_steps (id, flow_id, name, functionality, ordinal, source_node_ids, created_at) "
        "VALUES (?, ?, 'Initial Display', 'Loads screen', 1, '[]', ?)",
        (bs1_id, bf_id, now),
    )
    bs2_id = f"bs:s2_{new_id()}"
    await db.execute(
        "INSERT INTO bd_business_steps (id, flow_id, name, functionality, ordinal, source_node_ids, created_at) "
        "VALUES (?, ?, 'Validate Input', 'Checks parameters', 2, '[]', ?)",
        (bs2_id, bf_id, now),
    )
    bb_id = f"bb:{new_id()}"
    await db.execute(
        "INSERT INTO bd_business_branches (id, flow_id, branch_kind, guard_description, source_step_id, target_step_id, source_edge_ids, created_at) "
        "VALUES (?, ?, 'error', 'Invalid code entered', ?, ?, '[]', ?)",
        (bb_id, bf_id, bs1_id, bs2_id, now),
    )
    await db.commit()

    bd_ctx = await load_bd_context(db, cluster_id)
    branch_units = [u for u in bd_ctx.units if u.bd_kind == "branch"]
    assert len(branch_units) == 1
    bu = branch_units[0]
    assert bu.flow_name == "Coil Entry Flow"
    assert bu.source_step_name == "Initial Display"
    assert bu.target_step_name == "Validate Input"
    assert bu.description == "Invalid code entered"

    await db.close()


# ---------------------------------------------------------------------------
# Test D: Shortlist Overhaul & Polarity Twins (Task C)
# ---------------------------------------------------------------------------

def _pagination_ctx(n_decoys: int, decoy_desc: str) -> BDContext:
    """SUCCESS/ERROR polarity twins plus `n_decoys` step units of the given description."""
    units: list[BDUnit] = [
        BDUnit(
            alias="bd1",
            unit_id="bb_succ",
            bd_kind="branch",
            flow_id="bf1",
            name="success",
            description="Next page exists and displays correctly",
            flow_name="Pagination Flow",
        ),
        BDUnit(
            alias="bd2",
            unit_id="bb_err",
            bd_kind="branch",
            flow_id="bf1",
            name="error",
            description="No next page exists / Next page does not exist",
            flow_name="Pagination Flow",
        ),
    ]
    for i in range(3, 3 + n_decoys):
        units.append(BDUnit(
            alias=f"bd{i}",
            unit_id=f"bs_{i}",
            bd_kind="step",
            flow_id="bf2",
            name=f"Step {i}",
            description=decoy_desc.format(i=i),
            flow_name="General Flow",
        ))
    return BDContext(
        cluster_id="clust1",
        units=units,
        alias_to_unit={u.alias: u for u in units},
        id_to_alias={u.unit_id: u.alias for u in units},
        catalog_text="",
    )


# Step in Japanese expecting 「次ページは存在しません」 (No next page exists)
_PAGINATION_STEP = {
    "text_ja": "次ページボタンを押下する",
    "expected_ja": "次ページは存在しません というメッセージが表示される",
    "trigger_ja": "Enter",
    "screen_name_ja": "FHNIXLOT",
}


def test_shortlist_returns_all_below_threshold():
    """Pools of <= 30 units are never truncated."""
    ctx = _pagination_ctx(28, "Generic processing step {i}")
    assert len(ctx.units) == 30
    selected = _shortlist_bd_candidates(_PAGINATION_STEP, ctx, step_code_files={"FHNIXLOT.cbl"})
    assert len(selected) == 30


def test_shortlist_overhaul_and_polarity_twin():
    """Cap=24, and the correct ERROR branch survives against its SUCCESS polarity twin."""
    ctx = _pagination_ctx(33, "Generic processing step {i}")
    selected = _shortlist_bd_candidates(_PAGINATION_STEP, ctx, step_code_files={"FHNIXLOT.cbl"})
    selected_ids = {u.unit_id for u in selected}

    # Scored cap (24) + the 2 mandatory-branch slots.
    assert len(selected) <= 26
    assert "bb_err" in selected_ids


def test_shortlist_mandatory_branches_sit_outside_the_cap():
    """With the scored cap fully occupied by higher-scoring decoys, non-SUCCESS branches still ship."""
    # Every decoy mentions the step's screen file AND its concept tokens, so all 40 outscore the
    # ERROR branch and would otherwise fill the top-24 completely.
    ctx = _pagination_ctx(40, "FHNIXLOT next page previous page error validate step {i}")
    selected = _shortlist_bd_candidates(_PAGINATION_STEP, ctx, step_code_files={"FHNIXLOT.cbl"})
    selected_ids = [u.unit_id for u in selected]

    scored_slice = selected_ids[:24]
    assert "bb_err" not in scored_slice, "decoys failed to fill the scored cap — test no longer proves the rule"
    assert "bb_err" in selected_ids, "mandatory non-SUCCESS branch was cut by the cap"
    assert len(selected) == 25  # 24 scored + 1 mandatory branch (the SUCCESS twin is not mandatory)


def test_mandatory_branches_never_consume_a_scored_slot():
    """A HIGH-scoring mandatory branch must be additive, not a displacement: the 24 scored slots
    still go to 24 ordinary candidates."""
    # The ERROR branch scores top of the pool (it matches every concept in the step text), so a
    # shortlist that scored it inside the cap would ship only 23 ordinary units + the branch.
    ctx = _pagination_ctx(40, "Generic processing step {i}")
    selected = _shortlist_bd_candidates(_PAGINATION_STEP, ctx, step_code_files={"FHNIXLOT.cbl"})
    selected_ids = [u.unit_id for u in selected]

    # bb_err is the only mandatory unit (the SUCCESS twin competes for a scored slot like any
    # other candidate); everything else in the list must be a full 24 scored winners.
    scored = [uid for uid in selected_ids if uid != "bb_err"]
    assert len(scored) == 24, "the mandatory branch displaced an ordinary scored candidate"
    assert selected_ids[-1] == "bb_err", "mandatory branch should be appended after the scored cap"
    assert len(selected) == 25


def test_step_scoring_text_never_injects_a_none_token():
    from domain.user_flow._align import _step_scoring_text

    text = _step_scoring_text({"text_ja": "次ページ", "text_en": None, "screen_name_ja": None,
                               "expected_ja": None, "trigger_ja": None})
    assert text == "次ページ"
    assert "None" not in text
    assert _step_scoring_text({}) == ""


# ---------------------------------------------------------------------------
# Test E: Decoupled Semantic Mapping & member_of Gate (Task D)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_decoupled_mapping_and_member_of_gate(memory_db_schema, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Zero-claim semantic mapping is persisted; member_of at 0.56 kept; invalid member_of rejected."""
    db = await memory_db_schema()
    doc_id = f"ufdoc:{new_id()}"
    cluster_id = f"clust:{new_id()}"
    snapshot_id = f"snap:{new_id()}"
    now = utc_now_iso()

    await db.execute("INSERT INTO repo_snapshots (id, local_repo_id, local_path, synced_at, created_at) VALUES (?, 'repo', ?, ?, ?)", (snapshot_id, str(tmp_path), now, now))
    await db.execute("INSERT INTO user_flow_docs (id, source_name, file_hash, imported_at) VALUES (?, 'test.xlsx', 'h1', ?)", (doc_id, now))

    flow_id = f"uf:{new_id()}"
    await db.execute("INSERT INTO user_flows (id, doc_id, ordinal, name_ja, name_en, kind, sheet, scope_note, created_at) VALUES (?, ?, 1, 'Flow 1', 'Flow 1', 'narrative', 'Sheet1', '', ?)", (flow_id, doc_id, now))

    step1_id = f"ustep:s1_{new_id()}"
    step2_id = f"ustep:s2_{new_id()}"
    await db.execute("INSERT INTO user_steps (id, flow_id, ordinal, kind, section_id, text_ja, in_scope, sheet, row_start, row_end, created_at) VALUES (?, ?, 1, 'action', '1', 'Error step 1', 1, 'Sheet1', 1, 2, ?)", (step1_id, flow_id, now))
    await db.execute("INSERT INTO user_steps (id, flow_id, ordinal, kind, section_id, text_ja, in_scope, sheet, row_start, row_end, created_at) VALUES (?, ?, 2, 'action', '2', 'Error step 2', 1, 'Sheet1', 3, 4, ?)", (step2_id, flow_id, now))

    # Seed BD units: 1 error branch, 1 success branch, 1 step
    bf_id = f"bf:{new_id()}"
    await db.execute("INSERT INTO bd_business_flows (id, cluster_id, doc_id, sub_ix, block_key, name, description, ordinal, origin, created_at) VALUES (?, ?, 'doc1', 1, 'blk1', 'BD Flow', 'Desc', 1, 'declared', ?)", (bf_id, cluster_id, now))
    bs_id = f"bs:{new_id()}"
    await db.execute("INSERT INTO bd_business_steps (id, flow_id, name, functionality, ordinal, source_node_ids, created_at) VALUES (?, ?, 'Step 1', 'Func 1', 1, '[]', ?)", (bs_id, bf_id, now))
    bb_err_id = f"bb:err_{new_id()}"
    await db.execute("INSERT INTO bd_business_branches (id, flow_id, branch_kind, guard_description, source_step_id, source_edge_ids, created_at) VALUES (?, ?, 'error', 'Invalid input error', ?, '[]', ?)", (bb_err_id, bf_id, bs_id, now))
    bb_succ_id = f"bb:succ_{new_id()}"
    await db.execute("INSERT INTO bd_business_branches (id, flow_id, branch_kind, guard_description, source_step_id, source_edge_ids, created_at) VALUES (?, ?, 'success', 'Valid input proceed', ?, '[]', ?)", (bb_succ_id, bf_id, bs_id, now))
    await db.commit()

    # Mock fused LLM response:
    # Step 1:
    #   - Mapping 1: realizes onto bs_id with conf 0.80, ZERO code claims -> SHOULD BE PERSISTED (decoupled semantic mapping)
    #   - Mapping 2: member_of onto bb_err_id with conf 0.56, ZERO code claims -> SHOULD BE PERSISTED (member_of floor 0.55)
    # Step 2:
    #   - Mapping 3: member_of onto bb_succ_id with conf 0.90 -> MUST BE REJECTED (member_of forbidden on success branch)
    #   - Mapping 4: member_of onto bb_err_id with conf 0.52 -> MUST BE REJECTED (below 0.55 floor)
    #   - Mapping 5: invalid_relation with conf 0.90 -> MUST BE REJECTED (unknown relation)
    async def _mock_stream(req: ChatRequest):
        results = [
            {
                "step_id": "u1",
                "presentation": False,
                "claims": [],
                "bd_mappings": [
                    {"bd_unit_id": "bd2", "relation": "realizes", "confidence": 0.80, "reason": "Semantic match without claim"},
                    {"bd_unit_id": "bd3", "relation": "member_of", "confidence": 0.56, "reason": "Error branch instance"},
                ],
            },
            {
                "step_id": "u2",
                "presentation": False,
                "claims": [],
                "bd_mappings": [
                    {"bd_unit_id": "bd4", "relation": "member_of", "confidence": 0.90, "reason": "Forbidden success branch target"},
                    {"bd_unit_id": "bd3", "relation": "member_of", "confidence": 0.52, "reason": "Below floor"},
                    {"bd_unit_id": "bd2", "relation": "invalid_rel", "confidence": 0.90, "reason": "Invalid enum"},
                ],
            },
        ]
        yield {"type": "content", "text": json.dumps({"results": results})}

    monkeypatch.setattr(ProviderConfigService, "chat_stream_events", lambda self, req: _mock_stream(req))

    user_steps = [
        {"id": step1_id, "flow_id": flow_id, "ordinal": 1, "kind": "action", "section_id": "1", "text_ja": "Error step 1", "in_scope": 1, "sheet": "Sheet1"},
        {"id": step2_id, "flow_id": flow_id, "ordinal": 2, "kind": "action", "section_id": "2", "text_ja": "Error step 2", "in_scope": 1, "sheet": "Sheet1"},
    ]

    anchors_cnt, mapped_cnt, run_summary = await align_user_flow_steps(
        db=db,
        doc_id=doc_id,
        cluster_id=cluster_id,
        snapshot_id=snapshot_id,
        run_id="run_test",
        user_steps=user_steps,
        provider_id="mock_prov",
        local_path=tmp_path,
    )

    # Check persisted user_bd_mappings
    async with db.execute("SELECT user_step_id, bd_id, relation, confidence FROM user_bd_mappings WHERE run_id = 'run_test'") as cur:
        rows = await cur.fetchall()

    mappings = [dict(r) if hasattr(r, "keys") else {"user_step_id": r[0], "bd_id": r[1], "relation": r[2], "confidence": r[3]} for r in rows]

    # Step 1 should have 2 mappings kept
    s1_maps = [m for m in mappings if m["user_step_id"] == step1_id]
    assert len(s1_maps) == 2
    assert {m["relation"] for m in s1_maps} == {"realizes", "member_of"}

    # Step 2 should have 0 mappings kept (all 3 rejected)
    s2_maps = [m for m in mappings if m["user_step_id"] == step2_id]
    assert len(s2_maps) == 0

    # Summary checks
    assert run_summary["mapped_user_step_count"] == 1
    assert run_summary["evidence_backed_mapped_step_count"] == 0

    await db.close()


# ---------------------------------------------------------------------------
# Prompt contract: the rules the deterministic gate cannot enforce must stay in the prompts
# ---------------------------------------------------------------------------

def test_align_prompt_states_the_branch_class_and_decoupling_contract():
    from domain.user_flow._align import _ALIGN_SYSTEM_PROMPT as p

    # Decoupling: mappings are semantic judgments, emitted with or without code claims.
    assert "SHOULD emit bd_mappings even when `claims` is empty" in p
    assert "not a precondition for semantic mapping" in p
    # N:1 failure-case pattern is expected, not a catch-all.
    assert "Many distinct user failure cases legitimately map to ONE error branch" in p
    # ... but only within a shared guard class (the spray guard the gate cannot check).
    assert "shared guard CLASS" in p
    assert "not merely the same failure polarity" in p
    # Anti-catch-all is scoped to SUCCESS branches / generic init steps only.
    assert "ANTI-CATCH-ALL DISCIPLINE applies strictly to SUCCESS branches and generic initialization steps" in p
    # Closed relation enum + the floors the gate enforces.
    for rel in ("realizes", "partial", "member_of", "related"):
        assert f'"{rel}"' in p
    assert ">= 0.55 for member_of" in p
    # Explicit output schema block (a missing field list silently degrades the reply).
    assert "OUTPUT FORMAT" in p and '"bd_mappings"' in p and '"confidence"' in p


def test_tier1_prompt_states_the_per_pair_and_none_softening_contract():
    from domain.user_flow._activity_match import _TIER1_SYSTEM_PROMPT as p

    assert "Emit one object per (activity, bd_flow) pair" in p
    assert 'emit exactly ONE object with bd_flow_alias: null and match_status: "NONE"' in p
    assert "Do NOT mix NONE with FULLY/PARTIAL for the same activity" in p
    assert "emit a PARTIAL pair with low confidence rather than NONE" in p
    assert "OUTPUT JSON SCHEMA" in p
    for field in ("activity_alias", "bd_flow_alias", "match_status", "confidence", "reason"):
        assert field in p


# ---------------------------------------------------------------------------
# Test F: Spray Negatives Gate Rejection (Codex R2)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_spray_negatives_gate_rejection(memory_db_schema, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Gate rejects member_of onto SUCCESS branch, below confidence floor, or unknown enum."""
    db = await memory_db_schema()
    doc_id = f"ufdoc:{new_id()}"
    cluster_id = f"clust:{new_id()}"
    snapshot_id = f"snap:{new_id()}"
    now = utc_now_iso()

    await db.execute("INSERT INTO repo_snapshots (id, local_repo_id, local_path, synced_at, created_at) VALUES (?, 'repo', ?, ?, ?)", (snapshot_id, str(tmp_path), now, now))
    await db.execute("INSERT INTO user_flow_docs (id, source_name, file_hash, imported_at) VALUES (?, 'test.xlsx', 'h1', ?)", (doc_id, now))

    flow_id = f"uf:{new_id()}"
    await db.execute("INSERT INTO user_flows (id, doc_id, ordinal, name_ja, name_en, kind, sheet, scope_note, created_at) VALUES (?, ?, 1, 'Flow 1', 'Flow 1', 'narrative', 'Sheet1', '', ?)", (flow_id, doc_id, now))

    step_id = f"ustep:spray_{new_id()}"
    await db.execute("INSERT INTO user_steps (id, flow_id, ordinal, kind, section_id, text_ja, in_scope, sheet, row_start, row_end, created_at) VALUES (?, ?, 1, 'action', '1', 'Spray negative test', 1, 'Sheet1', 1, 2, ?)", (step_id, flow_id, now))

    bf_id = f"bf:{new_id()}"
    await db.execute("INSERT INTO bd_business_flows (id, cluster_id, doc_id, sub_ix, block_key, name, description, ordinal, origin, created_at) VALUES (?, ?, 'doc1', 1, 'blk1', 'BD Flow', 'Desc', 1, 'declared', ?)", (bf_id, cluster_id, now))
    bs_id = f"bs:{new_id()}"
    await db.execute("INSERT INTO bd_business_steps (id, flow_id, name, functionality, ordinal, source_node_ids, created_at) VALUES (?, ?, 'Step 1', 'Func 1', 1, '[]', ?)", (bs_id, bf_id, now))
    bb_succ_id = f"bb:succ_{new_id()}"
    await db.execute("INSERT INTO bd_business_branches (id, flow_id, branch_kind, guard_description, source_step_id, source_edge_ids, created_at) VALUES (?, ?, 'success', 'Valid proceed', ?, '[]', ?)", (bb_succ_id, bf_id, bs_id, now))
    bb_err_id = f"bb:err_{new_id()}"
    await db.execute("INSERT INTO bd_business_branches (id, flow_id, branch_kind, guard_description, source_step_id, source_edge_ids, created_at) VALUES (?, ?, 'error', 'Unrelated error', ?, '[]', ?)", (bb_err_id, bf_id, bs_id, now))
    bd_ctx = await load_bd_context(db, cluster_id)
    succ_alias = bd_ctx.id_to_alias[bb_succ_id]
    step_alias = bd_ctx.id_to_alias[bs_id]
    err_alias = bd_ctx.id_to_alias[bb_err_id]

    async def _mock_stream(req: ChatRequest):
        results = [
            {
                "step_id": "u1",
                "presentation": False,
                "claims": [],
                "bd_mappings": [
                    # Spray negative 1: member_of onto SUCCESS polarity twin (MUST BE DROPPED)
                    {"bd_unit_id": succ_alias, "relation": "member_of", "confidence": 0.95, "reason": "Spray onto success branch"},
                    # Spray negative 2: member_of onto Step (not branch) (MUST BE DROPPED)
                    {"bd_unit_id": step_alias, "relation": "member_of", "confidence": 0.90, "reason": "Spray onto step"},
                    # Spray negative 3: member_of onto Error branch but conf 0.50 (< 0.55) (MUST BE DROPPED)
                    {"bd_unit_id": err_alias, "relation": "member_of", "confidence": 0.50, "reason": "Spray below floor"},
                ],
            }
        ]
        yield {"type": "content", "text": json.dumps({"results": results})}

    monkeypatch.setattr(ProviderConfigService, "chat_stream_events", lambda self, req: _mock_stream(req))

    user_steps = [
        {"id": step_id, "flow_id": flow_id, "ordinal": 1, "kind": "action", "section_id": "1", "text_ja": "Spray negative test", "in_scope": 1, "sheet": "Sheet1"},
    ]

    anchors_cnt, mapped_cnt, run_summary = await align_user_flow_steps(
        db=db,
        doc_id=doc_id,
        cluster_id=cluster_id,
        snapshot_id=snapshot_id,
        run_id="run_spray_test",
        user_steps=user_steps,
        provider_id="mock_prov",
        local_path=tmp_path,
    )

    async with db.execute("SELECT count(*) FROM user_bd_mappings WHERE run_id = 'run_spray_test'") as cur:
        cnt = (await cur.fetchone())[0]

    assert cnt == 0
    assert run_summary["mapped_user_step_count"] == 0
    await db.close()


@pytest.mark.asyncio
async def test_high_confidence_guard_class_sprays_are_flagged(memory_db_schema, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """The confidence floor cannot catch a CONFIDENT spray. A member_of onto a branch whose guard
    shares nothing with the step (unrelated failure condition, or same screen/different outcome)
    is kept — deciding cross-language semantics is the model's job — but it must be FLAGGED for
    review, never silently indistinguishable from a corroborated mapping."""
    db = await memory_db_schema()
    doc_id = f"ufdoc:{new_id()}"
    cluster_id = f"clust:{new_id()}"
    snapshot_id = f"snap:{new_id()}"
    now = utc_now_iso()

    await db.execute("INSERT INTO repo_snapshots (id, local_repo_id, local_path, synced_at, created_at) VALUES (?, 'repo', ?, ?, ?)", (snapshot_id, str(tmp_path), now, now))
    await db.execute("INSERT INTO user_flow_docs (id, source_name, file_hash, imported_at) VALUES (?, 'test.xlsx', 'h1', ?)", (doc_id, now))
    flow_id = f"uf:{new_id()}"
    await db.execute("INSERT INTO user_flows (id, doc_id, ordinal, name_ja, name_en, kind, sheet, scope_note, created_at) VALUES (?, ?, 1, 'F', 'F', 'narrative', 'S', '', ?)", (flow_id, doc_id, now))

    # One user failure case: "next page does not exist".
    step_id = f"ustep:{new_id()}"
    await db.execute(
        "INSERT INTO user_steps (id, flow_id, ordinal, kind, section_id, text_ja, expected_ja, in_scope, sheet, row_start, row_end, created_at) "
        "VALUES (?, ?, 1, 'action', '1', '次ページボタンを押下する', '次ページは存在しません', 1, 'S', 1, 2, ?)",
        (step_id, flow_id, now),
    )

    bf_id = f"bf:{new_id()}"
    await db.execute("INSERT INTO bd_business_flows (id, cluster_id, doc_id, sub_ix, block_key, name, description, ordinal, origin, created_at) VALUES (?, ?, 'doc1', 1, 'blk1', 'Pagination Flow', 'Desc', 1, 'declared', ?)", (bf_id, cluster_id, now))
    bs_id = f"bs:{new_id()}"
    await db.execute("INSERT INTO bd_business_steps (id, flow_id, name, functionality, ordinal, source_node_ids, created_at) VALUES (?, ?, 'Paging', 'Paging', 1, '[]', ?)", (bs_id, bf_id, now))

    # (a) matching guard class, (b) unrelated failure condition, (c) same screen, different outcome
    bb_right = f"bb:right_{new_id()}"
    await db.execute("INSERT INTO bd_business_branches (id, flow_id, branch_kind, guard_description, source_step_id, source_edge_ids, created_at) VALUES (?, ?, 'error', 'No next page exists', ?, '[]', ?)", (bb_right, bf_id, bs_id, now))
    bb_unrelated = f"bb:unrel_{new_id()}"
    await db.execute("INSERT INTO bd_business_branches (id, flow_id, branch_kind, guard_description, source_step_id, source_edge_ids, created_at) VALUES (?, ?, 'error', 'Dataset allocation failed on the host job', ?, '[]', ?)", (bb_unrelated, bf_id, bs_id, now))
    bb_other_outcome = f"bb:other_{new_id()}"
    await db.execute("INSERT INTO bd_business_branches (id, flow_id, branch_kind, guard_description, source_step_id, source_edge_ids, created_at) VALUES (?, ?, 'error', 'Operator lacks authority for this transaction', ?, '[]', ?)", (bb_other_outcome, bf_id, bs_id, now))

    bd_ctx = await load_bd_context(db, cluster_id)
    a_right = bd_ctx.id_to_alias[bb_right]
    a_unrel = bd_ctx.id_to_alias[bb_unrelated]
    a_other = bd_ctx.id_to_alias[bb_other_outcome]

    async def _mock_stream(req: ChatRequest):
        yield {"type": "content", "text": json.dumps({"results": [{
            "step_id": "u1",
            "presentation": False,
            "claims": [],
            "bd_mappings": [
                {"bd_unit_id": a_right, "relation": "member_of", "confidence": 0.9, "reason": "same guard class"},
                {"bd_unit_id": a_unrel, "relation": "MEMBER_OF", "confidence": 0.9, "reason": "confident spray"},
                {"bd_unit_id": a_other, "relation": "member_of", "confidence": 0.9, "reason": "same screen, other outcome"},
            ],
        }]})}

    monkeypatch.setattr(ProviderConfigService, "chat_stream_events", lambda self, req: _mock_stream(req))

    user_steps = [{
        "id": step_id, "flow_id": flow_id, "ordinal": 1, "kind": "action", "section_id": "1",
        "text_ja": "次ページボタンを押下する", "expected_ja": "次ページは存在しません", "in_scope": 1, "sheet": "S",
    }]
    await align_user_flow_steps(
        db=db, doc_id=doc_id, cluster_id=cluster_id, snapshot_id=snapshot_id, run_id="run_guard",
        user_steps=user_steps, provider_id="mock_prov", local_path=tmp_path,
    )

    async with db.execute(
        "SELECT ref_id, payload FROM user_run_artifacts WHERE run_id = 'run_guard' AND ref_id LIKE 'align:%'"
    ) as cur:
        art = json.loads((await cur.fetchone())[1])
    flags = {m["bd_id"]: m.get("guard_class_flag") for m in art["mappings"]}

    assert flags[bb_right] is None, "a corroborated guard class must not be flagged"
    assert flags[bb_unrelated] == "UNCORROBORATED_GUARD_CLASS"
    assert flags[bb_other_outcome] == "UNCORROBORATED_GUARD_CLASS"

    # The closed enum is stored canonically: the model's "MEMBER_OF" spelling never reaches the DB.
    async with db.execute("SELECT DISTINCT relation FROM user_bd_mappings WHERE run_id = 'run_guard'") as cur:
        assert {r[0] for r in await cur.fetchall()} == {"member_of"}

    await db.close()

