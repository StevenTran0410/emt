"""Integration and acceptance tests for BD/DD doc_graph domain."""

import json
import shutil
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from domain.doc_graph._markdown_parser import _DSN_REGEX, parse_markdown_report
from infrastructure.db.database import close_db, get_db, init_db
from main import create_app

SOURCE_DOCS_DIR = Path(r"d:\Emt\emt_data\generated_docs")
FIXTURE_FILES = [
    "GEN.BD-CREASTMT.report.md",
    "GEN.DD.COBOL-CBSTM03A.report.md",
    "GEN.DD.COBOL-CBSTM03B.report.md",
    "GEN.DD.JCL-CREASTMT.report.md",
]


def stage_fixture_docs(dest_dir: Path) -> Path:
    """Stage exactly the 4 permitted public fixture files into an isolated directory."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    for fname in FIXTURE_FILES:
        src = SOURCE_DOCS_DIR / fname
        if not src.exists():
            pytest.skip(f"Fixture file missing: {src}")
        shutil.copy(src, dest_dir / fname)
    return dest_dir


@pytest.mark.asyncio
async def test_doc_graph_clean_corpus_build_and_acceptance(tmp_path, monkeypatch):
    """Test 1-5 & T1, T2, T4, R2-3, R2-4: Clean corpus build, node types, edges/assertions,
    dataset dedup, field_width_relation, provenance, and the exact clean-corpus mismatch
    breakdown (0 error, 0 warning, 3 unverified/info — see R3-TESTS #1)."""
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(db_dir))

    await init_db()

    try:
        docs_dir = stage_fixture_docs(tmp_path / "docs")

        from domain.doc_graph.service import DocGraphService
        from domain.doc_graph.types import BuildDocGraphRequest

        service = DocGraphService()
        req = BuildDocGraphRequest(source_dir=str(docs_dir), force_rebuild=True)
        summary = await service.build(req)

        # 1. Build succeeds
        assert summary.document_count == 4
        assert summary.assertion_count > 0
        assert summary.node_count > 0
        assert summary.edge_count > 0
        assert summary.cluster_name == "CREASTMT"  # M1: strip trailing ' cluster'

        cluster_id = summary.cluster_id

        # 2. Verify Nodes & R2-4 Node Provenance
        nodes_res = await service.nodes(cluster_id, limit=500)
        node_ids = {n.id for n in nodes_res.nodes}
        nodes_by_type: dict[str, list[str]] = {}
        for n in nodes_res.nodes:
            nodes_by_type.setdefault(n.node_type, []).append(n.id)

        assert "program/CBSTM03A" in node_ids
        assert "program/CBSTM03B" in node_ids
        assert "extroutine/CEE3ABD" in node_ids
        assert "job/CREASTMT" in node_ids
        assert "step/CREASTMT.DELDEF01" in node_ids
        assert "step/CREASTMT.STEP040" in node_ids

        # Copybooks
        assert any("COSTM01" in n for n in node_ids)
        assert any("CVACT03Y" in n for n in node_ids)
        assert any("CUSTREC" in n for n in node_ids)
        assert any("CVACT01Y" in n for n in node_ids)

        # C/G: BD's §9.1 handler and utility-typing must never fabricate a program/*
        # node out of a copybook, DDNAME, or step token (Codex finding 4).
        assert "program/COSTM01" not in node_ids
        assert "program/STEP010" not in node_ids
        assert "program/TRANSACT" not in node_ids
        assert "program/STMTFILE" not in node_ids
        assert "program/SORT" not in node_ids
        assert "extroutine/SORT" in node_ids  # utilities are extroutine/*, matching DD

        # Exact node-type counts for this fixed 4-file corpus — a genuine identity
        # check, not a loose "any(...)" — regressing any of these means an entity got
        # mistyped, duplicated, or dropped.
        assert len(nodes_by_type.get("program", [])) == 2  # CBSTM03A, CBSTM03B
        assert len(nodes_by_type.get("copybook", [])) == 4
        assert len(nodes_by_type.get("dataset", [])) == 9
        assert len(nodes_by_type.get("step", [])) == 5
        assert len(nodes_by_type.get("br", [])) == 15  # BR-001..015
        assert len(nodes_by_type.get("tbd", [])) == 8  # TBD-001..008
        assert set(nodes_by_type.get("extroutine", [])) == {
            "extroutine/CEE3ABD",
            "extroutine/IDCAMS",
            "extroutine/IEFBR14",
            "extroutine/SORT",
        }

        # T4: Dataset nodes deduped via alias map to canonical DSN — every dataset id is
        # a full-DSN shape (no 2-component fragment survives), and specific known
        # friendly-name/DDNAME collisions collapse onto the SAME canonical node (not just
        # "any node starts with dataset/").
        assert "dataset/AWS.M2.CARDDEMO.TRXFL.SEQ" in node_ids
        assert "dataset/AWS.M2.CARDDEMO.TRXFL.VSAM.KSDS" in node_ids
        assert "dataset/TRXFL.SEQ" not in node_ids
        assert "dataset/TRXFL.VSAM" not in node_ids
        assert "dataset/TRANSACT.VSAM" not in node_ids
        for ds_id in nodes_by_type.get("dataset", []):
            dsn = ds_id.split("/", 1)[1]
            assert _DSN_REGEX.fullmatch(dsn), f"dataset node id is not a full-DSN shape: {ds_id}"

        # R2-4: Node provenance populated for code entity nodes
        code_nodes = [
            n
            for n in nodes_res.nodes
            if n.node_type in ("program", "job", "step", "dataset", "copybook")
        ]
        assert len(code_nodes) > 0
        assert all(len(n.provenance) > 0 for n in code_nodes), (
            "Node provenance must be populated across code entity nodes"
        )

        # 3. Verify Edges & Assertions
        edges_res = await service.edges(cluster_id, limit=2000)
        edge_types = {e.edge_type for e in edges_res.edges}

        assert "program_calls_program" in edge_types
        assert "program_copies_copybook" in edge_types
        assert "job_executes_step" in edge_types
        assert "step_runs_program" in edge_types
        assert "accesses_dataset" in edge_types
        assert "step_binds_dd" in edge_types
        assert "dd_binds_dataset" in edge_types
        assert "rule_about" in edge_types
        assert "references" in edge_types
        assert "defines" in edge_types

        # Verify zero dangling edges across ALL edges
        for e in edges_res.edges:
            assert e.src_node_id in node_ids, f"Edge src_node_id missing: {e.src_node_id}"
            assert e.dst_node_id in node_ids, f"Edge dst_node_id missing: {e.dst_node_id}"

        # Check rule_about edges on BR-013 node
        br013_edges = [
            e
            for e in edges_res.edges
            if e.src_node_id == "br/BR-013" and e.edge_type == "rule_about"
        ]
        assert len(br013_edges) > 0, "BR-013 should have rule_about linking edge to step/entity"

        call_edge = next(
            (
                e
                for e in edges_res.edges
                if e.src_node_id == "program/CBSTM03A" and e.dst_node_id == "program/CBSTM03B"
            ),
            None,
        )
        assert call_edge is not None
        assert str(call_edge.attributes.get("call_site_count")) == "13"

        # F8: `cites` edges must point at a real (created) node id — no dangling edges.
        cites_edges = [e for e in edges_res.edges if e.edge_type == "cites"]
        assert len(cites_edges) > 0
        assert all(e.dst_node_id in node_ids for e in cites_edges), (
            "cites edge points at a node that was never created"
        )

        db = get_db()

        # T1 (R3-TESTS #3): DD-side calls == 13, asserted on the DD `calls` ASSERTION
        # itself (the immutable comparison substrate), not the merged/display edge.
        async with db.execute(
            "SELECT value FROM doc_graph_assertions WHERE cluster_id=? AND side='dd' "
            "AND predicate='calls' AND subject='program/CBSTM03A' "
            "AND object='program/CBSTM03B'",
            (cluster_id,),
        ) as cur:
            dd_call_rows = await cur.fetchall()
        assert len(dd_call_rows) == 1
        assert dd_call_rows[0]["value"] == "13"

        # R2-3: Verify field_width_relation assertion exists on both BD and DD sides,
        # for the CORRECT field id (H): field/CBSTM03A.ACCT-CURR-BAL — CBSTM03A's own
        # ACCOUNT-RECORD field (§7.4), not the wrong COSTM01 hardcode, and with no
        # trailing "(L7)" citation-suffix baked into the id.
        async with db.execute(
            "SELECT side, predicate, subject, object, qualifiers FROM doc_graph_assertions "
            "WHERE cluster_id=? AND predicate='field_width_relation'",
            (cluster_id,),
        ) as cur:
            fwr_rows = await cur.fetchall()
        # DD emits this relation twice (the DD-COBOL field-table handler independently
        # matches both the ST-CURR-BAL row in §4.2 and the ACCT-CURR-BAL row in §7.4 —
        # each re-derives the identical, correct relation) + once on the BD side = 3.
        assert len(fwr_rows) == 3, "field_width_relation: 2 DD-side (§4.2 + §7.4) + 1 BD-side"
        sides = [r["side"] for r in fwr_rows]
        assert sides.count("dd") == 2
        assert sides.count("bd") == 1
        for r in fwr_rows:
            assert r["subject"] == "field/CBSTM03A.ST-CURR-BAL"
            assert r["object"] == "field/CBSTM03A.ACCT-CURR-BAL"
            assert "(L" not in r["object"]
            qual = json.loads(r["qualifiers"])
            assert qual["relation"] == "narrower_than"
        # Both sides agree -> no `value` mismatch for this pair (checked exhaustively below).
        assert "field/COSTM01.ACCT-CURR-BAL" not in node_ids, (
            "ACCT-CURR-BAL was wrongly hardcoded onto COSTM01 (TRNX-RECORD), not CVACT01Y"
        )

        # T1 & T2 + R3-TESTS #1: exact clean-corpus mismatch breakdown.
        mismatches_res = await service.mismatches(cluster_id)
        by_type: dict[str, int] = {}
        for m in mismatches_res.mismatches:
            by_type[m.mismatch_type] = by_type.get(m.mismatch_type, 0) + 1

        error_mismatches = [m for m in mismatches_res.mismatches if m.severity == "error"]
        warning_mismatches = [m for m in mismatches_res.mismatches if m.severity == "warning"]
        assert len(error_mismatches) == 0, (
            f"Clean corpus should have 0 error mismatches, got: {error_mismatches}"
        )
        assert len(warning_mismatches) == 0, (
            f"Clean corpus should have 0 warning mismatches, got: {warning_mismatches}"
        )
        assert by_type.get("existence", 0) == 0
        assert by_type.get("broken_citation", 0) == 0
        assert by_type.get("relationship", 0) == 0
        assert by_type.get("value", 0) == 0
        # Documented: 3 genuine `unverified`/info entries, all DELDEF01 dataset accesses
        # (DELDEF01 has no dedicated §6.x DD-Statements table in the DD-JCL report —
        # it appears only in §4 Call Step List / §5 mermaid — so DD genuinely has no
        # binding-level statement to corroborate against; this is a real coverage gap,
        # not parser noise).
        assert by_type.get("unverified", 0) == 3
        for m in mismatches_res.mismatches:
            if m.mismatch_type == "unverified":
                assert m.severity == "info"
                assert "CREASTMT.DELDEF01" in json.dumps(m.evidence)

        # Negative controls: balance truncation and HTMLFILE DCB mismatch must NOT be flagged.
        assert not any("ACCT-CURR-BAL" in m.description for m in mismatches_res.mismatches)
        assert not any("HTMLFILE" in m.description for m in mismatches_res.mismatches)
        # Finding 1 (SKIP, documented no-op): BD never narrates DCB values in this
        # corpus at all — checked across EVERY bd-side assertion's qualifiers (not
        # filtered down to a predicate that is a priori guaranteed empty), so this is a
        # genuine assertion about the parser's behavior, not a vacuous `not any(...)`.
        async with db.execute(
            "SELECT qualifiers FROM doc_graph_assertions WHERE cluster_id=? AND side='bd'",
            (cluster_id,),
        ) as cur:
            bd_qual_rows = await cur.fetchall()
        bd_dcb_narrations = [
            json.loads(r["qualifiers"] or "{}").get("dcb")
            for r in bd_qual_rows
            if json.loads(r["qualifiers"] or "{}").get("dcb")
        ]
        assert bd_dcb_narrations == [], (
            f"Finding 1 says BD narrates no DCB values in this corpus (documented no-op); "
            f"found: {bd_dcb_narrations}"
        )

    finally:
        await close_db()


