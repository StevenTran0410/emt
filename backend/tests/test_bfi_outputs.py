"""Tests for Phase 3 Business Flow Integrity Outputs & Findings Endpoints (Ticket P3-3-FIX).

OFFLINE ONLY — no real LLM or network calls.
"""
import inspect
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
        assert cal["resolved_units"] == 11
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
        assert cal["resolved_units"] == 11
        assert cal["total_units"] == 214
        assert cal["calibration_pass"] is True
        assert cal["ai_bucket_counts"]["stale_missing"] == 2

    finally:
        await close_db()
