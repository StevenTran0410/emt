"""Tests for Phase 4 Part 2 Route Segments, Unit Mapping, and Unit Verdicts (Ticket P4-2)."""
import json
from pathlib import Path
import pytest

from domain.business_flow_integrity import (
    align_bd_to_code,
    build_code_flow,
    build_route_segments,
    generate_flow_narratives,
    get_e2e_flow_map,
    get_flow_integrity_findings,
    run_flow_verdicts,
    run_unit_mapping,
    run_unit_verdicts,
)
from domain.doc_graph._flow_group import run_bd_flow_grouping
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


@pytest.mark.asyncio
async def test_build_route_segments_mock_db(tmp_path, monkeypatch):
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(db_dir))
    await init_db()

    try:
        db = get_db()
        snap_id = f"snap-{new_id()}"
        now = utc_now_iso()

        # Insert test code flow nodes (menu -> program A -> program B)
        nodes = [
            (f"cfnode:{snap_id}:menu.pfd", snap_id, "menu", "MENU", "MENU", "MENU", 1, None, "menu.pfd", 1, 1, "code_structure", "{}", now),
            (f"cfnode:{snap_id}:proga.cbl", snap_id, "program", "PROGA", "PROGRAM", "PROGA", 2, None, "proga.cbl", 1, 1, "code_structure", "{}", now),
            (f"cfnode:{snap_id}:progb.cbl", snap_id, "program", "PROGB", "PROGRAM", "PROGB", 3, None, "progb.cbl", 1, 1, "code_structure", "{}", now),
        ]
        await db.executemany(
            "INSERT INTO code_flow_nodes (id, snapshot_id, node_kind, binding, binding_type, label, ordinal, guard_text, rel_path, line_start, line_end, provenance_tier, attributes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            nodes,
        )

        edges = [
            (f"cfedge:{snap_id}:1", snap_id, f"cfnode:{snap_id}:menu.pfd", f"cfnode:{snap_id}:proga.cbl", "menu_option", "menu_option", None, "menu.pfd", 1, "{}", now),
            (f"cfedge:{snap_id}:2", snap_id, f"cfnode:{snap_id}:proga.cbl", f"cfnode:{snap_id}:progb.cbl", "calls", "calls", None, "proga.cbl", 1, "{}", now),
        ]
        await db.executemany(
            "INSERT INTO code_flow_edges (id, snapshot_id, src_node_id, dst_node_id, edge_kind, label, guard_text, rel_path, line, attributes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            edges,
        )
        await db.commit()

        segments = await build_route_segments(db, snap_id)

        assert len(segments) == 1
        seg = segments[0]
        assert seg.segment_id.startswith("seg:")
        assert seg.bindings == ["MENU", "PROGA", "PROGB"]
        assert seg.edge_kinds == ["menu_option", "calls"]
        assert seg.rel_paths == ["menu.pfd", "proga.cbl", "progb.cbl"]
    finally:
        await close_db()