@pytest.mark.asyncio
async def test_doc_graph_synthetic_positive_mismatches(tmp_path, monkeypatch):
    """Test 6 & T3, T6, R2-1, R2-2 + R3-TESTS #5/#6/#7: Synthetic BD+DD variants triggering
    existence, broken_citation, relationship (both the definitive-error and the
    ambiguous-unverified case), and value mismatches — plus the matching negative controls."""
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(db_dir))

    await init_db()

    try:
        docs_dir = stage_fixture_docs(tmp_path / "docs_synth")
        bd_path = docs_dir / "GEN.BD-CREASTMT.report.md"
        original_bd = bd_path.read_text(encoding="utf-8")

        from domain.doc_graph.service import DocGraphService
        from domain.doc_graph.types import BuildDocGraphRequest

        service = DocGraphService()

        # 6a & T6 + R3-TESTS #6. Existence mismatch (unhedged non-existent program
        # reference CBSTM999); hedged external entity (CEE3ABD) stays suppressed.
        synthetic_bd_1 = (
            original_bd + "\n\n### 9.1 Call-Graph Table\n| Dependency | Resolution |\n"
            "| CBSTM03A -> CBSTM999 (5 call sites) | ✓ Resolved |\n"
        )
        bd_path.write_text(synthetic_bd_1, encoding="utf-8")

        summary1 = await service.build(
            BuildDocGraphRequest(source_dir=str(docs_dir), force_rebuild=True)
        )
        mismatches1 = await service.mismatches(summary1.cluster_id)
        existence_mismatches = [m for m in mismatches1.mismatches if m.mismatch_type == "existence"]
        assert len(existence_mismatches) == 1
        assert "CBSTM999" in existence_mismatches[0].description
        assert not any("CEE3ABD" in m.description for m in existence_mismatches), (
            "hedged external entity must be suppressed"
        )

        # 6b & R3-TESTS #7. Broken citation mismatch (citing non-existent section §9.3);
        # a nonsense §999 must also break; the real citations must still resolve.
        synthetic_bd_2 = (
            original_bd + "\n\nSee GEN.DD.COBOL-CBSTM03B.report.md §9.3 for details.\n"
            "Also see GEN.DD.COBOL-CBSTM03B.report.md §999 Nonsense.\n"
        )
        bd_path.write_text(synthetic_bd_2, encoding="utf-8")

        summary2 = await service.build(
            BuildDocGraphRequest(source_dir=str(docs_dir), force_rebuild=True)
        )
        mismatches2 = await service.mismatches(summary2.cluster_id)
        broken_cites = [m for m in mismatches2.mismatches if m.mismatch_type == "broken_citation"]
        assert any("9.3" in m.description for m in broken_cites)
        assert any("999" in m.description for m in broken_cites)
        # The real citations (§6.5, step 4.4/7.4/10.4/13.4, unnumbered §3 Purpose/§3
        # Control-Flow) must NOT be among the broken ones even with the synthetic additions.
        assert not any("§6.5" in m.description for m in broken_cites)
        assert not any("Purpose" in m.description for m in broken_cites)
        assert not any("Control-Flow" in m.description for m in broken_cites)

        # 6c & R2-1 + R3-TESTS #5 (error case). Relationship ERROR: BD asserts write
        # access on a DD *definitive*-read file — SORTIN is an unambiguous input DDNAME
        # (STEP010, TRANSACT.VSAM.KSDS), independent of its DISP=SHR.
        synthetic_bd_3 = (
            original_bd + "\n\n| Stage | Input | Process | Output |\n"
            "| STEP010 | | SORT | AWS.M2.CARDDEMO.TRANSACT.VSAM.KSDS |\n"
        )
        bd_path.write_text(synthetic_bd_3, encoding="utf-8")

        summary3 = await service.build(
            BuildDocGraphRequest(source_dir=str(docs_dir), force_rebuild=True)
        )
        mismatches3 = await service.mismatches(summary3.cluster_id)
        rel_errors = [
            m
            for m in mismatches3.mismatches
            if m.mismatch_type == "relationship" and m.severity == "error"
        ]
        assert len(rel_errors) > 0, (
            "Relationship error must be flagged when BD asserts write access on a DD "
            "definitive-read (input DDNAME) file"
        )

        # R3-TESTS #5 (unverified case). BD write vs a DISP=SHR-only file (ambiguous —
        # SHR does not by itself establish read/write direction) must downgrade to
        # `unverified`, never a hard `relationship` error.
        synthetic_bd_3b = (
            original_bd + "\n\n| Stage | Input | Process | Output |\n"
            "| 1 | | CBSTM03A | AWS.M2.CARDDEMO.CARDXREF.VSAM.KSDS |\n"
        )
        bd_path.write_text(synthetic_bd_3b, encoding="utf-8")

        summary3b = await service.build(
            BuildDocGraphRequest(source_dir=str(docs_dir), force_rebuild=True)
        )
        mismatches3b = await service.mismatches(summary3b.cluster_id)
        rel_errors_3b = [
            m
            for m in mismatches3b.mismatches
            if m.mismatch_type == "relationship" and m.severity == "error"
        ]
        unverified_3b = [
            m
            for m in mismatches3b.mismatches
            if m.mismatch_type == "unverified" and "CARDXREF" in m.description
        ]
        assert len(rel_errors_3b) == 0, (
            "DISP=SHR alone must not hard-error a BD write claim (ambiguous I/O direction)"
        )
        assert len(unverified_3b) > 0, (
            "BD write vs a DISP=SHR-only file must be downgraded to an unverified/info result"
        )
        assert all(m.severity == "info" for m in unverified_3b)

        # 6d & R2-2. Value mismatch (BD §9.1 cell asserting 12 call sites instead of 13)
        synthetic_bd_4 = original_bd.replace("(13 call sites)", "(12 call sites)")
        bd_path.write_text(synthetic_bd_4, encoding="utf-8")

        summary4 = await service.build(
            BuildDocGraphRequest(source_dir=str(docs_dir), force_rebuild=True)
        )
        mismatches4 = await service.mismatches(summary4.cluster_id)
        val_mismatches = [m for m in mismatches4.mismatches if m.mismatch_type == "value"]
        assert len(val_mismatches) == 1, (
            "Exactly 1 value mismatch expected when §9.1 cell states (12 call sites), "
            f"got {len(val_mismatches)}"
        )

    finally:
        await close_db()


