"""Tests for Phase 3 Business Flow Integrity Outputs & Findings Endpoints (Ticket P3-3-FIX).

OFFLINE ONLY — no real LLM or network calls.
"""
import inspect
import json
from pathlib import Path
import pytest

from domain.business_flow_integrity import (
    _queries,
    align_bd_to_code,
    build_code_flow,
    get_e2e_flow_map,
    get_flow_integrity_findings,
    run_flow_verdicts,
)
from domain.doc_graph._markdown_parser import parse_markdown_report
from domain.doc_graph.service._build import _save_bd_flow
from domain.manifest.service import ManifestService
from domain.manifest.types import BuildManifestRequest
from domain.structural_graph.service import StructuralGraphService
from domain.structural_graph.types import BuildGraphRequest
from infrastructure.db.database import close_db, get_db, init_db
from shared.utils import new_id, utc_now_iso

BD_FILE = Path(r"d:\Emt\emt_data\input_emt\EMT.BD-HSBMENU5.report.md")
SOURCE_DIR = Path(r"d:\Emt\emt_data\input_emt\Source_HSBMENU5")
SKIP_REASON = "Real BD file or HSBMENU5 source directory absent"


def test_no_hardcoded_sample_literals_in_queries():
    """FIX 3 assertion: verify query module contains no sample-specific string literal 'hnd2up1j'."""
    source_text = inspect.getsource(_queries)
    assert "hnd2up1j" not in source_text.lower()


@pytest.mark.skipif(not (BD_FILE.is_file() and SOURCE_DIR.is_dir()), reason=SKIP_REASON)
@pytest.mark.asyncio
async def test_bfi_outputs_oracle_hsbmens5(tmp_path, monkeypatch):
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(db_dir))
    await init_db()

    try:
        db = get_db()
        repo_id = f"repo-{new_id()}"
        snap_id = f"snap-{new_id()}"

        await db.execute(
            "INSERT INTO repo_snapshots (id, local_repo_id, local_path, synced_at, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (snap_id, repo_id, str(SOURCE_DIR), utc_now_iso(), utc_now_iso()),
        )
        await db.commit()

        # Step 1: Build Manifest & Structural Graph
        await ManifestService().build(BuildManifestRequest(snapshot_id=snap_id))
        await StructuralGraphService().build(BuildGraphRequest(snapshot_id=snap_id, force_rebuild=True))

        # Step 2: Build BD Flow cluster
        doc = parse_markdown_report("EMT.BD-HSBMENU5.report.md", BD_FILE.read_text(encoding="utf-8"), "sha_p3_3")
        cluster_id = f"cluster-{new_id()}"
        await _save_bd_flow(db, [doc], cluster_id)

        # Step 3: Build Code Flow & Run Alignment & Verdicts
        await build_code_flow(db, snap_id)
        await align_bd_to_code(db, cluster_id, snap_id)
        await run_flow_verdicts(db, cluster_id, snap_id, provider_id=None)

        # Step 4: Test get_e2e_flow_map output shape & code route edges (FIX 1)
        flow_map = await get_e2e_flow_map(db, cluster_id, snap_id)
        assert flow_map["cluster_id"] == cluster_id
        assert flow_map["snapshot_id"] == snap_id

        nodes = flow_map["nodes"]
        edges = flow_map["edges"]

        code_edges = [e for e in edges if e.get("data", {}).get("is_code_edge")]
        assert len(code_edges) > 0, "E2E map must carry code route edges"

        # Assert no floating CODE_ONLY node (every CODE_ONLY node has at least one incident edge)
        code_only_node_ids = {n["id"] for n in nodes if n.get("data", {}).get("tag") == "CODE_ONLY"}
        incident_node_ids = {e["source"] for e in edges} | {e["target"] for e in edges}
        for co_id in code_only_node_ids:
            assert co_id in incident_node_ids, f"CODE_ONLY node {co_id} must have incident edges"

        # Assert aligned code nodes are deduplicated (no code_node emitted when aligned to BD node)
        code_node_ids = {n["id"] for n in nodes if n["id"].startswith("flow:code_node:")}
        for cid in code_node_ids:
            assert cid not in code_to_bd_map, f"Code node {cid} should have been skipped as it is aligned to BD node"

        # Assert each edge data carries evidence and code_subpath (TICKET P3-5 FIX 2)
        bd_flow_edges = [e for e in edges if not e.get("data", {}).get("is_code_edge")]
        for e in bd_flow_edges:
            e_data = e.get("data", {})
            assert "evidence" in e_data, "Edge data must carry evidence"
            assert "code_subpath" in e_data, "Edge data must carry code_subpath"

        # Assert aligned BD nodes carry their resolved rel_path (TICKET P3-5 FIX 2)
        aligned_bd_nodes = [n for n in nodes if n.get("data", {}).get("tag") == "DOC_MATCHED"]
        for n in aligned_bd_nodes:
            n_data = n.get("data", {})
            assert "rel_path" in n_data and n_data["rel_path"], "Aligned BD node must carry rel_path"
            assert "match_method" in n_data, "Aligned BD node must carry match_method"

        # Step 5: Test get_flow_integrity_findings output shape & oracle entries
        findings = await get_flow_integrity_findings(db, cluster_id, snap_id)

        cal = findings["calibration"]
        assert 80.0 <= cal["match_percentage"] < 100.0, f"Match %: {cal['match_percentage']}"
        # Re-baselined 11 -> 12: the _resolve.py fix (PROGRAM/STEP no longer COBOL-only) lets more
        # program refs (e.g. PHNIXLOT.clist) DOC_MATCH, so one more unit is now resolved. total_units
        # (214) and stale_missing (2) are unchanged; calibration stays in-band.
        assert cal["resolved_units"] == 12
        assert cal["total_units"] == 214
        assert cal["calibration_pass"] is True
        assert cal["ai_bucket_counts"]["stale_missing"] == 2

        # Check Broken/Unknown findings list (must NOT contain 199 UNKNOWN rows)
        broken_list = findings["broken_unknown_findings"]
        assert len(broken_list) < 20  # Actionable rows only, not 199
        assert findings["collapsed_unknown_count"] > 100

        # Check ground-truth stale_missing findings exist
        stale_reasons = [f["reason"] for f in broken_list if f.get("ai_bucket") == "stale_missing"]
        assert len(stale_reasons) == 2

        # Check Code-Only findings list
        code_only_list = findings["code_only_findings"]
        code_only_paths = [f["rel_path"] for f in code_only_list]
        assert "HNIKLOT.cbl" in code_only_paths
        assert "FHNIKLOT.ipf" in code_only_paths
        assert "HNDK001N.jcl" in code_only_paths

        # Check Recovery Gaps list (must be empty for HSBMENU5)
        assert len(findings["recovery_gaps"]) == 0

    finally:
        await close_db()


