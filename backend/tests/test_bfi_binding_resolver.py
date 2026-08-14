"""Unit and integration tests for double-agent LLM binding resolver and Phase 6 enricher."""
from pathlib import Path
import json
import pytest

from domain.business_flow_integrity import (
    BindingResolutionResult,
    EnrichResult,
    MatchResult,
    build_evidence_index,
    enrich_unit_bindings,
    resolve_residual_unit_bindings,
    run_source_aware_verdicts,
)
from infrastructure.db.database import close_db, get_db, init_db
from shared.utils import new_id, utc_now_iso


@pytest.mark.asyncio
async def test_binding_resolver_unit_and_integration(tmp_path, monkeypatch):
    """Test binding resolver rules and pipeline integration."""
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    source_dir = tmp_path / "source"
    source_dir.mkdir()

    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(db_dir))
    await init_db()

    try:
        db = get_db()
        snap_id = f"snap-{new_id()}"
        repo_id = f"repo-{new_id()}"
        cluster_id = f"cluster-{new_id()}"
        now = utc_now_iso()

        # Seed repo_snapshots & manifest
        await db.execute(
            "INSERT INTO repo_snapshots (id, local_repo_id, local_path, synced_at, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (snap_id, repo_id, str(source_dir), now, now),
        )

        manifest_paths = ["HNIXLOT.cbl", "HSBMENU5.pfd", "PHNIXLOT.clist"]
        for p in manifest_paths:
            (source_dir / p).write_text(f"/* {p} content */\n", encoding="utf-8")
            await db.execute(
                "INSERT INTO manifest_files (id, snapshot_id, rel_path, language, category, size_bytes, mtime_ns, checksum) "
                "VALUES (?, ?, ?, 'cobol', 'source', 100, 0, 'hash')",
                (f"man-{p}", snap_id, p),
            )
        await db.execute(
            "INSERT INTO source_facts (snapshot_id, rel_path, language, fact_type, semantic_key, occurrence_ix, parent_key, name, value, attributes, line_start, line_end, extractor, extractor_ver, created_at) "
            "VALUES (?, 'HNIXLOT.cbl', 'cobol', 'branch', 'branch/HNIXLOT.MAIN#1', 0, 'section/HNIXLOT.MAIN', 'IF', 'X > 10', '{\"kind\": \"if\"}', 1, 1, 'cobol_antlr', '1.0.0', ?)",
            (snap_id, now),
        )
        await db.commit()

        # Seed bd_business_flows & steps/branches
        flow_id = f"flow-{new_id()}"
        flow2_id = f"flow-{new_id()}-2"
        await db.execute(
            "INSERT INTO bd_business_flows (id, cluster_id, doc_id, sub_ix, block_key, name, description, ordinal, origin, created_at) "
            "VALUES (?, ?, 'doc-1', 1, 'block-1', 'Main Flow', 'Flow desc', 1, 'llm', ?)",
            (flow_id, cluster_id, now),
        )
        await db.execute(
            "INSERT INTO bd_business_flows (id, cluster_id, doc_id, sub_ix, block_key, name, description, ordinal, origin, created_at) "
            "VALUES (?, ?, 'doc-1', 2, 'block-2', 'External Flow', 'Flow desc 2', 2, 'llm', ?)",
            (flow2_id, cluster_id, now),
        )

        node1_id = f"bdnode:{cluster_id}:1"
        node2_id = f"bdnode:{cluster_id}:2"
        node3_id = f"bdnode:{cluster_id}:3"

        # Node 1: Deterministic match -> HNIXLOT.cbl
        await db.execute(
            "INSERT INTO bd_flow_nodes (id, cluster_id, doc_id, node_kind, binding, binding_type, label, ordinal, provenance_tier, created_at) "
            "VALUES (?, ?, 'doc-1', 'program', 'HNIXLOT', 'PROGRAM', 'Step 1 (HNIXLOT)', 1, 'P1', ?)",
            (node1_id, cluster_id, now),
        )
        # Node 2: Residual candidate -> "Reporting Service" (unresolved deterministically, LLM maps to HNIXLOT.cbl)
        await db.execute(
            "INSERT INTO bd_flow_nodes (id, cluster_id, doc_id, node_kind, binding, binding_type, label, ordinal, provenance_tier, created_at) "
            "VALUES (?, ?, 'doc-1', 'program', 'Reporting Service', NULL, 'Step 2 (Reporting)', 2, 'P1', ?)",
            (node2_id, cluster_id, now),
        )
        # Node 3: Genuine external -> "SHIKENSV" (LLM maps to no_file: true)
        await db.execute(
            "INSERT INTO bd_flow_nodes (id, cluster_id, doc_id, node_kind, binding, binding_type, label, ordinal, provenance_tier, created_at) "
            "VALUES (?, ?, 'doc-1', 'program', 'SHIKENSV', NULL, 'Step 3 (External SVC)', 3, 'P1', ?)",
            (node3_id, cluster_id, now),
        )

        step1_id = f"step-{new_id()}-1"
        step2_id = f"step-{new_id()}-2"
        step3_id = f"step-{new_id()}-3"

        await db.execute(
            "INSERT INTO bd_business_steps (id, flow_id, name, functionality, ordinal, source_node_ids, created_at) VALUES (?, ?, 'Step 1', 'Execute HNIXLOT', 1, ?, ?)",
            (step1_id, flow_id, json.dumps([node1_id]), now),
        )
        await db.execute(
            "INSERT INTO bd_business_steps (id, flow_id, name, functionality, ordinal, source_node_ids, created_at) VALUES (?, ?, 'Step 2', 'Execute Reporting', 2, ?, ?)",
            (step2_id, flow_id, json.dumps([node2_id]), now),
        )
        await db.execute(
            "INSERT INTO bd_business_steps (id, flow_id, name, functionality, ordinal, source_node_ids, created_at) VALUES (?, ?, 'Step 3', 'Call External SVC', 3, ?, ?)",
            (step3_id, flow2_id, json.dumps([node3_id]), now),
        )
        await db.commit()

        # Build evidence index
        await build_evidence_index(db, snap_id)

        # -------------------------------------------------------------------
        # 1. Direct Test: resolve_residual_unit_bindings logic & filters
        # -------------------------------------------------------------------

        residual_units = [
            {"unit_id": step2_id, "unit_kind": "step", "prose": "Step 2", "label": "Step 2", "binding": "Reporting Service"},
            {"unit_id": step3_id, "unit_kind": "step", "prose": "Step 3", "label": "Step 3", "binding": "SHIKENSV"},
        ]

        from domain.business_flow_integrity._binding_resolver import LLMExtractorItem, LLMVerifierItem

        async def mock_extractor_batch(batch_units, manifest_info, provider_id, semaphore):
            res = {}
            for u in batch_units:
                if u["unit_id"] == step2_id:
                    res[u["unit_id"]] = LLMExtractorItem(
                        unit_id=step2_id,
                        candidates=["HNIXLOT.cbl", "HALLUCINATED.cbl"],
                        no_file=False,
                        reason="Matched HNIXLOT",
                    )
                else:
                    res[u["unit_id"]] = LLMExtractorItem(
                        unit_id=step3_id,
                        candidates=[],
                        no_file=True,
                        reason="External service",
                    )
            return res, 0

        async def mock_verifier_batch(batch_units, extractor_results, manifest_info, provider_id, semaphore):
            res = {}
            for u in batch_units:
                if u["unit_id"] == step2_id:
                    res[u["unit_id"]] = LLMVerifierItem(
                        unit_id=step2_id,
                        confirmed_rel_paths=["HNIXLOT.cbl", "HALLUCINATED.cbl"],
                        no_file=False,
                        reason="Confirmed HNIXLOT.cbl",
                    )
                else:
                    res[u["unit_id"]] = LLMVerifierItem(
                        unit_id=step3_id,
                        confirmed_rel_paths=[],
                        no_file=True,
                        reason="Confirmed external service",
                    )
            return res, 0

        monkeypatch.setattr("domain.business_flow_integrity._binding_resolver._evaluate_extractor_batch", mock_extractor_batch)
        monkeypatch.setattr("domain.business_flow_integrity._binding_resolver._evaluate_verifier_batch", mock_verifier_batch)

        res_map = await resolve_residual_unit_bindings(db, snap_id, residual_units, manifest_paths, "dummy-provider")

        # 1. Check hallucinated file dropped
        assert step2_id in res_map
        assert res_map[step2_id].confirmed_rel_paths == ["HNIXLOT.cbl"]
        assert res_map[step2_id].no_file is False

        # 2. Check no_file classification
        assert step3_id in res_map
        assert res_map[step3_id].confirmed_rel_paths == []
        assert res_map[step3_id].no_file is True
        assert "Confirmed external service" in res_map[step3_id].reason

        # -------------------------------------------------------------------
        # 2. Pipeline Integration & Artifact Persist Test (Phase 6 Enrich)
        # -------------------------------------------------------------------

        # Mock enrich_unit_bindings for Phase 6 pipeline
        async def mock_enrich_bindings(db, snap, units, manifest, provider):
            return {
                step1_id: EnrichResult(
                    unit_id=step1_id,
                    confirmed_rel_paths=["HNIXLOT.cbl"],
                    no_file=False,
                    reason="Confirmed HNIXLOT.cbl",
                    agent_raw={"unit_id": step1_id},
                    retry_count=0,
                ),
                step2_id: EnrichResult(
                    unit_id=step2_id,
                    confirmed_rel_paths=["HNIXLOT.cbl"],
                    no_file=False,
                    reason="Confirmed HNIXLOT.cbl",
                    agent_raw={"unit_id": step2_id},
                    retry_count=0,
                ),
                step3_id: EnrichResult(
                    unit_id=step3_id,
                    confirmed_rel_paths=[],
                    no_file=True,
                    reason="External service",
                    agent_raw={"unit_id": step3_id},
                    retry_count=0,
                ),
            }

        monkeypatch.setattr("domain.business_flow_integrity._verifier.enrich_unit_bindings", mock_enrich_bindings)

        # Mock matcher batch in _verifier.py
        async def mock_matcher_eval_batch(batch_units, snippets_by_unit, provider_id, semaphore):
            res = {}
            for u in batch_units:
                res[u["unit_id"]] = MatchResult(
                    unit_id=u["unit_id"],
                    subclaims=["Claim 1"],
                    citations=[{"rel_path": "HNIXLOT.cbl", "line_start": 1, "line_end": 1}],
                    maps_to_code=True,
                    reason="Matched",
                    agent_raw={"unit_id": u["unit_id"]},
                    retry_count=0,
                )
            return res, {"retry_count": 0, "latency_ms": 5.0}

        monkeypatch.setattr("domain.business_flow_integrity._verifier._evaluate_matcher_batch", mock_matcher_eval_batch)

        # Mock verifier batch in _verifier.py
        from domain.business_flow_integrity._verifier import LLMAspects, LLMCitation, LLMUnitVerification

        async def mock_verifier_eval_batch(batch_units, snippets_by_unit, matcher_results, provider_id, semaphore):
            res = {}
            for u in batch_units:
                res[u["unit_id"]] = LLMUnitVerification(
                    unit_id=u["unit_id"],
                    subclaims=["Claim 1"],
                    aspects=LLMAspects(
                        target_reachable="YES",
                        guard_equivalence="NOT_APPLICABLE",
                        route_order="YES",
                        negative_modality="NOT_APPLICABLE",
                    ),
                    citations=[LLMCitation(rel_path="HNIXLOT.cbl", line_start=1, line_end=1)],
                    verdict="MATCH",
                    reason_codes=["match_confirmed"],
                    reason="Verified",
                )
            return res, {"retry_count": 0, "latency_ms": 10.0}

        monkeypatch.setattr("domain.business_flow_integrity._verifier._evaluate_verifier_batch", mock_verifier_eval_batch)

        # Mock ProviderConfigService.get_by_id
        async def mock_get_by_id(self, pid):
            return type("Config", (), {"model_id": "test-model"})()

        monkeypatch.setattr(
            "domain.business_flow_integrity._verifier.ProviderConfigService.get_by_id",
            mock_get_by_id,
        )

        verdict_res = await run_source_aware_verdicts(db, cluster_id, snap_id, provider_id="dummy-provider")
        assert verdict_res.total_units == 3

        # Check DB verdicts:
        # Step 1 (deterministic) -> MAPPED / MATCH
        # Step 2 (LLM resolved to HNIXLOT.cbl) -> MAPPED / MATCH
        # Step 3 (LLM resolved to no_file) -> UNKNOWN with reason NO_FILE_IN_SNAPSHOT
        async with db.execute("SELECT unit_id, verdict, reason FROM business_unit_verdicts WHERE cluster_id=? ORDER BY unit_id", (cluster_id,)) as cur:
            v_rows = {r["unit_id"]: dict(r) for r in await cur.fetchall()}

        assert v_rows[step1_id]["verdict"] == "MATCH"
        assert v_rows[step2_id]["verdict"] == "MATCH"
        assert v_rows[step3_id]["verdict"] == "UNKNOWN"
        assert "NO_FILE_IN_SNAPSHOT" in v_rows[step3_id]["reason"] or "classified as no backing file" in v_rows[step3_id]["reason"]

        # Check persisted artifact payloads
        async with db.execute("SELECT payload FROM bfi_run_artifacts WHERE cluster_id=?", (cluster_id,)) as cur:
            art_rows = [json.loads(r["payload"]) for r in await cur.fetchall()]

        assert len(art_rows) == 3
        # Check enrich sub-object is present for units
        step2_art = next(a for a in art_rows if "Step 2" in a["unit_prose"])
        assert step2_art["enrich"] is not None
        assert step2_art["enrich"]["confirmed_rel_paths"] == ["HNIXLOT.cbl"]

        step3_art = next(a for a in art_rows if "Step 3" in a["unit_prose"])
        assert step3_art["enrich"] is not None
        assert step3_art["enrich"]["no_file"] is True

        step1_art = next(a for a in art_rows if "Step 1" in a["unit_prose"])
        assert step1_art["enrich"] is not None
        assert step1_art["enrich"]["confirmed_rel_paths"] == ["HNIXLOT.cbl"]

    finally:
        await close_db()


