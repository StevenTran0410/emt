"""Unit and integration tests for Linked Multi-Graph aggregation endpoint (TICKET 1/3)."""

import pytest
from domain.doc_code_compare.service import DocCodeCompareService
from infrastructure.db.database import close_db, get_db, init_db


@pytest.mark.asyncio
async def test_linked_graph_aggregation_and_no_db_mutation(tmp_path, monkeypatch):
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(db_dir))

    await init_db()
    try:
        db = get_db()
        cluster_id = "cluster-linked-1"
        snapshot_id = "snap-linked-1"

        # 1. Insert cluster
        await db.execute(
            """
            INSERT INTO doc_graph_clusters (
                id, cluster_name, source_dir, bd_path, input_fingerprint,
                parser_version, created_at
            )
            VALUES (?, 'CREASTMT', '/tmp/docs', '/tmp/bd.md', 'fp123', 1, '2026-01-01T00:00:00')
            """,
            (cluster_id,),
        )

        # 2. Insert doc_graph_nodes (Doc node BD + Doc node DD + Entity nodes)
        await db.execute(
            """
            INSERT INTO doc_graph_nodes (id, cluster_id, node_type, display_name, attributes, provenance, created_at)
            VALUES ('doc-bd-1', ?, 'doc', 'BD Document', '{"doc_kind": "bd"}', '[]', '2026-01-01T00:00:00')
            """,
            (cluster_id,),
        )
        await db.execute(
            """
            INSERT INTO doc_graph_nodes (id, cluster_id, node_type, display_name, attributes, provenance, created_at)
            VALUES ('doc-dd-1', ?, 'doc', 'DD COBOL Document', '{"doc_kind": "dd_cobol"}', '[]', '2026-01-01T00:00:00')
            """,
            (cluster_id,),
        )

        # Program CBSTM03A (in BD and DD)
        await db.execute(
            """
            INSERT INTO doc_graph_nodes (id, cluster_id, node_type, display_name, attributes, provenance, created_at)
            VALUES (
                'program/CBSTM03A', ?, 'program', 'CBSTM03A', '{}',
                '[{"doc_id": "doc-bd-1"}, {"doc_id": "doc-dd-1"}]', '2026-01-01T00:00:00'
            )
            """,
            (cluster_id,),
        )

        # Program CBSTM03B (in BD only)
        await db.execute(
            """
            INSERT INTO doc_graph_nodes (id, cluster_id, node_type, display_name, attributes, provenance, created_at)
            VALUES (
                'program/CBSTM03B', ?, 'program', 'CBSTM03B', '{}',
                '[{"doc_id": "doc-bd-1"}]', '2026-01-01T00:00:00'
            )
            """,
            (cluster_id,),
        )

        # Field item (unassessed node)
        await db.execute(
            """
            INSERT INTO doc_graph_nodes (id, cluster_id, node_type, display_name, attributes, provenance, created_at)
            VALUES ('field/CUST-ID', ?, 'field', 'CUST-ID', '{}', '[]', '2026-01-01T00:00:00')
            """,
            (cluster_id,),
        )

        # 3. Insert doc_graph_edges
        await db.execute(
            """
            INSERT INTO doc_graph_edges (cluster_id, src_node_id, dst_node_id, edge_type, edge_key, attributes, created_at)
            VALUES (?, 'program/CBSTM03A', 'program/CBSTM03B', 'program_calls_program', 'calls:CBSTM03A->CBSTM03B', '{}', '2026-01-01T00:00:00')
            """,
            (cluster_id,),
        )

        # 4. Insert doc_graph_assertions
        await db.execute(
            """
            INSERT INTO doc_graph_assertions (cluster_id, side, predicate, subject, object, status, confidence, doc_id, created_at)
            VALUES (?, 'bd', 'calls', 'program/CBSTM03A', 'program/CBSTM03B', 'asserted', 'authoritative', 'doc-bd-1', '2026-01-01T00:00:00')
            """,
            (cluster_id,),
        )

        # 5. Insert source_facts for snapshot
        await db.execute(
            """
            INSERT INTO source_facts (snapshot_id, fact_type, semantic_key, rel_path, line_start, line_end, name, language, occurrence_ix, extractor, extractor_ver, created_at)
            VALUES ('snap-linked-1', 'program', 'program/CBSTM03A', 'app/cbl/CBSTM03A.CBL', 1, 100, 'CBSTM03A', 'cobol', 0, 'cobol', '1.0', '2026-01-01T00:00:00')
            """
        )
        await db.execute(
            """
            INSERT INTO source_facts (snapshot_id, fact_type, semantic_key, rel_path, line_start, line_end, name, language, occurrence_ix, extractor, extractor_ver, created_at)
            VALUES ('snap-linked-1', 'program', 'program/CBSTM03B', 'app/cbl/CBSTM03B.CBL', 1, 100, 'CBSTM03B', 'cobol', 0, 'cobol', '1.0', '2026-01-01T00:00:00')
            """
        )

        # Parse diagnostics
        await db.execute(
            """
            INSERT INTO source_parse_diagnostics (snapshot_id, rel_path, language, status, error_count, elapsed_ms, extractor_ver, created_at)
            VALUES ('snap-linked-1', 'app/cbl/CBSTM03A.CBL', 'cobol', 'ok', 0, 10, '1.0', '2026-01-01T00:00:00')
            """
        )
        await db.execute(
            """
            INSERT INTO source_parse_diagnostics (snapshot_id, rel_path, language, status, error_count, elapsed_ms, extractor_ver, created_at)
            VALUES ('snap-linked-1', 'app/cbl/CBSTM03B.CBL', 'cobol', 'ok', 0, 10, '1.0', '2026-01-01T00:00:00')
            """
        )
        # Job node (defines job scope) + an UNDOCUMENTED code step: code has step/CREASTMT.STEP99
        # but no doc node describes it — the matcher-miss must be surfaced.
        await db.execute(
            """
            INSERT INTO doc_graph_nodes (id, cluster_id, node_type, display_name, attributes, provenance, created_at)
            VALUES ('job/CREASTMT', ?, 'job', 'CREASTMT', '{}', '[{"doc_id": "doc-dd-1"}]', '2026-01-01T00:00:00')
            """,
            (cluster_id,),
        )
        await db.execute(
            """
            INSERT INTO source_facts (snapshot_id, fact_type, semantic_key, rel_path, line_start, line_end, name, language, occurrence_ix, extractor, extractor_ver, created_at)
            VALUES ('snap-linked-1', 'step', 'step/CREASTMT.STEP99', 'app/jcl/CREASTMT.JCL', 1, 5, 'STEP99', 'jcl', 0, 'jcl', '1.0', '2026-01-01T00:00:00')
            """
        )
        await db.execute(
            """
            INSERT INTO source_parse_diagnostics (snapshot_id, rel_path, language, status, error_count, elapsed_ms, extractor_ver, created_at)
            VALUES ('snap-linked-1', 'app/jcl/CREASTMT.JCL', 'jcl', 'ok', 0, 5, '1.0', '2026-01-01T00:00:00')
            """
        )

        # Regression: a JCL `runs` edge (edge_type step_runs_program) must map to the
        # `runs` predicate so its side membership resolves. A naive mapping misses it.
        await db.execute(
            """
            INSERT INTO doc_graph_nodes (id, cluster_id, node_type, display_name, attributes, provenance, created_at)
            VALUES ('step/CREASTMT.STEP01', ?, 'step', 'CREASTMT.STEP01', '{}', '[{"doc_id": "doc-dd-1"}]', '2026-01-01T00:00:00')
            """,
            (cluster_id,),
        )
        await db.execute(
            """
            INSERT INTO doc_graph_edges (cluster_id, src_node_id, dst_node_id, edge_type, edge_key, attributes, created_at)
            VALUES (?, 'step/CREASTMT.STEP01', 'program/CBSTM03A', 'step_runs_program', 'runs:STEP01->CBSTM03A', '{}', '2026-01-01T00:00:00')
            """,
            (cluster_id,),
        )
        await db.execute(
            """
            INSERT INTO doc_graph_assertions (cluster_id, side, predicate, subject, object, status, confidence, doc_id, created_at)
            VALUES (?, 'dd', 'runs', 'step/CREASTMT.STEP01', 'program/CBSTM03A', 'asserted', 'authoritative', 'doc-dd-1', '2026-01-01T00:00:00')
            """,
            (cluster_id,),
        )

        await db.commit()

        # Count DB rows before linked_graph call
        async with db.execute("SELECT count(*) FROM doc_code_relation_comparisons") as cur:
            comp_count_before = (await cur.fetchone())[0]
        async with db.execute("SELECT count(*) FROM doc_code_relation_evidence") as cur:
            ev_count_before = (await cur.fetchone())[0]

        svc = DocCodeCompareService()

        # Execute linked_graph
        resp = await svc.linked_graph(cluster_id, snapshot_id)

        # Verify NO DB mutation occurred
        async with db.execute("SELECT count(*) FROM doc_code_relation_comparisons") as cur:
            comp_count_after = (await cur.fetchone())[0]
        async with db.execute("SELECT count(*) FROM doc_code_relation_evidence") as cur:
            ev_count_after = (await cur.fetchone())[0]

        assert comp_count_before == comp_count_after
        assert ev_count_before == ev_count_after

        # Verify contract response fields
        assert resp.cluster_id == cluster_id
        assert resp.snapshot_id == snapshot_id
        assert resp.eligibility.authoritative is True

        # Verify node memberships
        node_a = next((n for n in resp.nodes if n.id == 'program/CBSTM03A'), None)
        assert node_a is not None
        assert node_a.in_bd is True
        assert node_a.in_dd is True
        assert node_a.in_code is True
        assert node_a.code_rel_path == 'app/cbl/CBSTM03A.CBL'

        node_b = next((n for n in resp.nodes if n.id == 'program/CBSTM03B'), None)
        assert node_b is not None
        assert node_b.in_bd is True
        assert node_b.in_dd is False
        assert node_b.in_code is True

        # Verify unassessed coverage counts
        assert len(resp.not_assessed.entities) > 0
        field_cov = next((e for e in resp.not_assessed.entities if e.node_type == 'field'), None)
        assert field_cov is not None
        assert field_cov.count == 1

        # Verify edge
        assert len(resp.edges) >= 1
        edge = resp.edges[0]
        assert edge.src == 'program/CBSTM03A'
        assert edge.dst == 'program/CBSTM03B'
        assert edge.in_bd is True
        assert edge.in_dd is False

        # Regression: the runs edge must resolve to the `runs` predicate and inherit dd side.
        runs_edge = next((e for e in resp.edges if e.edge_type == 'step_runs_program'), None)
        assert runs_edge is not None
        assert runs_edge.in_dd is True
        assert runs_edge.in_bd is False

        # Regression: undocumented code entity (code has it, doc doesn't) is surfaced as a node.
        undoc = next((n for n in resp.nodes if n.id == 'step/CREASTMT.STEP99'), None)
        assert undoc is not None
        assert undoc.entity_verdict == 'undocumented'
        assert undoc.in_code is True and undoc.in_dd is False and undoc.in_bd is False

        # Regression: BD groups derivation
        assert len(resp.bd_groups) >= 1
        assert resp.bd_groups[0].group_id.startswith('bd-group:')

        # Regression: BD groups wrap DD members only (Ticket 2 §3) — BD-only nodes excluded.
        assert len(resp.bd_groups) >= 1
        all_members = {m for g in resp.bd_groups for m in g.member_dd_ids}
        dd_ids = {n.id for n in resp.nodes if n.in_dd}
        assert all_members <= dd_ids
        assert 'program/CBSTM03A' in all_members  # DD member is wrapped
        assert 'program/CBSTM03B' not in all_members  # BD-only, must not be a hull member

        # Regression: scope filter returns only the seed + its 1-hop neighborhood.
        scoped = await svc.linked_graph(cluster_id, snapshot_id, scope='program/CBSTM03A')
        scoped_ids = {n.id for n in scoped.nodes}
        assert 'program/CBSTM03A' in scoped_ids
        assert 'program/CBSTM03B' in scoped_ids  # linked via calls edge
        assert 'field/CUST-ID' not in scoped_ids  # unconnected → filtered out
    finally:
        await close_db()