@pytest.mark.asyncio
async def test_calibration_fails_at_100_percent_match(tmp_path, monkeypatch):
    """FIX 2 unit test: synthetic run with 100% match MUST result in calibration_pass is False."""
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(db_dir))
    await init_db()

    try:
        db = get_db()
        cluster_id = f"cluster-{new_id()}"
        snap_id = f"snap-{new_id()}"

        # Insert 5 synthetic MATCH verdicts
        verdict_tuples = [
            (
                f"verdict:{new_id()}", cluster_id, snap_id, f"edge-{i}", "[]",
                "MATCH", "CLASS_MATCH", None, "Faithful match", "{}", 1, utc_now_iso()
            )
            for i in range(5)
        ]
        await db.executemany(
            "INSERT INTO flow_verdicts (id, cluster_id, snapshot_id, bd_edge_id, code_subpath_json, verdict, guard_verdict, ai_bucket, reason, evidence_json, comparator_version, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            verdict_tuples,
        )
        await db.commit()

        findings = await get_flow_integrity_findings(db, cluster_id, snap_id)
        cal = findings["calibration"]

        assert cal["match_percentage"] == 100.0
        assert cal["calibration_pass"] is False, "Calibration MUST fail at 100.0% (SPEC §9)"

        # TICKET P4-4: a cluster with no bd_business_flows rows must NOT get a (falsely-truthy)
        # all-zero business payload — findings["business"] must be None so the frontend's "not
        # built yet" banner fires and generate_executive_summary falls back to the legacy path.
        assert findings["business"] is None

    finally:
        await close_db()


