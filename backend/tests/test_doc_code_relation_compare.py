"""Unit and integration tests for Structural Link v1 Fixes."""

import json

import pytest

from domain.doc_code_compare.service import DocCodeCompareService
from infrastructure.db.database import close_db, get_db, init_db


@pytest.mark.asyncio
async def test_structural_link_fixes_bug1_to_bug4(tmp_path, monkeypatch):
    """Test 4 bugfixes: dd->dataset exclusion, eligibility, site regex, BD/DD un-merging."""
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(db_dir))

    await init_db()
    try:
        db = get_db()
        cluster_id = "cluster-cbstm03a"
        snapshot_id = "snap-carddemo-1"

        # 1. Insert cluster with matching snapshot_id
        await db.execute(
            """
            INSERT INTO doc_graph_clusters (
                id, cluster_name, source_dir, bd_path, snapshot_id,
                input_fingerprint, parser_version, created_at
            )
            VALUES (?, 'CREASTMT', '/tmp/docs', '/tmp/bd.md', ?, 'fp1', 1, datetime('now'))
            """,
            (cluster_id, snapshot_id),
        )

        # 2. Insert doc_graph_nodes
        doc_nodes = [
            (cluster_id, "program/CBSTM03A", "program", "CBSTM03A", "{}"),
            (cluster_id, "program/CBSTM03B", "program", "CBSTM03B", "{}"),
            (cluster_id, "extroutine/CEE3ABD", "extroutine", "CEE3ABD", "{}"),
            (cluster_id, "copybook/COSTM01", "copybook", "COSTM01", "{}"),
            (cluster_id, "job/CREASTMT", "job", "CREASTMT", "{}"),
            (cluster_id, "step/CREASTMT.STEP010", "step", "STEP010", "{}"),
            (cluster_id, "dd/CREASTMT.STEP010.SYSPRINT", "dd", "SYSPRINT", "{}"),
            (cluster_id, "dd/CREASTMT.STEP040.ACCTFILE", "dd", "ACCTFILE", "{}"),
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

        # 3. Insert doc_graph_assertions (including BUG 3 suffix string & BUG 1 dd->dataset)
        call_sites = [
            351, 377, 401, 734, 746, 769, 787, 805, 835, 860, 877, 893,
            "L909 (13 call sites)",
        ]
        doc_assertions = [
            # DD side calls (site-match)
            (
                cluster_id,
                "DD",
                "calls",
                "program/CBSTM03A",
                "program/CBSTM03B",
                "13",
                json.dumps({"call_sites": call_sites}),
                "doc-1",
            ),
            # BD side calls (count-only match, no call_sites) - BUG 4 unmerged row!
            (
                cluster_id,
                "BD",
                "calls",
                "program/CBSTM03A",
                "program/CBSTM03B",
                "13",
                "{}",
                "doc-1-bd",
            ),
            (
                cluster_id,
                "DD",
                "copies",
                "program/CBSTM03A",
                "copybook/COSTM01",
                "L51",
                "{}",
                "doc-1",
            ),
            (
                cluster_id,
                "BD",
                "runs",
                "job/CREASTMT",
                "step/CREASTMT.STEP010",
                None,
                "{}",
                "doc-2",
            ),
            (
                cluster_id,
                "BD",
                "binds_dd",
                "step/CREASTMT.STEP010",
                "dd/CREASTMT.STEP010.SYSPRINT",
                None,
                "{}",
                "doc-2",
            ),
            # BUG 1: dd -> dataset assertion (MUST BE EXCLUDED from per_predicate results!)
            (
                cluster_id,
                "BD",
                "binds_dd",
                "dd/CREASTMT.STEP040.ACCTFILE",
                "dataset/AWS.M2.CARDDEMO.ACCTDATA.VSAM.KSDS",
                None,
                "{}",
                "doc-2",
            ),
            # BUG 2: DOC_ONLY assertion for a non-authoritative subject
            (
                cluster_id,
                "DD",
                "calls",
                "program/UNAUTHPROG",
                "program/NONEXIST",
                "1",
                "{}",
                "doc-unauth",
            ),
        ]
        for c_id, side, pred, subj, obj, val, quals, doc_id in doc_assertions:
            await db.execute(
                """
                INSERT INTO doc_graph_assertions (
                    cluster_id, side, predicate, subject, object, value,
                    qualifiers, doc_id, confidence, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'high', datetime('now'))
                """,
                (c_id, side, pred, subj, obj, val, quals, doc_id),
            )

        # 4. Insert source_facts & symbol_graph_edges (Code Substrate)
        code_facts = [
            (
                snapshot_id,
                "app/cbstm03a.cbl",
                "cobol",
                "program",
                "program/CBSTM03A",
                0,
                None,
                "CBSTM03A",
                None,
                1,
                1000,
            ),
            (
                snapshot_id,
                "app/cbstm03b.cbl",
                "cobol",
                "program",
                "program/CBSTM03B",
                0,
                None,
                "CBSTM03B",
                None,
                1,
                500,
            ),
            (
                snapshot_id,
                "app/unauthprog.cbl",
                "cobol",
                "program",
                "program/UNAUTHPROG",
                0,
                None,
                "UNAUTHPROG",
                None,
                1,
                100,
            ),
            (
                snapshot_id,
                "jcl/creastmt.jcl",
                "jcl",
                "job",
                "job/CREASTMT",
                0,
                None,
                "CREASTMT",
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
                "dd",
                "dd/CREASTMT.STEP010.SYSPRINT",
                0,
                "step/CREASTMT.STEP010",
                "SYSPRINT",
                None,
                7,
                8,
            ),
        ]
        # Insert exact 13 call facts for CBSTM03B (lines 351..909)
        code_call_lines = [351, 377, 401, 734, 746, 769, 787, 805, 835, 860, 877, 893, 909]
        for line in code_call_lines:
            code_facts.append(
                (
                    snapshot_id,
                    "app/cbstm03a.cbl",
                    "cobol",
                    "call",
                    f"call/CBSTM03A.0000-MAIN.CBSTM03B#{line}",
                    0,
                    "program/CBSTM03A",
                    "CBSTM03B",
                    "CBSTM03B",
                    line,
                    line,
                )
            )

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

        # Insert symbol_graph_edges for copies
        await db.execute(
            """
            INSERT INTO symbol_graph_edges (
                snapshot_id, src_symbol, dst_symbol, edge_type,
                confidence_score, resolution_method
            )
            VALUES (?, 'app/cbstm03a.cbl::CBSTM03A',
                    'cpy/costm01.cpy::COSTM01', 'copies', 1.0, 'test')
            """,
            (snapshot_id,),
        )

        # Insert source_parse_diagnostics: cbstm03a and creastmt are 'ok', unauthprog is 'partial'!
        diagnostics = [
            (snapshot_id, "app/cbstm03a.cbl", "cobol", "ok"),
            (snapshot_id, "app/cbstm03b.cbl", "cobol", "ok"),
            (snapshot_id, "app/unauthprog.cbl", "cobol", "partial"),  # BUG 2 non-ok file
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

        # Run relation comparison
        service = DocCodeCompareService()
        res = await service.compare_relations(cluster_id, snapshot_id)

        assert res.status == "OK"
        pred_map = {p.predicate: p for p in res.per_predicate}

        # ── BUG 1 VERIFICATION ──
        # dd -> dataset MUST NOT be in binds_dd details and MUST NOT produce DOC_ONLY
        binds_details = pred_map["binds_dd"].details
        assert not any(d.subject_key.startswith("dd/") for d in binds_details)

        # ── BUG 2 VERIFICATION ──
        # UNAUTHPROG has DOC_ONLY relation in doc, but file status is 'partial' -> MUST be UNKNOWN!
        unauth_details = [
            d for d in pred_map["calls"].details if d.subject_key == "program/UNAUTHPROG"
        ]
        assert len(unauth_details) == 1
        assert unauth_details[0].endpoint_verdict == "UNKNOWN"
        assert unauth_details[0].eligibility == "non_authoritative"

        # ── BUG 3 & BUG 4 VERIFICATION ──
        # Check CBSTM03B details: separate rows for BD and DD
        cbstm03b_details = [
            d for d in pred_map["calls"].details if d.object_key == "program/CBSTM03B"
        ]
        assert len(cbstm03b_details) == 2  # One BD row and one DD row

        dd_row = [d for d in cbstm03b_details if d.side == "DD"][0]
        assert dd_row.endpoint_verdict == "MATCH"
        assert dd_row.multiplicity_verdict == "EXACT_SITE_MATCH"
        assert dd_row.doc_count == 13

        bd_row = [d for d in cbstm03b_details if d.side == "BD"][0]
        assert bd_row.endpoint_verdict == "MATCH"
        assert bd_row.multiplicity_verdict == "COUNT_ONLY_MATCH"
        assert bd_row.doc_count == 13

        # Total summary checks
        assert res.summary.doc_only == 0  # 0 false DOC_ONLYs!

    finally:
        await close_db()