@pytest.mark.skipif(not (BD_FILE.is_file() and SOURCE_DIR.is_dir()), reason=SKIP_REASON)
@pytest.mark.asyncio
async def test_bfi_p4_2_pipeline_hsbmens5_offline(tmp_path, monkeypatch):
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
        db = get_db()

        # Step 2: Build BD Flow cluster & Business Flows (P4-1)
        doc = parse_markdown_report("EMT.BD-HSBMENU5.report.md", BD_FILE.read_text(encoding="utf-8"), "sha_p4_2")
        cluster_id = f"cluster-{new_id()}"
        await _save_bd_flow(db, [doc], cluster_id)
        await run_bd_flow_grouping(db, [doc], cluster_id, llm_enabled=False, provider_id=None)

        # Step 3: Build Code Flow & Alignment
        await build_code_flow(db, snap_id)
        await align_bd_to_code(db, cluster_id, snap_id)
        await run_flow_verdicts(db, cluster_id, snap_id, provider_id=None)

        # Step 4: Run P4-2 Route Segments & Unit Mapping & Unit Verdicts
        segments = await build_route_segments(db, snap_id)
        assert len(segments) > 0

        mappings = await run_unit_mapping(db, cluster_id, snap_id, provider_id=None)
        assert len(mappings) > 0

        unit_verdict_res = await run_unit_verdicts(db, cluster_id, snap_id, provider_id=None)
        assert unit_verdict_res.total_units == len(mappings)

        # Verify business_unit_verdicts table
        async with db.execute(
            "SELECT id, unit_kind, mapping_status, verdict, comparator_version FROM business_unit_verdicts WHERE cluster_id=? AND snapshot_id=?",
            (cluster_id, snap_id),
        ) as cur:
            rows = await cur.fetchall()
            assert len(rows) == unit_verdict_res.total_units
            assert all(r["comparator_version"] == 2 for r in rows)

        # Verify findings payload includes business object & P4-4 provenance / segment fields
        findings = await get_flow_integrity_findings(db, cluster_id, snap_id)
        assert "business" in findings
        b_payload = findings["business"]
        assert "calibration" in b_payload
        assert "per_flow" in b_payload
        assert len(b_payload["per_flow"]) > 0

        # Verify P4-4 per_flow provenance and narrative fields
        first_flow = b_payload["per_flow"][0]
        assert "section_name" in first_flow
        assert "narrative" in first_flow
        assert len(first_flow["narrative"]) > 0

        # Verify unit details have segment object attached when segment is mapped
        all_units = b_payload["matched_units"] + b_payload["contradicted_units"] + b_payload["unknown_units"]
        assert len(all_units) == unit_verdict_res.total_units
        matched_with_seg = [u for u in b_payload["matched_units"] if u.get("segment")]
        if matched_with_seg:
            assert "bindings" in matched_with_seg[0]["segment"]
            assert "rel_paths" in matched_with_seg[0]["segment"]

        # Verify P4-4 Executive Summary v2
        from domain.business_flow_integrity import generate_executive_summary
        summary = await generate_executive_summary(db, cluster_id, snap_id, provider_id=None)
        assert "overall_verdict" in summary
        assert "headline" in summary
        assert "key_risks" in summary
        assert "coverage_note" in summary
        assert "recommendation" in summary

        # Verify map v2 payload
        flow_map = await get_e2e_flow_map(db, cluster_id, snap_id)
        assert flow_map.get("business_flows_present") is True
        assert "unit_annotations" in flow_map
        assert len(flow_map["unit_annotations"]) == len(mappings)

    finally:
        await close_db()


async def _seed_one_flow_two_steps(db, cluster_id: str, snap_id: str) -> tuple[str, str, str]:
    """Insert one bd_business_flows row with a MATCH step (real bindings) and an UNKNOWN step."""
    now = utc_now_iso()
    flow_id = f"bf:{new_id()}"
    step_matched_id = f"bs:{new_id()}"
    step_unknown_id = f"bs:{new_id()}"

    await db.execute(
        "INSERT INTO bd_business_flows (id, cluster_id, doc_id, sub_ix, block_key, name, description, ordinal, origin, model_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (flow_id, cluster_id, "doc1", 2, "blk1", "Coil Lot Entry", "Full flow description text.", 1, "fallback", None, now),
    )
    await db.executemany(
        "INSERT INTO bd_business_steps (id, flow_id, name, functionality, ordinal, source_node_ids, doc_line_start, doc_line_end, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (step_matched_id, flow_id, "Route to coil entry", "Routes the user to the coil entry screen.", 1, "[]", 10, 12, now),
            (step_unknown_id, flow_id, "Validate lot number", "Validates the lot number format.", 2, "[]", 13, 14, now),
        ],
    )
    await db.executemany(
        "INSERT INTO business_unit_verdicts (id, cluster_id, snapshot_id, unit_id, unit_kind, mapping_status, mapping_method, route_segment_json, verdict, guard_verdict, ai_bucket, reason, evidence_json, comparator_version, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                f"uv:{new_id()}", cluster_id, snap_id, step_matched_id, "step", "MAPPED", "exact_binding",
                json.dumps({"bindings": ["HSBMENU5.pfd", "PHNIXLOT.clist"], "rel_paths": ["HSBMENU5.pfd", "PHNIXLOT.clist"]}),
                "MATCH", "CLASS_MATCH", None, "BD step routes to coil entry; source realizes it via HSBMENU5.pfd -> PHNIXLOT.clist.", "{}", 2, now,
            ),
            (
                f"uv:{new_id()}", cluster_id, snap_id, step_unknown_id, "step", "NO_SAFE_MATCH", "offline",
                None, "UNKNOWN", None, None, "No source code route found for this step (current MVP scope).", "{}", 2, now,
            ),
        ],
    )
    await db.commit()
    return flow_id, step_matched_id, step_unknown_id


@pytest.mark.asyncio
async def test_generate_flow_narratives_deterministic_fallback(tmp_path, monkeypatch):
    """TICKET P4-4: generate_flow_narratives with provider_id=None must produce a concrete,
    non-bare-count deterministic narrative naming the real matched binding and the missing step."""
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(db_dir))
    await init_db()

    try:
        db = get_db()
        cluster_id = f"cluster-{new_id()}"
        snap_id = f"snap-{new_id()}"
        flow_id, _, _ = await _seed_one_flow_two_steps(db, cluster_id, snap_id)

        narratives = await generate_flow_narratives(db, cluster_id, snap_id, provider_id=None)

        assert flow_id in narratives
        text = narratives[flow_id]
        assert text, "fallback narrative must not be empty"
        assert "Route to coil entry" in text
        assert "HSBMENU5.pfd -> PHNIXLOT.clist" in text
        assert "Validate lot number" in text
        assert "1/2" in text  # concrete count, not just a bare percentage
    finally:
        await close_db()