@pytest.mark.skipif(not (BD_FILE.is_file() and SOURCE_DIR.is_dir()), reason=SKIP_REASON)
@pytest.mark.asyncio
async def test_flow_integrity_run_endpoint_orchestration(tmp_path, monkeypatch):
    """Test POST /api/doc-graph/flow-integrity/{cluster_id}/{snapshot_id}/run endpoint orchestration."""
    from api.doc_graph import run_flow_integrity_pipeline, FlowIntegrityRunBody

    db_dir = tmp_path / "db"
    db_dir.mkdir()
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(db_dir))
    await init_db()

    try:
        db = get_db()
        repo_id = f"repo-{new_id()}"
        snap_id = f"snap-{new_id()}"

        await db.execute(
            "INSERT INTO repo_snapshots (id, local_repo_id, local_path, synced_at, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (snap_id, repo_id, str(SOURCE_DIR), utc_now_iso(), utc_now_iso()),
        )
        await db.commit()

        await ManifestService().build(BuildManifestRequest(snapshot_id=snap_id))
        await StructuralGraphService().build(BuildGraphRequest(snapshot_id=snap_id, force_rebuild=True))

        doc = parse_markdown_report("EMT.BD-HSBMENU5.report.md", BD_FILE.read_text(encoding="utf-8"), "sha_p3_4")
        cluster_id = f"cluster-{new_id()}"
        await _save_bd_flow(db, [doc], cluster_id)

        # Trigger run endpoint (provider_id=None -> deterministic only)
        res = await run_flow_integrity_pipeline(cluster_id, snap_id, body=FlowIntegrityRunBody(provider_id=None))

        cal = res["calibration"]
        assert 80.0 <= cal["match_percentage"] < 100.0
        # Re-baselined 11 -> 12 (see _resolve.py PROGRAM/STEP fix — more program refs now DOC_MATCH).
        assert cal["resolved_units"] == 12
        assert cal["total_units"] == 214
        assert cal["calibration_pass"] is True
        assert cal["ai_bucket_counts"]["stale_missing"] == 2

    finally:
        await close_db()


