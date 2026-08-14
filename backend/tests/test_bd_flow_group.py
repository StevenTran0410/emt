"""Tests for Phase 4 Business Flow Grouping (TICKET P4-1 & P4-1-FIX).

OFFLINE ONLY — stubbed chat_stream_events, no real LLM network calls.
"""
import json
from pathlib import Path
import re
from typing import Any
import pytest

from domain.doc_graph._markdown_parser import parse_markdown_report
from domain.doc_graph.service._build import _save_bd_flow
from domain.doc_graph._flow_group import run_bd_flow_grouping
from domain.doc_graph.service import DocGraphService
from infrastructure.db.database import close_db, get_db, init_db
from shared.utils import new_id, utc_now_iso

BD_FILE = Path(r"d:\Emt\emt_data\input_emt\EMT.BD-HSBMENU5.report.md")
SKIP_REASON = "Real BD file EMT.BD-HSBMENU5.report.md absent"


async def _insert_mock_cluster(db: Any, cluster_id: str) -> None:
    now = utc_now_iso()
    await db.execute(
        """
        INSERT INTO doc_graph_clusters
        (id, cluster_name, source_dir, bd_path, snapshot_id, input_fingerprint, parser_version, status, generated_at, created_at)
        VALUES (?, 'HSBMENU5', 'd:/Emt/source', 'd:/Emt/bd.md', NULL, 'sha_p4', 1, 'ready', ?, ?)
        """,
        (cluster_id, now, now),
    )
    await db.commit()


@pytest.mark.skipif(not BD_FILE.is_file(), reason=SKIP_REASON)
@pytest.mark.asyncio
async def test_bd_flow_grouping_fallback_offline(tmp_path, monkeypatch):
    """Test deterministic fallback grouping when provider_id=None or llm_enabled=False."""
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(db_dir))
    await init_db()

    try:
        db = get_db()
        cluster_id = f"cluster-{new_id()}"
        await _insert_mock_cluster(db, cluster_id)

        doc_content = BD_FILE.read_text(encoding="utf-8")
        doc = parse_markdown_report("EMT.BD-HSBMENU5.report.md", doc_content, "sha_p4_1")

        # 1. Save BD Flow skeleton
        await _save_bd_flow(db, [doc], cluster_id)

        # 2. Run BD Flow grouping offline (llm_enabled=False)
        await run_bd_flow_grouping(db, [doc], cluster_id, llm_enabled=False, provider_id=None)

        # 3. Query API response via DocGraphService
        res = await DocGraphService().bd_flow(cluster_id)
        assert "business_flows" in res
        flows = res["business_flows"]

        # Regression assertions per TICKET P4-1-FIX
        assert len(flows) >= 14, f"Offline fallback run must yield >= 14 flows, got {len(flows)}"
        sub6_flows = [f for f in flows if f["sub_ix"] == 6]
        assert len(sub6_flows) >= 10, f"Section 6 (Event-Flows) must yield >= 10 flows, got {len(sub6_flows)}"

        # Check nodes/edges lookup to verify IDs
        async with db.execute("SELECT id FROM bd_flow_nodes WHERE cluster_id=?", (cluster_id,)) as cur:
            valid_node_ids = {r["id"] for r in await cur.fetchall()}
        async with db.execute("SELECT id FROM bd_flow_edges WHERE cluster_id=?", (cluster_id,)) as cur:
            valid_edge_ids = {r["id"] for r in await cur.fetchall()}

        for f in flows:
            assert f["origin"] == "fallback"
            assert "Business flow" in f["name"]
            assert len(f["steps"]) > 0
            if "exseq" not in f["block_key"]:
                assert len(f["steps"]) <= 20, f"Mermaid flow {f['block_key']} has too many steps: {len(f['steps'])}"

            for s in f["steps"]:
                assert isinstance(s["source_node_ids"], list)
                assert len(s["source_node_ids"]) > 0
                for nid in s["source_node_ids"]:
                    assert nid in valid_node_ids

            for b in f["branches"]:
                assert isinstance(b["source_edge_ids"], list)
                for eid in b["source_edge_ids"]:
                    assert eid in valid_edge_ids
                assert b["branch_kind"] in ("SUCCESS", "FAILURE", "ERROR", "OTHER")

    finally:
        await close_db()


