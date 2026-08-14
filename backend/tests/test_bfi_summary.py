"""Tests for Phase 3 Business Flow Integrity Executive Summary Generation (TICKET P3-9, P3-10).

OFFLINE ONLY — stubbed chat_stream_events, no real LLM network calls.
"""
from pathlib import Path
import pytest

from domain.business_flow_integrity import (
    align_bd_to_code,
    build_code_flow,
    generate_executive_summary,
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


@pytest.mark.skipif(not (BD_FILE.is_file() and SOURCE_DIR.is_dir()), reason=SKIP_REASON)
@pytest.mark.asyncio
async def test_executive_summary_deterministic_fallback(tmp_path, monkeypatch):
    """Test executive summary generation with provider_id=None returns deterministic template with concrete examples."""
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

        doc = parse_markdown_report("EMT.BD-HSBMENU5.report.md", BD_FILE.read_text(encoding="utf-8"), "sha_p3_10")
        cluster_id = f"cluster-{new_id()}"
        await _save_bd_flow(db, [doc], cluster_id)

        await build_code_flow(db, snap_id)
        await align_bd_to_code(db, cluster_id, snap_id)
        await run_flow_verdicts(db, cluster_id, snap_id, provider_id=None)

        # 1. Test UNKNOWN findings enrichment (TICKET P3-10 FIX 1)
        findings = await get_flow_integrity_findings(db, cluster_id, snap_id)
        unknown_findings = findings["unknown_findings"]
        assert len(unknown_findings) > 0
        for uf in unknown_findings:
            assert "bd_reference" in uf
            assert uf["bd_reference"], "bd_reference must not be empty"
            assert "cannot be verified (UNKNOWN)" in uf["reason"]

        # 2. Test Fallback Summary Concrete Examples (TICKET P3-10 FIX 2)
        res = await generate_executive_summary(db, cluster_id, snap_id, provider_id=None)

        # P5-REVIEW-FIXES FIX 6: overall_verdict is now clamped deterministically and requires
        # ZERO broken/contradiction units to PASS — this fixture has 2 real stale_missing
        # contradictions, so the honest verdict is FAIL even though calibration_pass is True.
        assert res["overall_verdict"] == "FAIL"
        # Re-baselined 81.8% -> 83.3% (10/12): the _resolve.py PROGRAM/STEP fix resolves one more program ref.
        assert "calibration is 83.3%" in res["headline"]
        assert len(res["key_risks"]) >= 2
        # Assert key_risks cites resolved code paths for contradictions
        risks_text = " ".join(res["key_risks"])
        assert "PHNIKLOT" in risks_text or "HND2UP5J" in risks_text

        # Assert coverage_note names concrete unknown references
        assert "e.g." in res["coverage_note"]
        assert "HNDX270N" in res["coverage_note"] or "referenced in BD" in res["coverage_note"]
        assert "Align BD documentation" in res["recommendation"]

    finally:
        await close_db()


@pytest.mark.skipif(not (BD_FILE.is_file() and SOURCE_DIR.is_dir()), reason=SKIP_REASON)
@pytest.mark.asyncio
async def test_executive_summary_stubbed_provider(tmp_path, monkeypatch):
    """Test LLM executive summary call with stubbed ProviderConfigService (TICKET P3-10).

    Asserts:
    1. System prompt instructs 'Respond ONLY in English' and 'Cite specific BD references'
    2. Prompt payload contains 'unknown_examples' list and contradiction 'code_fact'
    3. Prompt contains NO raw source code
    4. Bounded reasoning and json_mode are set
    """
    from domain.model_connector.service import ProviderConfigService

    db_dir = tmp_path / "db"
    db_dir.mkdir()
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(db_dir))
    await init_db()

    captured_requests = []

    async def stub_chat_stream_events(self, request):
        captured_requests.append(request)
        json_resp = (
            '{\n'
            '  "overall_verdict": "PASS",\n'
            '  "headline": "Stubbed LLM: Flow integrity calibrated successfully.",\n'
            '  "key_risks": ["Contradiction at Line 35: PHNIKLOT.clist is resolved in code graph."],\n'
            '  "coverage_note": "199 unindexed BD micro-steps including HNDX270N.",\n'
            '  "recommendation": "Update BD documentation."\n'
            '}'
        )
        yield {"type": "content", "text": json_resp}

    monkeypatch.setattr(ProviderConfigService, "chat_stream_events", stub_chat_stream_events)

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

        doc = parse_markdown_report("EMT.BD-HSBMENU5.report.md", BD_FILE.read_text(encoding="utf-8"), "sha_p3_10")
        cluster_id = f"cluster-{new_id()}"
        await _save_bd_flow(db, [doc], cluster_id)

        await build_code_flow(db, snap_id)
        await align_bd_to_code(db, cluster_id, snap_id)
        await run_flow_verdicts(db, cluster_id, snap_id, provider_id=None)

        res = await generate_executive_summary(db, cluster_id, snap_id, provider_id="stub-provider")

        # P5-REVIEW-FIXES FIX 6: overall_verdict is clamped deterministically and overrides the
        # stubbed LLM's "PASS" — this fixture has 2 real BROKEN contradictions, so FAIL is correct.
        assert res["overall_verdict"] == "FAIL"
        assert res["headline"] == "Stubbed LLM: Flow integrity calibrated successfully."

        assert len(captured_requests) == 1
        req = captured_requests[0]

        # Assert bounded reasoning & json_mode
        assert req.reasoning_effort == "low"
        assert req.json_mode is True

        system_msg = req.messages[0].content
        assert "Respond ONLY in English." in system_msg
        assert "Cite specific BD references and source files" in system_msg

        user_msg = req.messages[1].content
        assert "unknown_examples" in user_msg
        assert "code_fact" in user_msg

        # Assert prompt contains no raw source code
        assert "IDENTIFICATION DIVISION" not in user_msg
        assert "PROCEDURE DIVISION" not in user_msg
        assert "COBOL" not in user_msg

    finally:
        await close_db()