# ---------------------------------------------------------------------------
# TICKET P5-REVIEW-FIXES acceptance tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verifier_missing_never_falls_back_to_extractor_alone(tmp_path, monkeypatch):
    """FIX 3: Agent 2 (verifier) missing/failed must NEVER fall back to confirming off Agent 1
    (extractor) alone -- a transport failure is not a validated "no file exists" classification.
    Expect confirmed_rel_paths=[], no_file=False, reason=LLM_NO_RESPONSE, Agent 1's proposal kept
    only for audit (agent1_raw), Agent 2 absent (agent2_raw=None)."""
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(db_dir))
    await init_db()

    try:
        db = get_db()
        snap_id = f"snap-{new_id()}"
        repo_id = f"repo-{new_id()}"
        now = utc_now_iso()

        await db.execute(
            "INSERT INTO repo_snapshots (id, local_repo_id, local_path, synced_at, created_at) VALUES (?, ?, ?, ?, ?)",
            (snap_id, repo_id, str(source_dir), now, now),
        )
        manifest_paths = ["HNIXLOT.cbl"]
        for p in manifest_paths:
            (source_dir / p).write_text(f"/* {p} content */\n", encoding="utf-8")
            await db.execute(
                "INSERT INTO manifest_files (id, snapshot_id, rel_path, language, category, size_bytes, mtime_ns, checksum) "
                "VALUES (?, ?, ?, 'cobol', 'source', 100, 0, 'hash')",
                (f"man-{p}", snap_id, p),
            )
        await db.commit()

        step_id = f"step-{new_id()}"
        residual_units = [
            {"unit_id": step_id, "unit_kind": "step", "prose": "Reporting step", "label": "Step 2", "binding": "Reporting Service"},
        ]

        from domain.business_flow_integrity._binding_resolver import LLMExtractorItem

        async def mock_extractor_batch(batch_units, system_prompt, provider_id, semaphore):
            res = {u["unit_id"]: LLMExtractorItem(
                unit_id=u["unit_id"], candidates=["HNIXLOT.cbl"], no_file=False, reason="Agent 1 proposes HNIXLOT.cbl",
            ) for u in batch_units}
            return res, 0

        async def mock_verifier_batch_empty(batch_units, extractor_results, system_prompt, provider_id, semaphore):
            return {}, 0

        monkeypatch.setattr("domain.business_flow_integrity._binding_resolver._evaluate_extractor_batch", mock_extractor_batch)
        monkeypatch.setattr("domain.business_flow_integrity._binding_resolver._evaluate_verifier_batch", mock_verifier_batch_empty)

        res_map = await resolve_residual_unit_bindings(db, snap_id, residual_units, manifest_paths, "dummy-provider")

        result = res_map[step_id]
        assert result.confirmed_rel_paths == []
        assert result.no_file is False
        assert result.reason == "LLM_NO_RESPONSE"
        assert result.agent1_raw is not None  # Agent 1's proposal kept for audit only, not confirmed
        assert result.agent2_raw is None
    finally:
        await close_db()


