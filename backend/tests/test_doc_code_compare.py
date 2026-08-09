"""Unit and integration tests for Doc↔Code Completeness Compare MVP."""

import json

import pytest
from httpx import ASGITransport, AsyncClient

from domain.doc_code_compare.service import DocCodeCompareService
from infrastructure.db.database import close_db, get_db, init_db
from main import create_app


@pytest.mark.asyncio
async def test_doc_code_compare_service_and_eligibility(tmp_path, monkeypatch):
    """Test Doc↔Code comparison logic, scope isolation, and eligibility gate behavior."""
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(db_dir))

    await init_db()
    try:
        db = get_db()
        cluster_id = "cluster-test-123"
        snapshot_id = "snap-test-456"

        # 1. Insert doc_graph_clusters & doc_graph_nodes
        await db.execute(
            """
            INSERT INTO doc_graph_clusters (
                id, cluster_name, source_dir, bd_path, input_fingerprint,
                parser_version, created_at
            )
            VALUES (?, 'CREASTMT', '/tmp/docs', '/tmp/bd.md', 'fp123', 1, datetime('now'))
            """,
            (cluster_id,),
        )

        doc_nodes = [
            (cluster_id, "program/CBSTM03A", "program", "CBSTM03A", json.dumps({"doc": "DDA"})),
            (cluster_id, "program/CBSTM03B", "program", "CBSTM03B", json.dumps({"doc": "DDB"})),
            (cluster_id, "job/CREASTMT", "job", "CREASTMT", json.dumps({"doc": "BD"})),
            (cluster_id, "step/CREASTMT.STEP010", "step", "STEP010", json.dumps({"doc": "S1"})),
            (cluster_id, "step/CREASTMT.STEP020", "step", "STEP020", json.dumps({"doc": "S2"})),
            (
                cluster_id,
                "dd/CREASTMT.STEP010.SYSPRINT",
                "dd",
                "SYSPRINT",
                json.dumps({"doc": "D1"}),
            ),
            (
                cluster_id,
                "dataset/CARDDEMO.LOAD",
                "dataset",
                "CARDDEMO.LOAD",
                json.dumps({"doc": "L"}),
            ),
            (
                cluster_id,
                "dataset/MISSING.DATASET",
                "dataset",
                "MISSING.DATASET",
                json.dumps({"doc": "M"}),
            ),
        ]
        for c_id, nid, ntype, dname, prov in doc_nodes:
            await db.execute(
                """
                INSERT INTO doc_graph_nodes (
                    id, cluster_id, node_type, display_name, provenance, created_at
                )
                VALUES (?, ?, ?, ?, ?, datetime('now'))
                """,
                (nid, c_id, ntype, dname, prov),
            )

        # 2. Insert source_facts for snapshot_id
        code_facts = [
            (
                snapshot_id,
                "app/cbstm03a.cbl",
                "cobol",
                "program",
                "program/CBSTM03A",
                0,
                None,
                "A",
                None,
                1,
                10,
            ),
            (
                snapshot_id,
                "app/cbstm03b.cbl",
                "cobol",
                "program",
                "program/CBSTM03B",
                0,
                None,
                "B",
                None,
                1,
                10,
            ),
            # Program OUTSIDE cluster scope (e.g. CBSTM04C) - MUST NOT be flagged as undocumented!
            (
                snapshot_id,
                "app/cbstm04c.cbl",
                "cobol",
                "program",
                "program/CBSTM04C",
                0,
                None,
                "C",
                None,
                1,
                10,
            ),
            (
                snapshot_id,
                "jcl/creastmt.jcl",
                "jcl",
                "job",
                "job/CREASTMT",
                0,
                None,
                "JOB",
                None,
                1,
                50,
            ),
            (
                snapshot_id,
                "jcl/creastmt.jcl",
                "jcl",
                "step",
                "step/CREASTMT.STEP010",
                0,
                "job/CREASTMT",
                "STEP010",
                "IDCAMS",
                5,
                20,
            ),
            (
                snapshot_id,
                "jcl/creastmt.jcl",
                "jcl",
                "step",
                "step/CREASTMT.STEP020",
                0,
                "job/CREASTMT",
                "STEP020",
                "CBSTM03A",
                21,
                40,
            ),
            # Undocumented step in code
            (
                snapshot_id,
                "jcl/creastmt.jcl",
                "jcl",
                "step",
                "step/CREASTMT.STEP030",
                0,
                "job/CREASTMT",
                "STEP030",
                "SORT",
                41,
                50,
            ),
            (
                snapshot_id,
                "jcl/creastmt.jcl",
                "jcl",
                "dd",
                "dd/CREASTMT.STEP010.SYSPRINT",
                0,
                "step/CREASTMT.STEP010",
                "SYSPRINT",
                None,
                7,
                8,
            ),
            (
                snapshot_id,
                "jcl/creastmt.jcl",
                "jcl",
                "dataset",
                "dataset/CARDDEMO.LOAD",
                0,
                "step/CREASTMT.STEP020",
                "CARDDEMO.LOAD",
                "CARDDEMO.LOAD",
                25,
                26,
            ),
        ]
        for snap_id, rpath, lang, ftype, skey, occ, pkey, name, val, lstart, lend in code_facts:
            await db.execute(
                """
                INSERT INTO source_facts (
                    snapshot_id, rel_path, language, fact_type, semantic_key,
                    occurrence_ix, parent_key, name, value, line_start, line_end,
                    extractor, extractor_ver, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'test', '1.0', datetime('now'))
                """,
                (snap_id, rpath, lang, ftype, skey, occ, pkey, name, val, lstart, lend),
            )

        # 3. Insert source_parse_diagnostics - all 'ok'
        diagnostics = [
            (snapshot_id, "app/cbstm03a.cbl", "cobol", "ok"),
            (snapshot_id, "app/cbstm03b.cbl", "cobol", "ok"),
            (snapshot_id, "app/cbstm04c.cbl", "cobol", "ok"),
            (snapshot_id, "jcl/creastmt.jcl", "jcl", "ok"),
        ]
        for snap_id, rpath, lang, status in diagnostics:
            await db.execute(
                """
                INSERT INTO source_parse_diagnostics (
                    snapshot_id, rel_path, language, status, extractor_ver, created_at
                )
                VALUES (?, ?, ?, ?, '1.0', datetime('now'))
                """,
                (snap_id, rpath, lang, status),
            )
        await db.commit()

        # Run comparison (Authoritative = True)
        service = DocCodeCompareService()
        res = await service.compare(cluster_id, snapshot_id)

        assert res.cluster_id == cluster_id
        assert res.snapshot_id == snapshot_id
        assert res.eligibility.authoritative is True
        assert "not_assessed" in res.model_dump()
        assert len(res.not_assessed) == 9

        # Verify per_type results
        per_type_map = {item.type: item for item in res.per_type}

        # Program: 2 doc, 2 in-scope code (CBSTM04C ignored), matched 2, undocumented 0
        assert per_type_map["program"].doc_count == 2
        assert per_type_map["program"].code_count == 2
        assert per_type_map["program"].matched == 2
        assert len(per_type_map["program"].undocumented) == 0

        # Step: 2 doc, 3 code (STEP030 is undocumented), matched 2
        assert per_type_map["step"].matched == 2
        assert len(per_type_map["step"].undocumented) == 1
        assert per_type_map["step"].undocumented[0]["key"] == "step/CREASTMT.STEP030"

        # Dataset: 2 doc (CARDDEMO.LOAD & MISSING.DATASET), 1 code (CARDDEMO.LOAD), matched 1
        # Since authoritative=True, MISSING.DATASET goes to missing!
        assert per_type_map["dataset"].doc_count == 2
        assert per_type_map["dataset"].code_count == 1
        assert per_type_map["dataset"].matched == 1
        assert len(per_type_map["dataset"].missing) == 1
        assert per_type_map["dataset"].missing[0]["key"] == "dataset/MISSING.DATASET"
        assert len(per_type_map["dataset"].unknown) == 0

        # 4. Now test Eligibility Gate when a scope file has status 'partial'
        await db.execute(
            "UPDATE source_parse_diagnostics SET status='partial' WHERE rel_path='jcl/creastmt.jcl'"
        )
        await db.commit()

        res_non_auth = await service.compare(cluster_id, snapshot_id)
        assert res_non_auth.eligibility.authoritative is False
        dataset_res = {item.type: item for item in res_non_auth.per_type}["dataset"]
        # Now MISSING.DATASET must go to unknown instead of missing!
        assert len(dataset_res.missing) == 0
        assert len(dataset_res.unknown) == 1
        assert dataset_res.unknown[0]["key"] == "dataset/MISSING.DATASET"

    finally:
        await close_db()


@pytest.mark.asyncio
async def test_doc_code_compare_api_endpoint(tmp_path, monkeypatch):
    """Test API endpoint POST /api/doc-code/compare."""
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(db_dir))

    await init_db()
    try:
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/doc-code/compare",
                json={"cluster_id": "non-existent-cluster", "snapshot_id": "non-existent-snap"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["cluster_id"] == "non-existent-cluster"
            assert data["summary"]["matched"] == 0
            assert "field" in data["not_assessed"]
    finally:
        await close_db()