@pytest.mark.skipif(not BD_FILE.is_file(), reason=SKIP_REASON)
@pytest.mark.asyncio
async def test_bd_flow_grouping_stubbed_provider(tmp_path, monkeypatch):
    """Test LLM grouping call with stubbed ProviderConfigService.

    Asserts:
    1. Valid LLM response creates origin='llm' flows with custom name/description
    2. System prompt contains field-name schema, worked example, and coverage rule
    """
    from domain.model_connector.service import ProviderConfigService

    db_dir = tmp_path / "db"
    db_dir.mkdir()
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(db_dir))
    await init_db()

    captured_requests = []

    async def stub_chat_stream_events(self, request):
        captured_requests.append(request)
        user_msg = request.messages[1].content
        node_aliases = re.findall(r"^(N\d{3})\s*\|", user_msg, re.MULTILINE)
        edge_aliases = re.findall(r"^(E\d{3})\s*\|", user_msg, re.MULTILINE)

        steps_json = []
        for ix, a in enumerate(node_aliases):
            steps_json.append({
                "aliases": [a],
                "name": f"Initialize Menu Screen {ix + 1}",
                "functionality": f"Display options for {a}."
            })

        branches_json = []
        if len(node_aliases) >= 2 and edge_aliases:
            branches_json.append({
                "edge_aliases": [edge_aliases[0]],
                "source_step_ix": 0,
                "target_step_ix": 1,
                "kind": "SUCCESS",
                "guard_description": "User selects valid option"
            })

        json_obj = {
            "name": "Process User Menu Dispatch",
            "description": "High-level business flow managing screen navigation and transaction execution.",
            "steps": steps_json,
            "branches": branches_json
        }
        yield {"type": "content", "text": json.dumps(json_obj)}

    monkeypatch.setattr(ProviderConfigService, "chat_stream_events", stub_chat_stream_events)

    try:
        db = get_db()
        cluster_id = f"cluster-{new_id()}"
        await _insert_mock_cluster(db, cluster_id)

        doc_content = BD_FILE.read_text(encoding="utf-8")
        doc = parse_markdown_report("EMT.BD-HSBMENU5.report.md", doc_content, "sha_p4_1")

        await _save_bd_flow(db, [doc], cluster_id)

        await run_bd_flow_grouping(db, [doc], cluster_id, llm_enabled=True, provider_id="stub-provider")

        res = await DocGraphService().bd_flow(cluster_id)
        flows = res["business_flows"]
        assert len(flows) >= 14

        for f in flows:
            if f["origin"] == "llm":
                assert f["name"] == "Process User Menu Dispatch"
                assert "High-level business flow" in f["description"]
                assert "Initialize Menu Screen" in f["steps"][0]["name"]
                assert f["branches"][0]["branch_kind"] == "SUCCESS"

        assert len(captured_requests) >= 1
        req0 = captured_requests[0]
        assert req0.reasoning_effort == "high"
        assert req0.json_mode is True

        sys_msg = req0.messages[0].content
        assert "OUTPUT JSON SCHEMA:" in sys_msg
        assert "EXAMPLE (" in sys_msg
        assert "Every qualifying step/decision/event/job_step node alias" in sys_msg

    finally:
        await close_db()


@pytest.mark.skipif(not BD_FILE.is_file(), reason=SKIP_REASON)
@pytest.mark.asyncio
async def test_bd_flow_grouping_rebuild_idempotency(tmp_path, monkeypatch):
    """Test that rebuilding the same cluster replaces (does not duplicate) business flow rows."""
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(db_dir))
    await init_db()

    try:
        db = get_db()
        cluster_id = f"cluster-{new_id()}"
        await _insert_mock_cluster(db, cluster_id)

        doc_content = BD_FILE.read_text(encoding="utf-8")
        doc = parse_markdown_report("EMT.BD-HSBMENU5.report.md", doc_content, "sha_p4_1")

        # Build run 1
        await _save_bd_flow(db, [doc], cluster_id)
        await run_bd_flow_grouping(db, [doc], cluster_id, llm_enabled=False, provider_id=None)

        res1 = await DocGraphService().bd_flow(cluster_id)
        count1 = len(res1["business_flows"])
        flow_ids_1 = [f["id"] for f in res1["business_flows"]]

        # Build run 2 (rebuild)
        await _save_bd_flow(db, [doc], cluster_id)
        await run_bd_flow_grouping(db, [doc], cluster_id, llm_enabled=False, provider_id=None)

        res2 = await DocGraphService().bd_flow(cluster_id)
        count2 = len(res2["business_flows"])
        flow_ids_2 = [f["id"] for f in res2["business_flows"]]

        assert count1 == count2, f"Rebuild must produce same count, got {count1} vs {count2}"
        assert flow_ids_1 == flow_ids_2, "Rebuild must produce identical stable flow IDs"

    finally:
        await close_db()
