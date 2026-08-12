"""Acceptance tests for BD Flow Skeleton deterministic extraction."""

from pathlib import Path
import pytest

from domain.doc_graph._markdown_parser import parse_markdown_report
from domain.doc_graph._flow_extract import extract_bd_flow

BD_FILE_PATH = Path(r"d:\Emt\emt_data\input_emt\EMT.BD-HSBMENU5.report.md")
SKIP_REASON = "Real BD file EMT.BD-HSBMENU5.report.md absent"


@pytest.mark.skipif(not BD_FILE_PATH.exists(), reason=SKIP_REASON)
def test_bd_flow_extraction_acceptance_gates():
    """Verify deterministic BD flow skeleton extraction against real document ground truth."""
    content = BD_FILE_PATH.read_text(encoding="utf-8")
    doc = parse_markdown_report(str(BD_FILE_PATH), content, "sha256_dummy")
    result = extract_bd_flow(doc, doc.id, "cluster_test_bdflow")

    lines = content.splitlines()

    # --- Gate 1: 21 Mermaid blocks captured with correct fence_line values ---
    # Hand derivation: Grep for ```mermaid fences in EMT.BD-HSBMENU5.report.md yields exactly 21 blocks
    # at line numbers: 92, 278, 605, 718, 1125, 1678, 1758, 2104, 2200, 2292, 2387, 2481, 2574,
    # 2679, 2789, 2903, 3010, 3116, 3222, 3323, 3424.
    assert len(doc.mermaid_diagrams) == 21

    expected_fence_lines = [
        92, 278, 605, 718, 1125, 1678, 1758, 2104, 2200, 2292, 2387, 2481,
        2574, 2679, 2789, 2903, 3010, 3116, 3222, 3323, 3424,
    ]
    actual_fence_lines = [b.fence_line for b in doc.mermaid_diagrams]
    assert actual_fence_lines == expected_fence_lines

    # Spot-check 3 fence lines in original file: line 92 (Screen Spec), line 718 (Business Flow), line 1758 (Program Dependencies)
    assert lines[91].strip().startswith("```mermaid")  # 1-based index 92 is 0-based index 91
    assert lines[717].strip().startswith("```mermaid")  # 1-based index 718 is 0-based index 717
    assert lines[1757].strip().startswith("```mermaid")  # 1-based index 1758 is 0-based index 1757

    # --- Gate 2: §5.1 Activity Diagram nodes & bindings ---
    # Hand derivation: §5.1 End-to-End Processing Flow stateDiagram (fence line 718, subreport 2) contains
    # 12 process steps (Step1 through Step12) and 4 decision nodes (Decision1 through Decision4).
    bf_nodes = [n for n in result.nodes if "bdflow:doc/EMT.BD-HSBMENU5.report.md:2:" in n["id"]]
    bf_steps = [n for n in bf_nodes if n["node_kind"] == "step"]
    bf_decisions = [n for n in bf_nodes if n["node_kind"] == "decision"]

    assert len(bf_steps) == 12
    assert len(bf_decisions) == 4

    for step_node in bf_steps:
        assert step_node["binding"] is not None
        assert step_node["binding_type"] == "program"

    # --- Gate 3: Execution Sequence table rows ---
    # Hand derivation: Table 1 at line 907 has 61 rows (S00..S06), Table 2 at line 1644 has 2 rows (S01, S06) = 63 total job_step rows.
    job_steps = [n for n in result.nodes if n["node_kind"] == "job_step"]
    assert len(job_steps) == 63

    # Check ordinals increasing per table
    t1_steps = [n for n in job_steps if n["doc_line_start"] < 1000]
    t2_steps = [n for n in job_steps if n["doc_line_start"] >= 1000]
    assert len(t1_steps) == 61
    assert len(t2_steps) == 2

    assert [n["ordinal"] for n in t1_steps] == list(range(61))
    assert [n["ordinal"] for n in t2_steps] == list(range(2))

    # Spot-check locator splitting on (FCPY@L640)
    fcpy_node = next(n for n in job_steps if n["local_id"] == "FCPY")
    assert fcpy_node["source_locator"] == "L640"

    # --- Gate 4: Event nodes merged from stateDiagram and Event Categories table ---
    # Hand derivation: Screen 1 has 6 event rows, Screen 2 has 8 event rows = 14 event nodes total.
    # Each event node is defined in BOTH Event Categories table (D2) and Event Flow stateDiagram (D1).
    event_nodes = [n for n in result.nodes if n["node_kind"] == "event"]
    assert len(event_nodes) == 14

    for ev_node in event_nodes:
        assert "D1" in ev_node["provenance_tier"]
        assert "D2" in ev_node["provenance_tier"]

    # --- Gate 5: Line coordinates sanity check & spot checks ---
    # Hand derivation: All line numbers must fall within document bounds [1, 3471].
    total_file_lines = len(lines)
    assert total_file_lines == 3471

    for node in result.nodes:
        assert 1 <= node["doc_line_start"] <= total_file_lines
        assert 1 <= node["doc_line_end"] <= total_file_lines

    for edge in result.edges:
        assert 1 <= edge["doc_line"] <= total_file_lines

    # Spot check 10 nodes against actual file lines
    spot_check_nodes = result.nodes[:10]
    for n in spot_check_nodes:
        start_l = n["doc_line_start"]
        end_l = n["doc_line_end"]
        text_block = "\n".join(lines[start_l - 1:end_l])
        local_id = n["local_id"]
        binding = n["binding"]
        assert local_id in text_block or (binding and binding in text_block)

    # --- Gate 6: Global Node ID uniqueness ---
    # Hand derivation: Local Step1 appears in Screen Spec, Business Flow, and Event Flows. Global IDs must be distinct.
    node_ids = [n["id"] for n in result.nodes]
    assert len(node_ids) == len(set(node_ids))

    step1_nodes = [n for n in result.nodes if n["local_id"] == "Step1"]
    assert len(step1_nodes) > 1
    step1_global_ids = {n["id"] for n in step1_nodes}
    assert len(step1_global_ids) == len(step1_nodes)

    # --- Gate 7: Redundant Job-Flow flowcharts skipped ---
    # Hand derivation: Job-Flow flowchart TD at fence line 1125 and line 1678 duplicate the Execution Sequence tables.
    # Diagnostics must record skipped_redundant_flowchart for these two blocks.
    skipped_diags = [d for d in result.diagnostics if "skipped_redundant_flowchart" in d]
    assert len(skipped_diags) == 2
    assert "fence_line 1125" in skipped_diags[0]
    assert "fence_line 1678" in skipped_diags[1]