@pytest.mark.asyncio
async def test_doc_graph_caching_and_robustness(tmp_path, monkeypatch):
    """Test 7 & T5, §7.7 + R3-TESTS #8: Repeat build caching, changed input fingerprint
    rebuild, DD order independence, malformed docs (recorded warning, not a silent
    sparse parse), and API routes."""
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(db_dir))

    await init_db()

    try:
        docs_dir = stage_fixture_docs(tmp_path / "docs_robust")

        from domain.doc_graph.service import DocGraphService
        from domain.doc_graph.types import BuildDocGraphRequest

        service = DocGraphService()

        # 1. Build initial
        req = BuildDocGraphRequest(source_dir=str(docs_dir))
        sum1 = await service.build(req)

        # 2. Repeat build without force_rebuild -> returns cached
        sum2 = await service.build(req)
        assert sum1.cluster_id == sum2.cluster_id
        assert sum1.generated_at == sum2.generated_at

        # 3. Modify staged file -> fingerprint changes -> rebuild
        bd_file = docs_dir / "GEN.BD-CREASTMT.report.md"
        bd_file.write_text(
            bd_file.read_text(encoding="utf-8") + "\n<!-- comment -->", encoding="utf-8"
        )

        sum3 = await service.build(req)
        assert sum3.cluster_id == sum1.cluster_id

        # 4. T5 & §7.7: DD order independence (force_rebuild yields identical summary)
        sum4 = await service.build(
            BuildDocGraphRequest(source_dir=str(docs_dir), force_rebuild=True)
        )
        assert sum4.document_count == sum1.document_count
        assert sum4.node_count == sum1.node_count

        # R3-TESTS #8: genuine DD-filename order-independence — stage the identical 4
        # files into a second directory (build glob order is filesystem-dependent) and
        # assert the resulting node/edge/assertion ID *sets* are identical, not merely counts.
        docs_dir_b = tmp_path / "docs_robust_shuffled"
        docs_dir_b.mkdir()
        # Copy DD files in reverse-name order, then the BD file, to perturb filesystem
        # enumeration order relative to docs_dir's straightforward copy order.
        for fname in reversed(FIXTURE_FILES[1:]):
            shutil.copy(SOURCE_DOCS_DIR / fname, docs_dir_b / fname)
        shutil.copy(SOURCE_DOCS_DIR / FIXTURE_FILES[0], docs_dir_b / FIXTURE_FILES[0])

        sum_shuffled = await service.build(
            BuildDocGraphRequest(source_dir=str(docs_dir_b), force_rebuild=True)
        )
        nodes_a = await service.nodes(sum1.cluster_id, limit=500)
        nodes_b = await service.nodes(sum_shuffled.cluster_id, limit=500)
        edges_a = await service.edges(sum1.cluster_id, limit=2000)
        edges_b = await service.edges(sum_shuffled.cluster_id, limit=2000)
        assert {n.id for n in nodes_a.nodes} == {n.id for n in nodes_b.nodes}
        assert {(e.src_node_id, e.dst_node_id, e.edge_type, e.edge_key) for e in edges_a.edges} == {
            (e.src_node_id, e.dst_node_id, e.edge_type, e.edge_key) for e in edges_b.edges
        }
        assert sum_shuffled.assertion_count == sum1.assertion_count

        # 5. §7.7: Robustness against malformed doc with missing/renamed sections —
        # build succeeds (never a hard-fail)...
        malformed_bd = docs_dir / "GEN.BD-CREASTMT.report.md"
        malformed_content = "# Basic Design — CREASTMT\n\nNo structured tables here.\n"
        malformed_bd.write_text(malformed_content, encoding="utf-8")
        sum5 = await service.build(
            BuildDocGraphRequest(source_dir=str(docs_dir), force_rebuild=True)
        )
        assert sum5.document_count == 4
        assert sum5.cluster_id is not None

        # ...and R3-TESTS #8: it is a *recorded* warning, not a silent sparse parse —
        # verified at the parser level (ParsedDoc.warnings), since it is never a hard-fail.
        malformed_doc = parse_markdown_report(str(malformed_bd), malformed_content, "sha")
        assert len(malformed_doc.warnings) > 0, (
            "a document with no discernible section structure must record a warning"
        )
        # A well-formed doc must NOT spuriously warn.
        wellformed_doc = parse_markdown_report(
            str(bd_file),
            (SOURCE_DOCS_DIR / "GEN.BD-CREASTMT.report.md").read_text(encoding="utf-8"),
            "sha",
        )
        assert wellformed_doc.warnings == []

        # 6. REST API routes smoke test
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp_build = await client.post(
                "/api/doc-graph/build", json={"source_dir": str(docs_dir)}
            )
            assert resp_build.status_code == 200
            data = resp_build.json()
            cluster_id = data["cluster_id"]

            resp_sum = await client.get(f"/api/doc-graph/summary/{cluster_id}")
            assert resp_sum.status_code == 200

            resp_nodes = await client.get(f"/api/doc-graph/nodes/{cluster_id}?limit=10")
            assert resp_nodes.status_code == 200
            assert "nodes" in resp_nodes.json()

            resp_edges = await client.get(f"/api/doc-graph/edges/{cluster_id}?limit=10")
            assert resp_edges.status_code == 200
            assert "edges" in resp_edges.json()

            resp_mm = await client.get(f"/api/doc-graph/mismatches/{cluster_id}")
            assert resp_mm.status_code == 200
            assert "mismatches" in resp_mm.json()

            resp_exp = await client.get(f"/api/doc-graph/export/{cluster_id}")
            assert resp_exp.status_code == 200
            assert "summary" in resp_exp.json()

            # F10: an unknown cluster must 404, not silently return an empty 200 —
            # for every read route (summary already covers this pattern in the router).
            # F10: an unknown cluster must 404, not silently return an empty 200 —
            # for every read route (summary already covers this pattern in the router).
            resp_nodes_404 = await client.get("/api/doc-graph/nodes/does-not-exist")
            assert resp_nodes_404.status_code == 404
            resp_edges_404 = await client.get("/api/doc-graph/edges/does-not-exist")
            assert resp_edges_404.status_code == 404
            resp_mm_404 = await client.get("/api/doc-graph/mismatches/does-not-exist")
            assert resp_mm_404.status_code == 404

    finally:
        await close_db()