@pytest.mark.asyncio
async def test_verifier_missing_ends_unresolved_asset_not_no_file(tmp_path, monkeypatch):
    """FIX 3 downstream: a residual unit whose binding resolution hits ladder failure
    (no_file=False, LLM_NO_RESPONSE) must abstain UNRESOLVED_ASSET in the verifier pipeline --
    never the factual NO_FILE_IN_SNAPSHOT claim (that requires a validated no_file=True)."""
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(db_dir))
    await init_db()

    try:
        db = get_db()
        snap_id = f"snap-{new_id()}"
        repo_id = f"repo-{new_id()}"
        cluster_id = f"cluster-{new_id()}"
        now = utc_now_iso()

        await db.execute(
            "INSERT INTO repo_snapshots (id, local_repo_id, local_path, synced_at, created_at) VALUES (?, ?, ?, ?, ?)",
            (snap_id, repo_id, str(source_dir), now, now),
        )
        manifest_paths = ["HNIXLOT.cbl"]
        for p in manifest_paths:
            (source_dir / p).write_text(f"/* {p} content */\n", encoding="utf-8")
            await db.execute(
                "INSERT INTO manifest_files (id, snapshot_id, rel_path, language, category, size_bytes, mtime_ns, checksum) "
                "VALUES (?, ?, ?, 'cobol', 'source', 100, 0, 'hash')",
                (f"man-{p}", snap_id, p),
            )
        await db.commit()
        await build_evidence_index(db, snap_id)

        flow_id = f"flow-{new_id()}"
        await db.execute(
            "INSERT INTO bd_business_flows (id, cluster_id, doc_id, sub_ix, block_key, name, description, ordinal, origin, created_at) "
            "VALUES (?, ?, 'doc-1', 1, 'block-1', 'Main Flow', 'Flow desc', 1, 'llm', ?)",
            (flow_id, cluster_id, now),
        )
        node_id = f"bdnode:{cluster_id}:1"
        await db.execute(
            "INSERT INTO bd_flow_nodes (id, cluster_id, doc_id, node_kind, binding, binding_type, label, ordinal, provenance_tier, created_at) "
            "VALUES (?, ?, 'doc-1', 'program', 'Reporting Service', NULL, 'Step (Reporting)', 1, 'P1', ?)",
            (node_id, cluster_id, now),
        )
        step_id = f"step-{new_id()}"
        await db.execute(
            "INSERT INTO bd_business_steps (id, flow_id, name, functionality, ordinal, source_node_ids, created_at) VALUES (?, ?, 'Step', 'Execute Reporting', 1, ?, ?)",
            (step_id, flow_id, json.dumps([node_id]), now),
        )
        await db.commit()

        # Mock enricher to fail (ladder exhausted -> no_file=False, LLM_NO_RESPONSE)
        async def mock_enrich_failed(db, snap, units, manifest, provider):
            return {
                step_id: EnrichResult(
                    unit_id=step_id,
                    confirmed_rel_paths=[],
                    no_file=False,
                    reason="LLM_NO_RESPONSE",
                    agent_raw=None,
                    retry_count=0,
                )
            }

        monkeypatch.setattr("domain.business_flow_integrity._verifier.enrich_unit_bindings", mock_enrich_failed)

        # The unit abstains before ever reaching verifier/matcher (empty rel_paths), so
        # no chat_stream_events stub is needed for the verifier stage itself.
        await run_source_aware_verdicts(db, cluster_id, snap_id, provider_id="dummy-provider")

        async with db.execute(
            "SELECT verdict, reason, evidence_json FROM business_unit_verdicts WHERE cluster_id=? AND unit_id=?",
            (cluster_id, step_id),
        ) as cur:
            row = await cur.fetchone()

        assert row["verdict"] == "UNKNOWN"
        evidence = json.loads(row["evidence_json"])
        assert evidence["reason_codes"] == ["UNRESOLVED_ASSET"]
        assert "NO_FILE_IN_SNAPSHOT" not in row["reason"]
    finally:
        await close_db()