@pytest.mark.asyncio
async def test_p5_4_evidence_payload_integration(tmp_path, monkeypatch):
    """TICKET P5-4: Assert get_flow_integrity_findings surfaces citations (with fetched_text), aspects, reason, and whitelisted reason_codes."""
    import json
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(db_dir))
    await init_db()

    try:
        db = get_db()
        cluster_id = f"cluster-{new_id()}"
        snap_id = f"snap-{new_id()}"
        flow_id = f"flow-{new_id()}"
        step_id = f"step-{new_id()}"
        run_id = f"run-{new_id()}"

        # 1. Insert mock BD business flow & step
        await db.execute(
            "INSERT INTO bd_business_flows (id, cluster_id, doc_id, sub_ix, block_key, name, description, ordinal, origin, created_at) "
            "VALUES (?, ?, 'doc-1', 2, 'FLOW1', 'Test Flow', 'Desc', 1, 'manual', ?)",
            (flow_id, cluster_id, utc_now_iso()),
        )
        await db.execute(
            "INSERT INTO bd_business_steps (id, flow_id, name, functionality, ordinal, source_node_ids, doc_line_start, doc_line_end, created_at) "
            "VALUES (?, ?, 'Test Step', 'Does something', 1, '[]', 10, 20, ?)",
            (step_id, flow_id, utc_now_iso()),
        )


        # 2. Insert flow verdict (for calibration metrics calculation)
        await db.execute(
            "INSERT INTO flow_verdicts (id, cluster_id, snapshot_id, bd_edge_id, code_subpath_json, verdict, guard_verdict, ai_bucket, reason, evidence_json, comparator_version, created_at) "
            "VALUES (?, ?, ?, 'edge-1', '[]', 'MATCH', 'CLASS_MATCH', NULL, 'Edge ok', '{}', 1, ?)",
            (f"verdict:{new_id()}", cluster_id, snap_id, utc_now_iso()),
        )

        # 3. Insert business unit verdict with evidence_json (carrying whitelisted + non-whitelisted reason codes)
        evidence_json = json.dumps({
            "run_id": run_id,
            "reason_codes": ["NO_FILE_IN_SNAPSHOT", "FREEFORM_LLM_NOTE_123", "UNRESOLVED_ASSET"],
            "citations": [
                {"rel_path": "COBOL/TEST.cbl", "line_start": 5, "line_end": 15, "valid": True}
            ],
        })
        await db.execute(
            "INSERT INTO business_unit_verdicts (id, cluster_id, snapshot_id, unit_id, unit_kind, mapping_status, mapping_method, route_segment_json, verdict, guard_verdict, ai_bucket, reason, evidence_json, created_at) "
            "VALUES (?, ?, ?, ?, 'step', 'MAPPED', 'STRUCTURAL', '{\"bindings\":[\"TEST.cbl\"],\"rel_paths\":[\"COBOL/TEST.cbl\"]}', 'MATCH', 'CLASS_MATCH', NULL, 'Verified source code alignment', ?, ?)",
            (f"buv:{new_id()}", cluster_id, snap_id, step_id, evidence_json, utc_now_iso()),
        )

        # 4. Insert bfi_run_artifacts with citation_resolutions (containing fetched_text) and model_raw_output.aspects
        art_payload = json.dumps({
            "run_id": run_id,
            "unit_id": step_id,
            "citation_resolutions": [
                {
                    "rel_path": "COBOL/TEST.cbl",
                    "line_start": 5,
                    "line_end": 15,
                    "valid": True,
                    "fetched_text": "000500 DISPLAY 'HELLO WORLD'.\n000600 MOVE 1 TO RETURN-CODE.",
                }
            ],
            "model_raw_output": {
                "aspects": {
                    "target_reachable": "YES",
                    "guard_equivalence": "YES",
                    "route_order": "YES",
                    "negative_modality": "NOT_APPLICABLE",
                }
            },
        })
        await db.execute(
            "INSERT INTO bfi_run_artifacts (id, run_id, cluster_id, snapshot_id, unit_id, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (f"art:{new_id()}", run_id, cluster_id, snap_id, step_id, art_payload, utc_now_iso()),
        )

        await db.commit()

        # 5. Execute findings query and verify payload fields
        findings = await get_flow_integrity_findings(db, cluster_id, snap_id)
        assert findings["business"] is not None
        biz = findings["business"]

        matched = biz["matched_units"]
        assert len(matched) == 1
        unit = matched[0]

        # Assert citations
        assert "citations" in unit
        assert len(unit["citations"]) == 1
        cit = unit["citations"][0]
        assert cit["rel_path"] == "COBOL/TEST.cbl"
        assert cit["line_start"] == 5
        assert cit["line_end"] == 15
        assert cit["fetched_text"] == "000500 DISPLAY 'HELLO WORLD'.\n000600 MOVE 1 TO RETURN-CODE."

        # Assert aspects
        assert "aspects" in unit
        assert unit["aspects"] == {
            "target_reachable": "YES",
            "guard_equivalence": "YES",
            "route_order": "YES",
            "negative_modality": "NOT_APPLICABLE",
        }

        # Assert reason
        assert unit["reason"] == "Verified source code alignment"

        # Assert whitelisted reason codes filter
        assert "reason_codes" in unit
        assert "NO_FILE_IN_SNAPSHOT" in unit["reason_codes"]
        assert "UNRESOLVED_ASSET" in unit["reason_codes"]
        assert "FREEFORM_LLM_NOTE_123" not in unit["reason_codes"]

    finally:
        await close_db()