# ---------------------------------------------------------------------------
# TICKET P5-REVIEW-FIXES acceptance tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_overall_verdict_clamped_to_fail_despite_lying_llm(tmp_path, monkeypatch):
    """FIX 6: calibration FAIL (below pass band + real BROKEN units) but the LLM's own
    overall_verdict says "PASS" -- the persisted, returned overall_verdict must be the
    deterministic "FAIL", never the LLM's free-form string."""
    from domain.business_flow_integrity._summary import load_executive_summary
    from domain.model_connector.service import ProviderConfigService

    db_dir = tmp_path / "db"
    db_dir.mkdir()
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(db_dir))
    await init_db()

    async def lying_stub(self, request):
        json_resp = (
            '{"overall_verdict": "PASS", "headline": "Everything is fine.", '
            '"key_risks": [], "coverage_note": "n/a", "recommendation": "n/a"}'
        )
        yield {"type": "content", "text": json_resp}

    monkeypatch.setattr(ProviderConfigService, "chat_stream_events", lying_stub)

    try:
        db = get_db()
        cluster_id = f"cluster-{new_id()}"
        snap_id = f"snap-{new_id()}"

        # 3 MATCH + 2 BROKEN -> match% = 60% (below the 80% pass band) AND broken_count=2>0:
        # both conditions independently demand FAIL.
        verdict_tuples = [
            (f"verdict:{new_id()}", cluster_id, snap_id, f"edge-{i}", "[]", "MATCH", "CLASS_MATCH", None, "ok", "{}", 1, utc_now_iso())
            for i in range(3)
        ] + [
            (f"verdict:{new_id()}", cluster_id, snap_id, f"edge-broken-{i}", "[]", "BROKEN", None, "stale_missing", "contradicted", "{}", 1, utc_now_iso())
            for i in range(2)
        ]
        await db.executemany(
            "INSERT INTO flow_verdicts (id, cluster_id, snapshot_id, bd_edge_id, code_subpath_json, verdict, guard_verdict, ai_bucket, reason, evidence_json, comparator_version, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            verdict_tuples,
        )
        await db.commit()

        findings = await get_flow_integrity_findings(db, cluster_id, snap_id)
        assert findings["calibration"]["calibration_pass"] is False
        assert findings["calibration"]["broken_count"] == 2

        res = await generate_executive_summary(db, cluster_id, snap_id, provider_id="stub-provider")
        assert res["overall_verdict"] == "FAIL"
        assert res["headline"] == "Everything is fine."  # LLM's explanation text is kept as-is

        persisted = await load_executive_summary(db, cluster_id, snap_id)
        assert persisted is not None
        assert persisted["overall_verdict"] == "FAIL"
    finally:
        await close_db()