@pytest.mark.asyncio
async def test_binding_resolver_extractor_duplicate_unit_id_forces_retry(monkeypatch):
    """FIX 7: the extractor agent's _parse must reject a duplicated unit_id the same way the main
    verifier's does -- a naive set comparison would let [A, A] silently pass for expected {A, B}."""
    import asyncio

    from domain.business_flow_integrity._binding_resolver import _evaluate_extractor_batch
    from domain.model_connector.service import ProviderConfigService

    batch_units = [
        {"unit_id": "u-a", "unit_kind": "step", "prose": "Step A", "label": "A", "binding": "A", "binding_type": None},
        {"unit_id": "u-b", "unit_kind": "step", "prose": "Step B", "label": "B", "binding": "B", "binding_type": None},
    ]

    efforts_seen: list[str] = []

    async def stub(self, request):
        efforts_seen.append(request.reasoning_effort)
        if request.reasoning_effort == "high":
            results = [
                {"unit_id": "u-a", "candidates": [], "no_file": True, "reason": "dup 1"},
                {"unit_id": "u-a", "candidates": [], "no_file": True, "reason": "dup 2"},
            ]
        else:
            results = [
                {"unit_id": "u-a", "candidates": [], "no_file": True, "reason": "ok"},
                {"unit_id": "u-b", "candidates": [], "no_file": True, "reason": "ok"},
            ]
        yield {"type": "content", "text": json.dumps({"results": results})}

    monkeypatch.setattr(ProviderConfigService, "chat_stream_events", stub)

    res, retry_count = await _evaluate_extractor_batch(batch_units, "system prompt", "dummy-provider", asyncio.Semaphore(5))

    assert efforts_seen == ["high", "low"]  # exactly one retry, coverage recovered
    assert set(res.keys()) == {"u-a", "u-b"}