# ---------------------------------------------------------------------------
# TICKET P5-REVIEW-FIXES acceptance tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_artifacts_joined_by_verdict_run_id_not_newest(tmp_path, monkeypatch):
    """FIX 4: two bfi_run_artifacts rows for the same unit from different run_ids -- the payload
    chosen must match the verdict's evidence_json.run_id, NOT whichever row has the newest
    created_at (the OLD "newest-per-unit" behavior would pick the wrong-run row here since it is
    inserted with a later created_at than the correct one)."""
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(db_dir))
    await init_db()

    try:
        db = get_db()
        cluster_id = f"cluster-{new_id()}"
        snap_id = f"snap-{new_id()}"
        flow_id = f"flow-{new_id()}"
        step_id = f"step-{new_id()}"

        await db.execute(
            "INSERT INTO bd_business_flows (id, cluster_id, doc_id, sub_ix, block_key, name, description, ordinal, origin, created_at) "
            "VALUES (?, ?, 'doc-1', 2, 'FLOW1', 'Test Flow', 'Desc', 1, 'manual', ?)",
            (flow_id, cluster_id, utc_now_iso()),
        )
        await db.execute(
            "INSERT INTO bd_business_steps (id, flow_id, name, functionality, ordinal, source_node_ids, doc_line_start, doc_line_end, created_at) "
            "VALUES (?, ?, 'Test Step', 'Does something', 1, '[]', 10, 20, ?)",
            (step_id, flow_id, utc_now_iso()),
        )

        evidence_json = json.dumps({"run_id": "run-correct", "reason_codes": [], "citations": []})
        await db.execute(
            "INSERT INTO business_unit_verdicts (id, cluster_id, snapshot_id, unit_id, unit_kind, mapping_status, mapping_method, route_segment_json, verdict, guard_verdict, ai_bucket, reason, evidence_json, created_at) "
            "VALUES (?, ?, ?, ?, 'step', 'MAPPED', 'source_aware_llm', NULL, 'MATCH', 'CLASS_MATCH', NULL, 'Verified', ?, ?)",
            (f"buv:{new_id()}", cluster_id, snap_id, step_id, evidence_json, utc_now_iso()),
        )

        payload_correct = json.dumps({
            "run_id": "run-correct",
            "citation_resolutions": [
                {"rel_path": "CORRECT.cbl", "line_start": 1, "line_end": 5, "valid": True, "fetched_text": "CORRECT_MARKER"}
            ],
            "model_raw_output": {"aspects": {"target_reachable": "YES", "guard_equivalence": "NOT_APPLICABLE", "route_order": "YES", "negative_modality": "NOT_APPLICABLE"}},
        })
        payload_wrong = json.dumps({
            "run_id": "run-wrong",
            "citation_resolutions": [
                {"rel_path": "WRONG.cbl", "line_start": 1, "line_end": 5, "valid": True, "fetched_text": "WRONG_MARKER"}
            ],
            "model_raw_output": {"aspects": {"target_reachable": "YES", "guard_equivalence": "NOT_APPLICABLE", "route_order": "YES", "negative_modality": "NOT_APPLICABLE"}},
        })
        # The wrong-run row is inserted with a LATER created_at -- the pre-fix "newest-per-unit"
        # ORDER BY created_at ASC + dict-overwrite logic would have picked this one.
        await db.execute(
            "INSERT INTO bfi_run_artifacts (id, run_id, cluster_id, snapshot_id, unit_id, payload, created_at) VALUES (?, 'run-correct', ?, ?, ?, ?, '2020-01-01T00:00:00Z')",
            (f"art:{new_id()}", cluster_id, snap_id, step_id, payload_correct),
        )
        await db.execute(
            "INSERT INTO bfi_run_artifacts (id, run_id, cluster_id, snapshot_id, unit_id, payload, created_at) VALUES (?, 'run-wrong', ?, ?, ?, ?, '2030-01-01T00:00:00Z')",
            (f"art:{new_id()}", cluster_id, snap_id, step_id, payload_wrong),
        )
        await db.commit()

        findings = await get_flow_integrity_findings(db, cluster_id, snap_id)
        biz = findings["business"]
        assert biz is not None
        matched = biz["matched_units"]
        assert len(matched) == 1
        cit = matched[0]["citations"][0]
        assert cit["fetched_text"] == "CORRECT_MARKER"
        assert cit["rel_path"] == "CORRECT.cbl"
    finally:
        await close_db()