@pytest.mark.asyncio
async def test_generate_flow_narratives_stubbed_provider_llm_discipline(tmp_path, monkeypatch):
    """TICKET P4-4: verify LLM discipline (reasoning_effort=low, json_mode, temperature=0.0,
    max_completion_tokens=50000, single-arg chat_stream_events call) and that an empty/error
    response logs a warning and falls back to the deterministic narrative (never silent)."""
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(db_dir))
    await init_db()

    captured_requests = []

    async def stub_ok(self, request):
        captured_requests.append(request)
        payload = json.loads(request.messages[1].content.split(":\n", 1)[1])
        flow_id = payload[0]["flow_id"]
        yield {"type": "content", "text": json.dumps({"narratives": [{"flow_id": flow_id, "narrative": "Stubbed grounded narrative."}]})}

    try:
        db = get_db()
        cluster_id = f"cluster-{new_id()}"
        snap_id = f"snap-{new_id()}"
        flow_id, _, _ = await _seed_one_flow_two_steps(db, cluster_id, snap_id)

        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", stub_ok)
        narratives = await generate_flow_narratives(db, cluster_id, snap_id, provider_id="stub-provider")

        assert narratives[flow_id] == "Stubbed grounded narrative."
        assert len(captured_requests) == 1
        req = captured_requests[0]
        assert req.reasoning_effort == "low"
        assert req.json_mode is True
        assert req.temperature == 0.0
        assert req.max_completion_tokens == 50000

        # Now simulate an empty/erroring LLM response — must fall back deterministically, never silently.
        cluster_id2 = f"cluster-{new_id()}"
        snap_id2 = f"snap-{new_id()}"
        flow_id2, _, _ = await _seed_one_flow_two_steps(db, cluster_id2, snap_id2)

        async def stub_empty(self, request):
            if False:
                yield {}  # pragma: no cover - make this an async generator

        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", stub_empty)
        narratives2 = await generate_flow_narratives(db, cluster_id2, snap_id2, provider_id="stub-provider")
        assert narratives2[flow_id2]  # deterministic fallback still populated
        assert "Route to coil entry" in narratives2[flow_id2]
    finally:
        await close_db()


@pytest.mark.asyncio
async def test_flow_llm_output_persistence_roundtrip(tmp_path, monkeypatch):
    """P4-4 persistence: narratives generated with a provider are STORED and readable back without any
    LLM call (so an LLM-blocked machine shows the persisted text); a no-provider regen must not
    overwrite them; and the executive summary persists/loads too."""
    from domain.business_flow_integrity._narrative import load_flow_narratives
    from domain.business_flow_integrity._summary import _persist_executive_summary, load_executive_summary

    db_dir = tmp_path / "db"
    db_dir.mkdir()
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(db_dir))
    await init_db()

    try:
        db = get_db()
        cluster_id = f"cluster-{new_id()}"
        snap_id = f"snap-{new_id()}"
        flow_id, _, _ = await _seed_one_flow_two_steps(db, cluster_id, snap_id)

        async def stub_ok(self, request):
            payload = json.loads(request.messages[1].content.split(":\n", 1)[1])
            fid = payload[0]["flow_id"]
            yield {"type": "content", "text": json.dumps({"narratives": [{"flow_id": fid, "narrative": "Persisted LLM narrative."}]})}

        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", stub_ok)

        # Generating with a provider persists the narratives...
        await generate_flow_narratives(db, cluster_id, snap_id, provider_id="stub-provider")
        # ...and a later read (no LLM) returns the stored text.
        loaded = await load_flow_narratives(db, cluster_id, snap_id)
        assert loaded.get(flow_id) == "Persisted LLM narrative."

        # A no-provider regen must NOT overwrite the stored LLM narrative (it early-returns before persist).
        await generate_flow_narratives(db, cluster_id, snap_id, provider_id=None)
        assert (await load_flow_narratives(db, cluster_id, snap_id)).get(flow_id) == "Persisted LLM narrative."

        # Executive summary persist/load round-trip.
        await _persist_executive_summary(db, cluster_id, snap_id, {"overall_verdict": "PASS", "headline": "H"}, "llm")
        loaded_sum = await load_executive_summary(db, cluster_id, snap_id)
        assert loaded_sum and loaded_sum["overall_verdict"] == "PASS"
    finally:
        await close_db()
