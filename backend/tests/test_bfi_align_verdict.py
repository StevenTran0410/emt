"""Tests for Stage A Alignment, Stage B LLM Verdict Engine, and Calibration (Ticket P3-2-FIX).

OFFLINE ONLY — no real LLM or network calls.
"""
import json
from pathlib import Path
import pytest

from domain.business_flow_integrity import (
    align_bd_to_code,
    build_code_flow,
    run_flow_verdicts,
)
from domain.business_flow_integrity._verdict import (
    _enforce_unknown_dominance,
    _is_value_comparison_reason,
)
from domain.doc_graph._markdown_parser import parse_markdown_report
from domain.doc_graph.service._build import _save_bd_flow
from domain.manifest.service import ManifestService
from domain.manifest.types import BuildManifestRequest
from domain.model_connector.service import ProviderConfigService
from domain.structural_graph.service import StructuralGraphService
from domain.structural_graph.types import BuildGraphRequest
from infrastructure.db.database import close_db, get_db, init_db
from shared.utils import new_id, utc_now_iso

BD_FILE = Path(r"d:\Emt\emt_data\input_emt\EMT.BD-HSBMENU5.report.md")
SOURCE_DIR = Path(r"d:\Emt\emt_data\input_emt\Source_HSBMENU5")
SKIP_REASON = "Real BD file or HSBMENU5 source directory absent"


# ---------------------------------------------------------------------------
# 1. Unit Tests for Guard-Class Gate and UNKNOWN Dominance
# ---------------------------------------------------------------------------


def test_guard_validator_gate_rejects_numeric_value_comparison():
    assert _is_value_comparison_reason("Numeric value 21 != 0 is wrong") is True
    assert _is_value_comparison_reason("Value equality check failed on return code") is True
    assert _is_value_comparison_reason("Order of execution matches at route level") is False


def test_unknown_dominance_rule():
    assert _enforce_unknown_dominance("BROKEN", is_external_or_unresolved=True) == "UNKNOWN"
    assert _enforce_unknown_dominance("BROKEN", is_external_or_unresolved=False) == "BROKEN"
    assert _enforce_unknown_dominance("MATCH", is_external_or_unresolved=True) == "MATCH"


# ---------------------------------------------------------------------------
# 2. Stage A Alignment & Stage B Verdict Real Pipeline Oracle Test (SPEC §10)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not (BD_FILE.is_file() and SOURCE_DIR.is_dir()), reason=SKIP_REASON)
@pytest.mark.asyncio
async def test_bfi_alignment_verdict_oracle_hsbmens5(tmp_path, monkeypatch):
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
        doc = parse_markdown_report("EMT.BD-HSBMENU5.report.md", BD_FILE.read_text(encoding="utf-8"), "sha_p3_2")
        cluster_id = f"cluster-{new_id()}"
        await _save_bd_flow(db, [doc], cluster_id)

        # Step 3: Build Code Flow
        await build_code_flow(db, snap_id)

        # Step 4: Run Stage A Alignment
        align_res = await align_bd_to_code(db, cluster_id, snap_id)

        # Stage A Oracle assertions (SPEC §10):
        tags_by_code_path = {
            r.code_node_id: r.tag for r in align_res.records if r.code_node_id
        }

        # Check code_flow_nodes by path
        async with db.execute(
            "SELECT id, rel_path FROM code_flow_nodes WHERE snapshot_id=?", (snap_id,)
        ) as cur:
            code_nodes_path_map = {r["id"]: r["rel_path"] for r in await cur.fetchall()}

            path_tags = {
                Path(code_nodes_path_map[cid]).name: tag
                for cid, tag in tags_by_code_path.items()
                if cid in code_nodes_path_map
            }

            # PHNIKLOT.clist and HND2UP5J.clist still carry a DOC_CONTRADICTED record (the BD prose
            # claims them unresolved while the code graph has them) — the stale_missing signal.
            # Since the _resolve.py fix now also lets a clean reference to the same file resolve
            # (DOC_MATCHED), the per-path aggregation above may surface DOC_MATCHED; assert directly
            # on the contradiction record so the stale_missing finding is verified independently of
            # aggregation order.
            contradicted_paths = {
                Path(code_nodes_path_map[r.code_node_id]).name
                for r in align_res.records
                if r.code_node_id and r.tag == "DOC_CONTRADICTED" and r.code_node_id in code_nodes_path_map
            }
            assert "PHNIKLOT.clist" in contradicted_paths, f"Actual contradicted: {contradicted_paths}"
            assert "HND2UP5J.clist" in contradicted_paths, f"Actual contradicted: {contradicted_paths}"

        # Downstream continuations must be CODE_ONLY
        assert path_tags.get("HNIKLOT.cbl") == "CODE_ONLY"
        assert path_tags.get("FHNIKLOT.ipf") == "CODE_ONLY"
        assert path_tags.get("HNDK001N.jcl") == "CODE_ONLY"

        # Step 5: Run Stage B Verdicts & Calibration Gate (deterministic mode, 0 LLM)
        verdict_res = await run_flow_verdicts(db, cluster_id, snap_id, provider_id=None)

        assert verdict_res.total_units > 0
        assert verdict_res.code_only_count >= 3  # Bar-steel continuation files

        # Calibration gate assertion (SPEC §9 & FIX 4): match% over resolved units must land strictly between 80.0 and 100.0 (not 100.0)
        assert 80.0 <= verdict_res.match_percentage < 100.0, f"Match %: {verdict_res.match_percentage}"

    finally:
        await close_db()


