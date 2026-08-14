"""Acceptance and unit tests for Phase 6 (3-LLM Matching Architecture).

OFFLINE ONLY — all provider calls are mocked stubs.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
import pytest

from domain.business_flow_integrity import (
    EnrichResult,
    MatchResult,
    build_code_flow,
    build_evidence_index,
    enrich_unit_bindings,
    expand_unit_snippets,
    run_source_aware_verdicts,
    run_unit_matching,
)
from domain.business_flow_integrity._enrich import LLMEnrichItem, _evaluate_enrich_batch
from domain.business_flow_integrity._matching import LLMMatchItem, _evaluate_matcher_batch
from domain.model_connector.service import ProviderConfigService
from infrastructure.db.database import close_db, get_db, init_db
from shared.utils import new_id, utc_now_iso


# ---------------------------------------------------------------------------
# Test 1: Enrich Agent tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enrich_drops_wrong_seed_adds_missing_file_and_intersects_manifest(tmp_path, monkeypatch):
    """1. Enrich runs on deterministically-resolved units (non-empty seed) and can:
    - Drop a wrong seed file and add a missing valid file
    - Manifest intersection drops hallucinated paths
    - ladder-None -> no_file=False + LLM_NO_RESPONSE (not no_file=True).
    """
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
        manifest_paths = ["HNIXLOT.cbl", "CORRECT.cbl", "WRONG_SEED.cbl"]
        for p in manifest_paths:
            (source_dir / p).write_text(f"/* {p} */\n", encoding="utf-8")
            await db.execute(
                "INSERT INTO manifest_files (id, snapshot_id, rel_path, language, category, size_bytes, mtime_ns, checksum) "
                "VALUES (?, ?, ?, 'cobol', 'source', 100, 0, 'hash')",
                (f"man-{p}", snap_id, p),
            )
        await db.commit()

        u1 = {
            "unit_id": "u-1",
            "unit_kind": "step",
            "prose": "Step 1 does Correct processing",
            "label": "Step 1",
            "binding": "WRONG_SEED",
            "binding_type": "PROGRAM",
            "rel_paths": ["WRONG_SEED.cbl"],  # Non-empty seed that is wrong
        }
        u2 = {
            "unit_id": "u-2",
            "unit_kind": "step",
            "prose": "Step 2 calls External Service",
            "label": "Step 2",
            "binding": "EXTERNAL_SVC",
            "binding_type": None,
            "rel_paths": [],
        }

        # Mock _evaluate_enrich_batch
        async def mock_enrich_batch(batch_units, system_prompt, provider_id, semaphore):
            res = {}
            for u in batch_units:
                if u["unit_id"] == "u-1":
                    # Propose dropping WRONG_SEED.cbl, adding CORRECT.cbl + 1 hallucinated file
                    res[u["unit_id"]] = LLMEnrichItem(
                        unit_id="u-1",
                        confirmed_rel_paths=["CORRECT.cbl", "HALLUCINATED_FILE.cbl"],
                        no_file=False,
                        reason="Dropped WRONG_SEED.cbl, matched CORRECT.cbl",
                    )
                elif u["unit_id"] == "u-2":
                    res[u["unit_id"]] = LLMEnrichItem(
                        unit_id="u-2",
                        confirmed_rel_paths=[],
                        no_file=True,
                        reason="External service not in manifest",
                    )
            return res, 0

        monkeypatch.setattr("domain.business_flow_integrity._enrich._evaluate_enrich_batch", mock_enrich_batch)

        enrich_res = await enrich_unit_bindings(db, snap_id, [u1, u2], manifest_paths, "dummy-provider")

        # u-1: dropped WRONG_SEED, added CORRECT.cbl, HALLUCINATED_FILE dropped by manifest intersection
        assert "u-1" in enrich_res
        assert enrich_res["u-1"].confirmed_rel_paths == ["CORRECT.cbl"]
        assert enrich_res["u-1"].no_file is False
        assert "CORRECT.cbl" in enrich_res["u-1"].reason

        # u-2: genuine external -> no_file: True
        assert "u-2" in enrich_res
        assert enrich_res["u-2"].confirmed_rel_paths == []
        assert enrich_res["u-2"].no_file is True

        # Test ladder-None exhaustion: transport/stream failure returns empty batch_res
        async def mock_enrich_batch_failed(batch_units, system_prompt, provider_id, semaphore):
            return {}, 0

        monkeypatch.setattr("domain.business_flow_integrity._enrich._evaluate_enrich_batch", mock_enrich_batch_failed)

        enrich_fail = await enrich_unit_bindings(db, snap_id, [u1], manifest_paths, "dummy-provider")
        assert "u-1" in enrich_fail
        assert enrich_fail["u-1"].confirmed_rel_paths == []
        assert enrich_fail["u-1"].no_file is False  # Must NOT be no_file: True!
        assert enrich_fail["u-1"].reason == "LLM_NO_RESPONSE"
    finally:
        await close_db()


# ---------------------------------------------------------------------------
# Test 2: BFS source expansion tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expand_unit_snippets_1hop_bfs_and_caps(tmp_path, monkeypatch):
    """2. expand_unit_snippets: 1-hop BFS adds a called file's snippet that the seed file
    alone would miss; caps/ordering hold; abstain reasons reachable.
    """
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

        # Create 2 files: CALLER.cbl and CALLEE.cbl
        caller_lines = [f"       * CALLER LINE {i:03d}" for i in range(1, 40)]
        caller_lines[19] = "       CALL 'CALLEE'"
        caller_text = "\n".join(caller_lines)
        (source_dir / "CALLER.cbl").write_text(caller_text, encoding="utf-8", newline="\n")

        callee_lines = [f"       * CALLEE LINE {i:03d}" for i in range(1, 40)]
        callee_lines[14] = "       IF STATUS = 'OK' PERFORM 2000-PROCESS"
        callee_text = "\n".join(callee_lines)
        (source_dir / "CALLEE.cbl").write_text(callee_text, encoding="utf-8", newline="\n")

        (source_dir / "MENU.pfd").write_text("/* MENU */\n", encoding="utf-8")
        for p in ("MENU.pfd", "CALLER.cbl", "CALLEE.cbl"):
            await db.execute(
                "INSERT INTO manifest_files (id, snapshot_id, rel_path, language, category, size_bytes, mtime_ns, checksum) "
                "VALUES (?, ?, ?, 'cobol', 'source', 500, 0, 'hash')",
                (new_id(), snap_id, p),
            )

        # Insert source facts for both
        await db.execute(
            "INSERT INTO source_facts (snapshot_id, rel_path, language, fact_type, semantic_key, occurrence_ix, parent_key, name, value, attributes, line_start, line_end, extractor, extractor_ver, created_at) "
            "VALUES (?, 'CALLER.cbl', 'cobol', 'call', 'call/CALLER.MAIN.CALLEE', 0, 'section/CALLER.MAIN', 'CALLEE', NULL, '{}', 20, 20, 'cobol_antlr', '1.0.0', ?)",
            (snap_id, now),
        )
        await db.execute(
            "INSERT INTO source_facts (snapshot_id, rel_path, language, fact_type, semantic_key, occurrence_ix, parent_key, name, value, attributes, line_start, line_end, extractor, extractor_ver, created_at) "
            "VALUES (?, 'CALLEE.cbl', 'cobol', 'branch', 'branch/CALLEE.MAIN#1', 0, 'section/CALLEE.MAIN', 'IF', 'STATUS = OK', '{\"kind\": \"if\"}', 15, 15, 'cobol_antlr', '1.0.0', ?)",
            (snap_id, now),
        )
        await db.commit()

        # Seed structural graph edges and build code flow: MENU.pfd -> CALLER.cbl -> CALLEE.cbl
        await db.execute(
            "INSERT INTO structural_graph_edges (snapshot_id, src_path, dst_path, edge_type, is_external, created_at) "
            "VALUES (?, 'MENU.pfd', 'CALLER.cbl', 'menu_option', 0, ?)",
            (snap_id, now),
        )
        await db.execute(
            "INSERT INTO structural_graph_edges (snapshot_id, src_path, dst_path, edge_type, is_external, created_at) "
            "VALUES (?, 'CALLER.cbl', 'CALLEE.cbl', 'calls', 0, ?)",
            (snap_id, now),
        )
        await db.commit()

        await build_code_flow(db, snap_id)
        await build_evidence_index(db, snap_id)

        # Unit only seeds CALLER.cbl
        unit = {"unit_id": "u-caller", "unit_kind": "step", "rel_paths": ["CALLER.cbl"]}
        res = await expand_unit_snippets(db, snap_id, unit, rel_paths=["CALLER.cbl"])

        assert res.abstain_reason is None
        # 1-hop BFS must have traversed to CALLEE.cbl and loaded snippets from both
        paths_in_snippets = {s.rel_path for s in res.snippets}
        assert "CALLER.cbl" in paths_in_snippets
        assert "CALLEE.cbl" in paths_in_snippets

        # Verify deterministic ordering: sorted by rel_path, line_start
        assert [s.rel_path for s in res.snippets] == sorted([s.rel_path for s in res.snippets])

        # Test abstain reasons:
        unres = await expand_unit_snippets(db, snap_id, {"unit_id": "u-none"}, rel_paths=[])
        assert unres.abstain_reason == "UNRESOLVED_ASSET"

        ext = await expand_unit_snippets(db, snap_id, {"unit_id": "u-ext"}, rel_paths=["__external__/API"])
        assert ext.abstain_reason == "EXTERNAL_TARGET"

        no_occ = await expand_unit_snippets(db, snap_id, {"unit_id": "u-miss"}, rel_paths=["NONEXISTENT.cbl"])
        assert no_occ.abstain_reason == "NO_OCCURRENCE"
    finally:
        await close_db()


# ---------------------------------------------------------------------------
# Test 3: Matcher maps_to_code:false still reaches verifier (corrector)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_matcher_maps_to_code_false_still_reaches_verifier(tmp_path, monkeypatch):
    """3. Matcher maps_to_code:false does NOT gate the unit — the verifier is called as the
    final corrector and can override it to MATCH.
    """
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
        (source_dir / "PROG1.cbl").write_text("       * LINE 1\n       IF A = 1 PERFORM 100\n", encoding="utf-8")
        await db.execute(
            "INSERT INTO manifest_files (id, snapshot_id, rel_path, language, category, size_bytes, mtime_ns, checksum) "
            "VALUES (?, ?, 'PROG1.cbl', 'cobol', 'source', 100, 0, 'hash')",
            (new_id(), snap_id),
        )
        await db.execute(
            "INSERT INTO source_facts (snapshot_id, rel_path, language, fact_type, semantic_key, occurrence_ix, parent_key, name, value, attributes, line_start, line_end, extractor, extractor_ver, created_at) "
            "VALUES (?, 'PROG1.cbl', 'cobol', 'branch', 'branch/PROG1.MAIN#1', 0, 'section/PROG1.MAIN', 'IF', 'A = 1', '{\"kind\": \"if\"}', 2, 2, 'cobol_antlr', '1.0.0', ?)",
            (snap_id, now),
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
            "VALUES (?, ?, 'doc-1', 'program', 'PROG1', 'PROGRAM', 'Step 1', 1, 'P1', ?)",
            (node_id, cluster_id, now),
        )
        step_id = f"step-{new_id()}"
        await db.execute(
            "INSERT INTO bd_business_steps (id, flow_id, name, functionality, ordinal, source_node_ids, created_at) VALUES (?, ?, 'Step 1', 'Does logic in PROG1', 1, ?, ?)",
            (step_id, flow_id, json.dumps([node_id]), now),
        )
        await db.commit()

        # Mock enrich to confirm PROG1.cbl
        async def mock_enrich(db, snap, units, manifest, provider):
            return {
                step_id: EnrichResult(
                    unit_id=step_id,
                    confirmed_rel_paths=["PROG1.cbl"],
                    no_file=False,
                    reason="Confirmed PROG1.cbl",
                    agent_raw={"unit_id": step_id},
                    retry_count=0,
                )
            }

        # Mock matcher to return maps_to_code: false
        async def mock_matcher(batch_units, snippets, provider, semaphore):
            return {
                step_id: MatchResult(
                    unit_id=step_id,
                    subclaims=[],
                    citations=[],
                    maps_to_code=False,
                    reason="Matcher falsely claims no match",
                    agent_raw={"unit_id": step_id, "maps_to_code": False},
                    retry_count=0,
                )
            }, {"retry_count": 0, "latency_ms": 5.0}

        # Verifier MUST be called as corrector!
        from domain.business_flow_integrity._verifier import LLMAspects, LLMCitation, LLMUnitVerification
        verifier_called = []

        async def mock_verifier(batch_units, snippets, matcher_results, provider, semaphore):
            verifier_called.append(True)
            res = {}
            for u in batch_units:
                res[u["unit_id"]] = LLMUnitVerification(
                    unit_id=u["unit_id"],
                    subclaims=["Step logic realized"],
                    aspects=LLMAspects(
                        target_reachable="YES",
                        guard_equivalence="NOT_APPLICABLE",
                        route_order="YES",
                        negative_modality="NOT_APPLICABLE",
                    ),
                    citations=[LLMCitation(rel_path="PROG1.cbl", line_start=2, line_end=2)],
                    verdict="MATCH",
                    reason_codes=["target_reachable_confirmed"],
                    reason="Verifier correctly found logic in PROG1.cbl",
                )
            return res, {"retry_count": 0, "latency_ms": 10.0}

        monkeypatch.setattr("domain.business_flow_integrity._verifier.enrich_unit_bindings", mock_enrich)
        monkeypatch.setattr("domain.business_flow_integrity._verifier._evaluate_matcher_batch", mock_matcher)
        monkeypatch.setattr("domain.business_flow_integrity._verifier._evaluate_verifier_batch", mock_verifier)

        result = await run_source_aware_verdicts(db, cluster_id, snap_id, provider_id="test-provider")

        assert len(verifier_called) == 1, "Verifier MUST be called as corrector even when matcher returned maps_to_code: false"
        assert result.total_units == 1

        async with db.execute(
            "SELECT verdict, reason, evidence_json FROM business_unit_verdicts WHERE cluster_id=? AND unit_id=?",
            (cluster_id, step_id),
        ) as cur:
            row = await cur.fetchone()

        assert row["verdict"] == "MATCH"
        evidence = json.loads(row["evidence_json"])
        assert "target_reachable_confirmed" in evidence["reason_codes"]

        # Check artifact contains verifier_corrected = True
        async with db.execute("SELECT payload FROM bfi_run_artifacts WHERE unit_id=?", (step_id,)) as cur:
            art = json.loads((await cur.fetchone())["payload"])
        assert art["verifier_corrected"] is True
        assert art["matcher_maps_to_code"] is False
    finally:
        await close_db()


# ---------------------------------------------------------------------------
# Test 4: End-to-end offline provider_id=None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_offline_path_writes_enrich_and_match_in_artifacts(tmp_path, monkeypatch):
    """4. End-to-end offline (provider_id=None) writes artifacts with enrich/match
    sub-objects and every unit abstains (no crash).
    """
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
        (source_dir / "PROG1.cbl").write_text("       * LINE 1\n       IF A = 1 PERFORM 100\n", encoding="utf-8")
        await db.execute(
            "INSERT INTO manifest_files (id, snapshot_id, rel_path, language, category, size_bytes, mtime_ns, checksum) "
            "VALUES (?, ?, 'PROG1.cbl', 'cobol', 'source', 100, 0, 'hash')",
            (new_id(), snap_id),
        )
        await db.execute(
            "INSERT INTO source_facts (snapshot_id, rel_path, language, fact_type, semantic_key, occurrence_ix, parent_key, name, value, attributes, line_start, line_end, extractor, extractor_ver, created_at) "
            "VALUES (?, 'PROG1.cbl', 'cobol', 'branch', 'branch/PROG1.MAIN#1', 0, 'section/PROG1.MAIN', 'IF', 'A = 1', '{\"kind\": \"if\"}', 2, 2, 'cobol_antlr', '1.0.0', ?)",
            (snap_id, now),
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
            "VALUES (?, ?, 'doc-1', 'program', 'PROG1', 'PROGRAM', 'Step 1', 1, 'P1', ?)",
            (node_id, cluster_id, now),
        )
        step_id = f"step-{new_id()}"
        await db.execute(
            "INSERT INTO bd_business_steps (id, flow_id, name, functionality, ordinal, source_node_ids, created_at) VALUES (?, ?, 'Step 1', 'Does logic', 1, ?, ?)",
            (step_id, flow_id, json.dumps([node_id]), now),
        )
        await db.commit()

        # Run with provider_id=None (offline)
        result = await run_source_aware_verdicts(db, cluster_id, snap_id, provider_id=None)

        assert result.total_units == 1
        assert result.llm_batches_issued == 0
        assert result.insufficient_count == 1

        async with db.execute("SELECT payload FROM bfi_run_artifacts WHERE cluster_id=?", (cluster_id,)) as cur:
            art_row = await cur.fetchone()

        assert art_row is not None
        payload = json.loads(art_row["payload"])
        assert "enrich" in payload
        assert "match" in payload
        assert payload["enrich"] is None
        assert payload["match"] is None
        assert payload["fused_verdict"] == "INSUFFICIENT_EVIDENCE"
    finally:
        await close_db()


# ---------------------------------------------------------------------------
# Test 5: End-to-end 3-LLM pipeline mock test (MATCH outcome)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_end_to_end_3llm_matching_pipeline_match_outcome(tmp_path, monkeypatch):
    """5. Full 3-LLM pipeline end-to-end:
    - LLM #1 (Enrich) confirms PROG1.cbl
    - Stage 3 (BFS expand) produces snippet for PROG1.cbl lines 1-12
    - LLM #2 (Matcher) matches and cites PROG1.cbl lines 2-2
    - LLM #3 (Verifier) receives exact citation snippet and verifies MATCH
    - Final fused verdict is MATCH.
    """
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
        (source_dir / "PROG1.cbl").write_text("       * LINE 1\n       IF A = 1 PERFORM 100-PROCESS\n       * LINE 3\n", encoding="utf-8", newline="\n")
        await db.execute(
            "INSERT INTO manifest_files (id, snapshot_id, rel_path, language, category, size_bytes, mtime_ns, checksum) "
            "VALUES (?, ?, 'PROG1.cbl', 'cobol', 'source', 100, 0, 'hash')",
            (new_id(), snap_id),
        )
        await db.execute(
            "INSERT INTO source_facts (snapshot_id, rel_path, language, fact_type, semantic_key, occurrence_ix, parent_key, name, value, attributes, line_start, line_end, extractor, extractor_ver, created_at) "
            "VALUES (?, 'PROG1.cbl', 'cobol', 'branch', 'branch/PROG1.MAIN#1', 0, 'section/PROG1.MAIN', 'IF', 'A = 1', '{\"kind\": \"if\"}', 2, 2, 'cobol_antlr', '1.0.0', ?)",
            (snap_id, now),
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
            "VALUES (?, ?, 'doc-1', 'program', 'PROG1', 'PROGRAM', 'Step 1', 1, 'P1', ?)",
            (node_id, cluster_id, now),
        )
        step_id = f"step-{new_id()}"
        await db.execute(
            "INSERT INTO bd_business_steps (id, flow_id, name, functionality, ordinal, source_node_ids, created_at) VALUES (?, ?, 'Step 1', 'Validate A = 1', 1, ?, ?)",
            (step_id, flow_id, json.dumps([node_id]), now),
        )
        await db.commit()

        # Mock enrich
        async def mock_enrich(db, snap, units, manifest, provider):
            return {
                step_id: EnrichResult(
                    unit_id=step_id,
                    confirmed_rel_paths=["PROG1.cbl"],
                    no_file=False,
                    reason="Confirmed PROG1.cbl",
                    agent_raw={"unit_id": step_id},
                    retry_count=0,
                )
            }

        # Mock matcher
        async def mock_matcher(batch_units, snippets, provider, semaphore):
            return {
                step_id: MatchResult(
                    unit_id=step_id,
                    subclaims=["Condition A = 1 checks out."],
                    citations=[{"rel_path": "PROG1.cbl", "line_start": 2, "line_end": 2}],
                    maps_to_code=True,
                    reason="Found branch at line 2",
                    agent_raw={"unit_id": step_id},
                    retry_count=0,
                )
            }, {"retry_count": 0, "latency_ms": 10.0}

        # Mock verifier
        from domain.business_flow_integrity._verifier import LLMAspects, LLMCitation, LLMUnitVerification

        async def mock_verifier(batch_units, snippets, matcher_results, provider, semaphore):
            # Assert snippets received by verifier contain fetched_text from line 2
            assert step_id in snippets
            assert any("IF A = 1" in s.text for s in snippets[step_id])
            assert matcher_results is not None
            assert step_id in matcher_results

            return {
                step_id: LLMUnitVerification(
                    unit_id=step_id,
                    subclaims=["Condition A = 1 checks out."],
                    aspects=LLMAspects(
                        target_reachable="YES",
                        guard_equivalence="YES",
                        route_order="YES",
                        negative_modality="NOT_APPLICABLE",
                    ),
                    citations=[LLMCitation(rel_path="PROG1.cbl", line_start=2, line_end=2)],
                    verdict="MATCH",
                    reason_codes=["match_confirmed"],
                    reason="Verified branch statement matches BD requirement.",
                )
            }, {"retry_count": 0, "latency_ms": 10.0}

        monkeypatch.setattr("domain.business_flow_integrity._verifier.enrich_unit_bindings", mock_enrich)
        monkeypatch.setattr("domain.business_flow_integrity._verifier._evaluate_matcher_batch", mock_matcher)
        monkeypatch.setattr("domain.business_flow_integrity._verifier._evaluate_verifier_batch", mock_verifier)

        result = await run_source_aware_verdicts(db, cluster_id, snap_id, provider_id="test-provider")

        assert result.total_units == 1
        assert result.match_count == 1

        async with db.execute(
            "SELECT verdict, guard_verdict, evidence_json FROM business_unit_verdicts WHERE cluster_id=? AND unit_id=?",
            (cluster_id, step_id),
        ) as cur:
            row = await cur.fetchone()

        assert row["verdict"] == "MATCH"
        assert row["guard_verdict"] == "CLASS_MATCH"

        # Check run artifacts payload
        async with db.execute("SELECT payload FROM bfi_run_artifacts WHERE cluster_id=?", (cluster_id,)) as cur:
            art_row = await cur.fetchone()

        payload = json.loads(art_row["payload"])
        assert payload["enrich"]["confirmed_rel_paths"] == ["PROG1.cbl"]
        assert payload["match"]["maps_to_code"] is True
        assert payload["fused_verdict"] == "MATCH"
        assert len(payload["citation_resolutions"]) == 1
        assert payload["citation_resolutions"][0]["valid"] is True
    finally:
        await close_db()