@pytest.mark.asyncio
@pytest.mark.skipif(not BD_FILE_PATH.exists(), reason=SKIP_REASON)
async def test_build_bd_flow_only_standalone(tmp_path, monkeypatch):
    """Test BD-only build service method on real BD report file in a throwaway DB."""
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(db_dir))

    from infrastructure.db.database import close_db, get_db, init_db
    from domain.doc_graph.service import DocGraphService
    from domain.doc_graph.types import BuildBdFlowOnlyRequest

    await init_db()
    try:
        service = DocGraphService()
        res = await service.build_bd_flow_only(
            BuildBdFlowOnlyRequest(bd_path=str(BD_FILE_PATH), llm_enabled=False)
        )
        assert res.cluster_id is not None
        assert res.cluster_name == "HSBMENU5 - Basic Design" or "HSBMENU5" in res.cluster_name
        assert res.node_count == 216
        assert res.edge_count == 210

        # Check cluster row in database
        db = get_db()
        async with db.execute(
            "SELECT status FROM doc_graph_clusters WHERE id=?",
            (res.cluster_id,),
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row["status"] == "ready"

        # Query BD flow endpoint data
        flow_data = await service.bd_flow(res.cluster_id)
        assert len(flow_data["nodes"]) == 216
        assert len(flow_data["edges"]) == 210

    finally:
        await close_db()


@pytest.mark.asyncio
@pytest.mark.skipif(not BD_FILE_PATH.exists(), reason=SKIP_REASON)
async def test_build_bd_flow_only_convergence_and_safety(tmp_path, monkeypatch):
    """Test convergence and non-destructive safety when BD-only build runs on an existing full build cluster."""
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(db_dir))

    from infrastructure.db.database import close_db, get_db, init_db
    from domain.doc_graph.service import DocGraphService
    from domain.doc_graph.types import BuildBdFlowOnlyRequest, BuildDocGraphRequest

    await init_db()
    try:
        db = get_db()
        # Seed repo_snapshots row for full build binding
        snap_id = "test-snap-bd-only"
        await db.execute(
            """
            INSERT INTO repo_snapshots (id, local_repo_id, local_path, status, clone_policy, manual_ignores, synced_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (snap_id, "repo1", str(tmp_path), "ready", "copy", "[]", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        await db.commit()

        # Stage fixture directory containing BD report + dummy DD report
        stage_dir = tmp_path / "docs"
        stage_dir.mkdir()
        bd_staged = stage_dir / BD_FILE_PATH.name
        bd_staged.write_text(BD_FILE_PATH.read_text(encoding="utf-8"), encoding="utf-8")

        dd_staged = stage_dir / "GEN.DD.COBOL-HSBMENU5.report.md"
        dd_staged.write_text(
            "# Detail Design — HSBMENU5\n\n## 4. Program Call\n| Call Target | Site |\n|---|---|\n| CBSTM03A | L10 |\n",
            encoding="utf-8",
        )

        service = DocGraphService()

        # 1. Run full build first
        full_sum = await service.build(
            BuildDocGraphRequest(source_dir=str(stage_dir), snapshot_id=snap_id, llm_enabled=False)
        )
        assert full_sum.document_count == 2
        initial_assertion_cnt = full_sum.assertion_count

        async with db.execute(
            "SELECT COUNT(*) as cnt FROM doc_graph_assertions WHERE cluster_id=?", (full_sum.cluster_id,)
        ) as cur:
            ast_cnt_before = (await cur.fetchone())["cnt"]
        assert ast_cnt_before == initial_assertion_cnt

        # 2. Run BD-only build on the staged BD report file
        bd_only_res = await service.build_bd_flow_only(
            BuildBdFlowOnlyRequest(bd_path=str(bd_staged), snapshot_id=snap_id, llm_enabled=False)
        )

        # Convergence: cluster_id MUST match exactly
        assert bd_only_res.cluster_id == full_sum.cluster_id

        # Safety: documents and assertions MUST NOT be wiped
        async with db.execute(
            "SELECT COUNT(*) as cnt FROM doc_graph_documents WHERE cluster_id=?", (full_sum.cluster_id,)
        ) as cur:
            doc_cnt_after = (await cur.fetchone())["cnt"]
        assert doc_cnt_after == 2

        async with db.execute(
            "SELECT COUNT(*) as cnt FROM doc_graph_assertions WHERE cluster_id=?", (full_sum.cluster_id,)
        ) as cur:
            ast_cnt_after = (await cur.fetchone())["cnt"]
        assert ast_cnt_after == initial_assertion_cnt

        # BD flow table rows refreshed
        async with db.execute(
            "SELECT COUNT(*) as cnt FROM bd_flow_nodes WHERE cluster_id=?", (full_sum.cluster_id,)
        ) as cur:
            flow_nodes_cnt = (await cur.fetchone())["cnt"]
        assert flow_nodes_cnt == 216

    finally:
        await close_db()