# ---------------------------------------------------------------------------
# 3. Stage B LLM Verdict Engine Stub Test (FIX 1)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not (BD_FILE.is_file() and SOURCE_DIR.is_dir()), reason=SKIP_REASON)
@pytest.mark.asyncio
async def test_llm_verdict_engine_with_stubbed_provider(tmp_path, monkeypatch):
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
        # Register a stub provider config
        provider_id = "stub-llm-provider"
        await db.execute(
            "INSERT INTO provider_configs (id, kind, display_name, base_url, model_id, capabilities, extra, created_at, updated_at) "
            "VALUES (?, 'openai', 'Stub LLM', 'http://localhost', 'gpt-4o', '{}', '{}', ?, ?)",
            (provider_id, utc_now_iso(), utc_now_iso()),
        )
        await db.commit()

        # Step 1: Build Manifest & Structural Graph
        await ManifestService().build(BuildManifestRequest(snapshot_id=snap_id))
        await StructuralGraphService().build(BuildGraphRequest(snapshot_id=snap_id, force_rebuild=True))

        # Step 2: Build BD Flow cluster
        doc = parse_markdown_report("EMT.BD-HSBMENU5.report.md", BD_FILE.read_text(encoding="utf-8"), "sha_p3_2")
        cluster_id = f"cluster-{new_id()}"
        await _save_bd_flow(db, [doc], cluster_id)

        # Step 3: Build Code Flow & Alignment
        await build_code_flow(db, snap_id)
        await align_bd_to_code(db, cluster_id, snap_id)

        # Stub chat_stream_events to return batched responses with schema {"results": [...]}
        prompts_sent: list[str] = []

        async def stub_chat_stream_events(self, request):
            user_msg = request.messages[-1].content
            prompts_sent.append(user_msg)

            units = json.loads(user_msg)
            results = []
            for u in units:
                uid = u["unit_id"]
                if "edge:1" in uid:
                    # Return numeric value comparison mismatch (must be rejected by gate)
                    results.append({
                        "unit_id": uid,
                        "verdict": "BROKEN",
                        "guard_verdict": "CLASS_MISMATCH",
                        "ai_bucket": "fabricated",
                        "reason": "Numeric value 4 != 0 is wrong",
                        "evidence": None,
                    })
                elif u.get("base_verdict") == "BROKEN":
                    # Attempt MATCH on deterministic BROKEN (must be clamped to BROKEN by override floor)
                    results.append({
                        "unit_id": uid,
                        "verdict": "MATCH",
                        "guard_verdict": "CLASS_MATCH",
                        "ai_bucket": None,
                        "reason": "LLM claims match despite stale missing",
                        "evidence": None,
                    })
                else:
                    results.append({
                        "unit_id": uid,
                        "verdict": "MATCH",
                        "guard_verdict": "CLASS_MATCH",
                        "ai_bucket": None,
                        "reason": "Preserved flow route",
                        "evidence": None,
                    })

            payload = {"results": results}
            yield {"type": "content", "text": json.dumps(payload)}

        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", stub_chat_stream_events)

        verdict_res = await run_flow_verdicts(db, cluster_id, snap_id, provider_id=provider_id)

        # Assert LLM calls were batched (approx 1 call per 10 units, not 210 calls)
        assert 0 < len(prompts_sent) <= 5, f"Expected batched calls <= 5, got {len(prompts_sent)}"

        # Assert NO raw source code files (.cbl, .jcl) were in the LLM prompts sent
        for prompt in prompts_sent:
            assert "IDENTIFICATION DIVISION" not in prompt
            assert "EXEC SQL" not in prompt
            assert "//JOB" not in prompt
            units_in_prompt = json.loads(prompt)
            assert len(units_in_prompt) <= 10

        # Assert numeric value comparison verdict was REJECTED by the validator gate
        for v in verdict_res.verdicts:
            assert "Numeric value 4 != 0" not in v.reason

        # Assert override floor prevented BROKEN -> MATCH flip for stale_missing unit
        stale_missing_verdicts = [v for v in verdict_res.verdicts if v.ai_bucket == "stale_missing"]
        for v in stale_missing_verdicts:
            assert v.verdict == "BROKEN", "Override floor must clamp LLM MATCH on BROKEN unit"

    finally:
        await close_db()