@pytest.mark.asyncio
async def test_per_flow_status_never_matched_with_missing_verdict(tmp_path, monkeypatch):
    """FIX 5: a flow with 1 MATCH + 1 UNKNOWN + 1 step with NO verdict row at all must never report
    MATCHED -- the missing-verdict unit counts as UNKNOWN in the expected set, so status is PARTIAL
    (matched>0, short of full coverage)."""
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(db_dir))
    await init_db()

    try:
        db = get_db()
        cluster_id = f"cluster-{new_id()}"
        snap_id = f"snap-{new_id()}"
        flow_id = f"flow-{new_id()}"
        step_match = f"step-{new_id()}-match"
        step_unknown = f"step-{new_id()}-unknown"
        step_missing = f"step-{new_id()}-missing"
        now = utc_now_iso()

        await db.execute(
            "INSERT INTO bd_business_flows (id, cluster_id, doc_id, sub_ix, block_key, name, description, ordinal, origin, created_at) "
            "VALUES (?, ?, 'doc-1', 2, 'FLOW1', 'Test Flow', 'Desc', 1, 'manual', ?)",
            (flow_id, cluster_id, now),
        )
        await db.executemany(
            "INSERT INTO bd_business_steps (id, flow_id, name, functionality, ordinal, source_node_ids, created_at) VALUES (?, ?, ?, ?, ?, '[]', ?)",
            [
                (step_match, flow_id, "Matched Step", "Does the matched thing.", 1, now),
                (step_unknown, flow_id, "Unknown Step", "Does the unknown thing.", 2, now),
                (step_missing, flow_id, "Missing Step", "No verdict row exists for this step.", 3, now),
            ],
        )
        await db.executemany(
            "INSERT INTO business_unit_verdicts (id, cluster_id, snapshot_id, unit_id, unit_kind, mapping_status, mapping_method, route_segment_json, verdict, guard_verdict, ai_bucket, reason, evidence_json, created_at) "
            "VALUES (?, ?, ?, ?, 'step', 'MAPPED', 'offline', NULL, ?, NULL, NULL, 'reason', '{}', ?)",
            [
                (f"buv:{new_id()}", cluster_id, snap_id, step_match, "MATCH", now),
                (f"buv:{new_id()}", cluster_id, snap_id, step_unknown, "UNKNOWN", now),
                # step_missing intentionally has NO row here.
            ],
        )
        await db.commit()

        findings = await get_flow_integrity_findings(db, cluster_id, snap_id)
        biz = findings["business"]
        assert biz is not None
        flow_rollup = biz["per_flow"][0]

        assert flow_rollup["status"] != "MATCHED"
        assert flow_rollup["status"] == "PARTIAL"
        assert flow_rollup["steps_matched"] == 1
        assert flow_rollup["steps_backed"] == 1  # TICKET P5-UI-FIX: MATCH-or-PARTIAL steps (no PARTIAL here)
        assert flow_rollup["units_unknown"] == 2  # 1 explicit UNKNOWN + 1 missing-verdict
        assert flow_rollup["units_partial"] == 0
        # TICKET P5-UI: additive per-flow coverage fields (steps+branches, not steps-only)
        assert flow_rollup["units_matched"] == 1 and flow_rollup["total_expected"] == 3
    finally:
        await close_db()


@pytest.mark.asyncio
async def test_corrupt_evidence_json_degrades_unit_not_500(tmp_path, monkeypatch):
    """FIX 9: a seeded corrupt evidence_json row must degrade only that unit to UNKNOWN with
    MALFORMED_PERSISTED_ARTIFACT, never raise/500 the whole findings query."""
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(db_dir))
    await init_db()

    try:
        db = get_db()
        cluster_id = f"cluster-{new_id()}"
        snap_id = f"snap-{new_id()}"
        flow_id = f"flow-{new_id()}"
        step_id = f"step-{new_id()}"
        now = utc_now_iso()

        await db.execute(
            "INSERT INTO bd_business_flows (id, cluster_id, doc_id, sub_ix, block_key, name, description, ordinal, origin, created_at) "
            "VALUES (?, ?, 'doc-1', 2, 'FLOW1', 'Test Flow', 'Desc', 1, 'manual', ?)",
            (flow_id, cluster_id, now),
        )
        await db.execute(
            "INSERT INTO bd_business_steps (id, flow_id, name, functionality, ordinal, source_node_ids, created_at) VALUES (?, ?, 'Test Step', 'Does something', 1, '[]', ?)",
            (step_id, flow_id, now),
        )
        # Stored verdict claims MATCH, but its evidence_json is corrupt (not valid JSON).
        await db.execute(
            "INSERT INTO business_unit_verdicts (id, cluster_id, snapshot_id, unit_id, unit_kind, mapping_status, mapping_method, route_segment_json, verdict, guard_verdict, ai_bucket, reason, evidence_json, created_at) "
            "VALUES (?, ?, ?, ?, 'step', 'MAPPED', 'source_aware_llm', NULL, 'MATCH', 'CLASS_MATCH', NULL, 'Verified', '{not valid json', ?)",
            (f"buv:{new_id()}", cluster_id, snap_id, step_id, now),
        )
        await db.commit()

        findings = await get_flow_integrity_findings(db, cluster_id, snap_id)  # must not raise

        biz = findings["business"]
        assert biz is not None
        assert len(biz["matched_units"]) == 0
        unknown = biz["unknown_units"]
        assert len(unknown) == 1
        assert unknown[0]["unit_id"] == step_id
        assert unknown[0]["verdict"] == "UNKNOWN"
        assert "MALFORMED_PERSISTED_ARTIFACT" in unknown[0]["reason_codes"]

        flow_rollup = biz["per_flow"][0]
        assert flow_rollup["status"] != "MATCHED"
        assert flow_rollup["units_unknown"] == 1
    finally:
        await close_db()