@pytest.mark.asyncio
async def test_doc_graph_llm_citation_tier(tmp_path, monkeypatch):
    """Test LLM Citation-Support Verification tier (stubbed ProviderConfigService)."""
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(db_dir))

    await init_db()

    try:
        docs_dir = stage_fixture_docs(tmp_path / "docs_llm")

        from domain.doc_graph.service import DocGraphService
        from domain.doc_graph.types import BuildDocGraphRequest
        from domain.model_connector.service import ProviderConfigService
        from domain.model_connector.types import (
            ChatResponse,
            ProviderCapabilities,
            ProviderConfig,
            ProviderKind,
        )

        fake_cfg = ProviderConfig(
            id="fake_provider",
            kind=ProviderKind.OPENAI,
            display_name="Fake Provider",
            base_url="http://fake",
            model_id="gpt-4o",
            capabilities=ProviderCapabilities(has_chat_capability=True),
        )

        async def mock_list_all(self):
            return [fake_cfg]

        async def mock_get_by_id(self, pid):
            return fake_cfg

        monkeypatch.setattr(ProviderConfigService, "list_all", mock_list_all)
        monkeypatch.setattr(ProviderConfigService, "get_by_id", mock_get_by_id)

        service = DocGraphService()

        # (e) llm_enabled=False -> 0 LLM mismatches
        sum_no_llm = await service.build(
            BuildDocGraphRequest(source_dir=str(docs_dir), llm_enabled=False, force_rebuild=True)
        )
        mms_no_llm = await service.mismatches(sum_no_llm.cluster_id)
        assert not any(m.derivation == "llm" for m in mms_no_llm.mismatches)

        # (a) CONTRADICTED verdict -> contradicted_citation error row
        call_count = 0

        async def mock_chat_contradicted(self, request):
            nonlocal call_count
            call_count += 1
            content = json.dumps({
                "verdict": "CONTRADICTED",
                "bd_quote": "BD claim text",
                "dd_quote": "DD span text",
                "uncovered_subclaims": ["subclaim 1"],
                "rationale": "DD directly contradicts BD claim.",
                "confidence": "high",
            })
            return ChatResponse(content=content, provider_id="fake_provider", model_id="gpt-4o")

        monkeypatch.setattr(ProviderConfigService, "chat", mock_chat_contradicted)

        sum_contra = await service.build(
            BuildDocGraphRequest(source_dir=str(docs_dir), force_rebuild=True)
        )
        mms_contra = await service.mismatches(sum_contra.cluster_id)
        llm_mms = [m for m in mms_contra.mismatches if m.derivation == "llm"]
        assert len(llm_mms) > 0
        assert any(
            m.mismatch_type == "contradicted_citation" and m.severity == "error" for m in llm_mms
        )

        # (f) Caching check: re-building cluster reuses cached verdict without re-calling chat stub
        initial_calls = call_count
        await service.build(
            BuildDocGraphRequest(source_dir=str(docs_dir), force_rebuild=True)
        )
        assert call_count == initial_calls, "Cached verdicts should prevent extra LLM chat calls"

        # (b) INSUFFICIENT verdict -> unsupported_claim warning row
        async def mock_chat_insufficient(self, request):
            content = json.dumps({
                "verdict": "INSUFFICIENT",
                "bd_quote": "BD claim text",
                "dd_quote": "",
                "uncovered_subclaims": [],
                "rationale": "DD text provides insufficient detail.",
                "confidence": "medium",
            })
            return ChatResponse(content=content, provider_id="fake_provider", model_id="gpt-4o")

        monkeypatch.setattr(ProviderConfigService, "chat", mock_chat_insufficient)

        # Clear verdict cache table for test
        db = get_db()
        await db.execute("DELETE FROM doc_graph_llm_cache")
        await db.commit()

        sum_insuff = await service.build(
            BuildDocGraphRequest(source_dir=str(docs_dir), force_rebuild=True)
        )
        mms_insuff = await service.mismatches(sum_insuff.cluster_id)
        llm_mms_insuff = [m for m in mms_insuff.mismatches if m.derivation == "llm"]
        assert any(
            m.mismatch_type == "unsupported_claim" and m.severity == "warning"
            for m in llm_mms_insuff
        )

        # (c) FULL_SUPPORT verdict -> 0 LLM mismatch rows
        async def mock_chat_full_support(self, request):
            content = json.dumps({
                "verdict": "FULL_SUPPORT",
                "bd_quote": "BD claim text",
                "dd_quote": "DD span text",
                "uncovered_subclaims": [],
                "rationale": "DD text fully supports BD claim.",
                "confidence": "high",
            })
            return ChatResponse(content=content, provider_id="fake_provider", model_id="gpt-4o")

        monkeypatch.setattr(ProviderConfigService, "chat", mock_chat_full_support)
        await db.execute("DELETE FROM doc_graph_llm_cache")
        await db.commit()

        sum_full = await service.build(
            BuildDocGraphRequest(source_dir=str(docs_dir), force_rebuild=True)
        )
        mms_full = await service.mismatches(sum_full.cluster_id)
        assert not any(m.derivation == "llm" for m in mms_full.mismatches)

        # (d) Malformed LLM JSON -> needs_review info row, build still succeeds
        async def mock_chat_malformed(self, request):
            return ChatResponse(
                content="NOT_VALID_JSON", provider_id="fake_provider", model_id="gpt-4o"
            )

        monkeypatch.setattr(ProviderConfigService, "chat", mock_chat_malformed)
        await db.execute("DELETE FROM doc_graph_llm_cache")
        await db.commit()

        sum_malformed = await service.build(
            BuildDocGraphRequest(source_dir=str(docs_dir), force_rebuild=True)
        )
        mms_malformed = await service.mismatches(sum_malformed.cluster_id)
        llm_mms_mal = [m for m in mms_malformed.mismatches if m.derivation == "llm"]
        assert any(m.mismatch_type == "needs_review" and m.severity == "info" for m in llm_mms_mal)

        # (e) Delete cluster test: delete_cluster removes cluster + all child rows
        cluster_id_to_delete = sum_malformed.cluster_id
        await service.delete_cluster(cluster_id_to_delete)

        # Verify cluster is gone
        clusters = await service.list_clusters()
        assert not any(c.cluster_id == cluster_id_to_delete for c in clusters)

        # Verify zero child orphan rows remain
        for table in [
            "doc_graph_documents",
            "doc_graph_assertions",
            "doc_graph_nodes",
            "doc_graph_edges",
            "doc_graph_mismatches",
        ]:
            async with db.execute(
                f"SELECT COUNT(*) as cnt FROM {table} WHERE cluster_id=?", (cluster_id_to_delete,)
            ) as cur:
                cnt = (await cur.fetchone())["cnt"]
                assert cnt == 0, f"Table {table} still has {cnt} orphan rows for deleted cluster"

    finally:
        await close_db()
