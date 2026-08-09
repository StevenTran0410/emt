"""Unit and integration tests for Structural Link v1 Fixes."""

import json

import pytest

from domain.doc_code_compare.service import DocCodeCompareService
from infrastructure.db.database import close_db, get_db, init_db


@pytest.mark.asyncio
async def test_structural_link_fixes_v2(tmp_path, monkeypatch):
    """Test v2 bugfixes: Blockers 1-3 and Should-Fix 4."""
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
            (cluster_id, "program/PROG_DIFF", "program", "PROG_DIFF", "{}"),
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

        # 3. Insert doc_graph_assertions
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
            # BLOCKER 1: doc sites [10, 20] vs code sites [11, 19] (equal length, different lines!)
            (
                cluster_id,
                "DD",
                "calls",
                "program/CBSTM03A",
                "program/PROG_DIFF",
                "2",
                json.dumps({"call_sites": [10, 20]}),
                "doc-diff",
            ),
            # BD side calls (count-only match, no call_sites)
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
                "app/prog_diff.cbl",
                "cobol",
                "program",
                "program/PROG_DIFF",
                0,
                None,
                "PROG_DIFF",
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
        # Insert exact 13 call facts for CBSTM03B
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

        # BLOCKER 1 code call lines [11, 19] for PROG_DIFF
        for line in [11, 19]:
            code_facts.append(
                (
                    snapshot_id,
                    "app/cbstm03a.cbl",
                    "cobol",
                    "call",
                    f"call/CBSTM03A.0000-MAIN.PROG_DIFF#{line}",
                    0,
                    "program/CBSTM03A",
                    "PROG_DIFF",
                    "PROG_DIFF",
                    line,
                    line,
                )
            )

        # BLOCKER 3: Code relation not in doc (CODE_ONLY)
        code_facts.append(
            (
                snapshot_id,
                "app/cbstm03a.cbl",
                "cobol",
                "call",
                "call/CBSTM03A.0000-MAIN.EXTRA_PROG#999",
                0,
                "program/CBSTM03A",
                "EXTRA_PROG",
                "EXTRA_PROG",
                999,
                999,
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

        # Insert source_parse_diagnostics
        diagnostics = [
            (snapshot_id, "app/cbstm03a.cbl", "cobol", "ok"),
            (snapshot_id, "app/cbstm03b.cbl", "cobol", "ok"),
            (snapshot_id, "app/prog_diff.cbl", "cobol", "ok"),
            (snapshot_id, "app/unauthprog.cbl", "cobol", "partial"),
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

        # ── BLOCKER 1 VERIFICATION ──
        # doc [10, 20] vs code [11, 19] MUST BE COUNT_MISMATCH (not COUNT_ONLY_MATCH)!
        prog_diff_detail = [
            d for d in pred_map["calls"].details if d.object_key == "program/PROG_DIFF"
        ][0]
        assert prog_diff_detail.multiplicity_verdict == "COUNT_MISMATCH"

        # ── BLOCKER 3 VERIFICATION ──
        # CODE_ONLY relation (EXTRA_PROG) MUST BE UNKNOWN
        extra_detail = [
            d for d in pred_map["calls"].details if d.object_key == "extroutine/EXTRA_PROG"
        ][0]
        assert extra_detail.endpoint_verdict == "UNKNOWN"

        # ── BUG 1 & BUG 2 VERIFICATION ──
        binds_details = pred_map["binds_dd"].details
        assert not any(d.subject_key.startswith("dd/") for d in binds_details)

        unauth_details = [
            d for d in pred_map["calls"].details if d.subject_key == "program/UNAUTHPROG"
        ]
        assert len(unauth_details) == 1
        assert unauth_details[0].endpoint_verdict == "UNKNOWN"

    finally:
        await close_db()