@pytest.mark.asyncio
async def test_partial_step_and_branch_units_not_dropped_from_findings(tmp_path, monkeypatch):
    """TICKET P5-UI-FIX2: a PARTIAL step-unit (and branch-unit) must still render -- the bucketing
    used to be if MATCH / elif BROKEN / elif UNKNOWN with no PARTIAL branch, so a PARTIAL unit was
    appended to no list and silently vanished from the findings payload. It must now land in
    unknown_units with verdict "PARTIAL" (never dropped, never miscounted as matched/contradicted)."""
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(db_dir))
    await init_db()

    try:
        db = get_db()
        cluster_id = f"cluster-{new_id()}"
        snap_id = f"snap-{new_id()}"
        flow_id = f"flow-{new_id()}"
        step_match = f"step-{new_id()}-match"
        step_partial = f"step-{new_id()}-partial"
        now = utc_now_iso()

        await db.execute(
            "INSERT INTO bd_business_flows (id, cluster_id, doc_id, sub_ix, block_key, name, description, ordinal, origin, created_at) "
            "VALUES (?, ?, 'doc-1', 2, 'FLOW1', 'Test Flow', 'Desc', 1, 'manual', ?)",
            (flow_id, cluster_id, now),
        )
        await db.executemany(
            "INSERT INTO bd_business_steps (id, flow_id, name, functionality, ordinal, source_node_ids, created_at) VALUES (?, ?, ?, ?, ?, '[]', ?)",
            [
                (step_match, flow_id, "Matched Step", "Does the matched thing.", 1, now),
                (step_partial, flow_id, "Partial Step", "Does the partially-backed thing.", 2, now),
            ],
        )
        await db.executemany(
            "INSERT INTO business_unit_verdicts (id, cluster_id, snapshot_id, unit_id, unit_kind, mapping_status, mapping_method, route_segment_json, verdict, guard_verdict, ai_bucket, reason, evidence_json, created_at) "
            "VALUES (?, ?, ?, ?, 'step', 'MAPPED', 'offline', NULL, ?, NULL, NULL, 'reason', '{}', ?)",
            [
                (f"buv:{new_id()}", cluster_id, snap_id, step_match, "MATCH", now),
                (f"buv:{new_id()}", cluster_id, snap_id, step_partial, "PARTIAL", now),
            ],
        )
        await db.commit()

        findings = await get_flow_integrity_findings(db, cluster_id, snap_id)
        biz = findings["business"]
        assert biz is not None

        # The PARTIAL step must appear in unknown_units with verdict "PARTIAL" -- not dropped,
        # not silently absorbed into matched_units or contradicted_units.
        matched_ids = {u["unit_id"] for u in biz["matched_units"]}
        contradicted_ids = {u["unit_id"] for u in biz["contradicted_units"]}
        unknown_by_id = {u["unit_id"]: u for u in biz["unknown_units"]}

        assert step_partial not in matched_ids
        assert step_partial not in contradicted_ids
        assert step_partial in unknown_by_id
        assert unknown_by_id[step_partial]["verdict"] == "PARTIAL"

        assert step_match in matched_ids

        # Per-flow counts already handled PARTIAL correctly before this fix -- confirm unchanged.
        flow_rollup = biz["per_flow"][0]
        assert flow_rollup["units_partial"] == 1
        assert flow_rollup["steps_backed"] == 2  # MATCH + PARTIAL both count as "backed"
        assert flow_rollup["steps_matched"] == 1  # MATCH-only stays MATCH-only
    finally:
        await close_db()

