"""Unit and integration tests for BFI Evidence Index (Ticket P5-1)."""
from pathlib import Path
import json
import pytest

from domain.business_flow_integrity import (
    build_evidence_index,
    load_evidence_for_target,
)
from infrastructure.db.database import close_db, get_db, init_db
from shared.utils import new_id, utc_now_iso

DEV_DB_PATH = Path(r"d:\Emt\.database\codespectra.db")
DEV_SNAPSHOT_ID = "fe0868a4-7790-453f-911e-dba2d6be184e"


@pytest.mark.asyncio
async def test_evidence_index_unit_in_memory(tmp_path, monkeypatch):
    """Test evidence index creation with synthetic source_facts & diagnostics."""
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(db_dir))
    await init_db()

    try:
        db = get_db()
        snap_id = f"snap-{new_id()}"
        now = utc_now_iso()

        # Seed manifest
        manifest_files = [
            ("JOB1.jcl", "jcl"),
            ("PROG1.cbl", "cobol"),
            ("MAINPFD.pfd", "pfd"),
            ("MENU.clist", "clist"),
        ]
        for rel_p, lang in manifest_files:
            await db.execute(
                "INSERT INTO manifest_files (id, snapshot_id, rel_path, language, category, size_bytes, mtime_ns, checksum) "
                "VALUES (?, ?, ?, ?, 'source', 100, 0, 'hash')",
                (new_id(), snap_id, rel_p, lang),
            )

        # Seed source_parse_diagnostics (PROG1.cbl has parse error -> partial)
        await db.execute(
            "INSERT INTO source_parse_diagnostics (snapshot_id, rel_path, language, status, error_count, first_error, elapsed_ms, extractor_ver, created_at) "
            "VALUES (?, 'PROG1.cbl', 'cobol', 'partial', 1, 'Syntax error at line 50', 5, '1.0.0', ?)",
            (snap_id, now),
        )
        await db.execute(
            "INSERT INTO source_parse_diagnostics (snapshot_id, rel_path, language, status, error_count, first_error, elapsed_ms, extractor_ver, created_at) "
            "VALUES (?, 'JOB1.jcl', 'jcl', 'ok', 0, NULL, 5, '1.0.0', ?)",
            (snap_id, now),
        )

        # Seed source_facts
        source_facts_data = [
            # 1. COBOL branch w/ paragraph parent
            (snap_id, "PROG1.cbl", "cobol", "branch", "branch/PROG1.1000-PARA#1", 0, "paragraph/PROG1.1000-PARA", "IF", "X > 10", '{"kind": "if"}', 25, 27, "cobol_antlr", "1.0.0", now),
            # 2. COBOL branch w/ MAIN section parent
            (snap_id, "PROG1.cbl", "cobol", "branch", "branch/PROG1.MAIN#1", 0, "section/PROG1.MAIN", "EVALUATE", "STATUS-CODE", '{"kind": "evaluate"}', 50, 60, "cobol_antlr", "1.0.0", now),
            # 3. COBOL call
            (snap_id, "PROG1.cbl", "cobol", "call", "call/PROG1.2000-CAL.SUBPROG", 0, "paragraph/PROG1.2000-CAL", "SUBPROG", None, "{}", 75, 75, "cobol_antlr", "1.0.0", now),
            # 4. JCL step exec 1
            (snap_id, "JOB1.jcl", "jcl", "step", "step/JOB1.STEP01", 0, "job/JOB1", "STEP01", "PROG1", '{"type": "pgm"}', 10, 10, "jcl_antlr", "1.0.0", now),
            # 5. JCL step exec 2 (repeated PGM execution - distinct occurrence)
            (snap_id, "JOB1.jcl", "jcl", "step", "step/JOB1.STEP02", 0, "job/JOB1", "STEP02", "PROG1", '{"type": "pgm"}', 20, 20, "jcl_antlr", "1.0.0", now),
            # 6. JCL cond_gate with execute_when
            (snap_id, "JOB1.jcl", "jcl", "cond_gate", "step/JOB1.STEP02", 0, "step/JOB1.STEP02", "COND", "8", '{"raw": "8", "execute_when": "RC<=8"}', 19, 19, "jcl_fujitsu_normalizer", "1.0.0", now),
            # 7. CLIST IF guard
            (snap_id, "MENU.clist", "clist", "guard", "guard/15", 0, "clist/MENU", "IF", "&ANS=1 THEN GOTO SUB", "{}", 15, 15, "clist_regex", "1.0.0", now),
        ]

        await db.executemany(
            "INSERT INTO source_facts (snapshot_id, rel_path, language, fact_type, semantic_key, occurrence_ix, parent_key, name, value, attributes, line_start, line_end, extractor, extractor_ver, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            source_facts_data,
        )
        await db.commit()

        # Build evidence index
        res1 = await build_evidence_index(db, snap_id)

        # Assert occurrence counts
        assert res1.total_occurrences == 7
        assert res1.occurrences_by_kind["guard"] == 4  # 2 COBOL branches + 1 JCL cond_gate + 1 CLIST guard
        assert res1.occurrences_by_kind["edge"] == 3   # 1 COBOL call + 2 JCL step execs

        # Query occurrences from DB
        async with db.execute(
            "SELECT * FROM bfi_evidence_occurrences WHERE snapshot_id=? ORDER BY rel_path, line_start, occurrence_ix",
            (snap_id,),
        ) as cur:
            rows = await cur.fetchall()

        assert len(rows) == 7

        # Check parse_status propagation
        prog1_rows = [r for r in rows if r["rel_path"] == "PROG1.cbl"]
        job1_rows = [r for r in rows if r["rel_path"] == "JOB1.jcl"]
        for r in prog1_rows:
            assert r["parse_status"] == "partial"
        for r in job1_rows:
            assert r["parse_status"] == "ok"

        # Check JCL step executions preserve 2 distinct occurrence rows with real lines
        exec_rows = [r for r in job1_rows if r["edge_type"] == "executes"]
        assert len(exec_rows) == 2
        assert exec_rows[0]["src_binding"] == "STEP01"
        assert exec_rows[0]["dst_binding"] == "PROG1"
        assert exec_rows[0]["line_start"] == 10
        assert exec_rows[0]["dst_rel_path"] == "PROG1.cbl"

        assert exec_rows[1]["src_binding"] == "STEP02"
        assert exec_rows[1]["dst_binding"] == "PROG1"
        assert exec_rows[1]["line_start"] == 20
        assert exec_rows[1]["dst_rel_path"] == "PROG1.cbl"

        # Check structured guard_json for JCL cond_gate
        cond_row = next(r for r in job1_rows if r["kind"] == "guard")
        g_json = json.loads(cond_row["guard_json"])
        assert g_json["execute_when"] == "RC<=8"
        assert g_json["raw"] == "8"

        # Check deterministic ordering across 2 builds
        res2 = await build_evidence_index(db, snap_id)
        assert res2.total_occurrences == res1.total_occurrences

        async with db.execute(
            "SELECT id FROM bfi_evidence_occurrences WHERE snapshot_id=? ORDER BY rel_path, line_start, occurrence_ix",
            (snap_id,),
        ) as cur:
            rows2 = await cur.fetchall()

        assert [r["id"] for r in rows] == [r["id"] for r in rows2]

        # Test load_evidence_for_target
        loaded = await load_evidence_for_target(db, snap_id, "PROG1.cbl")
        assert len(loaded) >= 3  # PROG1.cbl as rel_path or dst_rel_path

    finally:
        await close_db()


@pytest.mark.asyncio
async def test_evidence_index_symbol_edge_derivation(tmp_path, monkeypatch):
    """Builder step 5: flow-relevant edges present only in symbol_graph_edges are materialized
    one occurrence per evidence line; non-flow edges (binds_dataset) are excluded."""
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(db_dir))
    await init_db()

    try:
        db = get_db()
        snap_id = f"snap-{new_id()}"

        for rel_p, lang in (("MENU.clist", "clist"), ("JOB1.jcl", "jcl")):
            await db.execute(
                "INSERT INTO manifest_files (id, snapshot_id, rel_path, language, category, size_bytes, mtime_ns, checksum) "
                "VALUES (?, ?, ?, ?, 'source', 100, 0, 'hash')",
                (new_id(), snap_id, rel_p, lang),
            )
        # A flow-relevant submits edge with two evidence lines, present ONLY in symbol_graph_edges.
        await db.execute(
            "INSERT INTO symbol_graph_edges (snapshot_id, src_symbol, dst_symbol, edge_type, confidence, evidence_lines, confidence_score, resolution_method) "
            "VALUES (?, 'MENU.clist::MENU', 'JOB1.jcl::JOB1', 'submits', 'high', '[5, 9]', 0.9, 'literal')",
            (snap_id,),
        )
        # A dataset-binding edge that must be IGNORED (not a control-flow edge).
        await db.execute(
            "INSERT INTO symbol_graph_edges (snapshot_id, src_symbol, dst_symbol, edge_type, confidence, evidence_lines, confidence_score, resolution_method) "
            "VALUES (?, 'JOB1.jcl::STEP', '__synthetic__/dataset/X::DATASET', 'binds_dataset', 'high', '[12]', 0.9, 'literal')",
            (snap_id,),
        )
        await db.commit()

        await build_evidence_index(db, snap_id)

        async with db.execute(
            "SELECT * FROM bfi_evidence_occurrences WHERE snapshot_id=? ORDER BY line_start",
            (snap_id,),
        ) as cur:
            rows = await cur.fetchall()

        submits = [r for r in rows if r["edge_type"] == "submits"]
        assert len(submits) == 2  # one occurrence per evidence line
        assert {r["line_start"] for r in submits} == {5, 9}
        assert submits[0]["src_binding"] == "MENU"
        assert submits[0]["dst_binding"] == "JOB1"
        assert submits[0]["dst_rel_path"] == "JOB1.jcl"
        assert all(r["edge_type"] != "binds_dataset" for r in rows)
    finally:
        await close_db()


@pytest.mark.skipif(not DEV_DB_PATH.is_file(), reason="Dev DB absent")
@pytest.mark.asyncio
async def test_evidence_index_dev_db_jcl_occurrences(tmp_path, monkeypatch):
    """Integration: run the builder on a COPY of the real dev DB and assert JCL execution
    occurrences are preserved (>= 69), not collapsed to the old ~25 file-edge count."""
    import shutil

    data_dir = tmp_path / "devcopy"
    data_dir.mkdir()
    shutil.copyfile(DEV_DB_PATH, data_dir / "codespectra.db")
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(data_dir))
    await init_db()  # applies migration 13 (bfi_evidence_occurrences) to the copy

    try:
        db = get_db()
        async with db.execute(
            "SELECT COUNT(*) c FROM source_facts WHERE snapshot_id=? AND fact_type='step'",
            (DEV_SNAPSHOT_ID,),
        ) as cur:
            raw_step_count = (await cur.fetchone())["c"]
        assert raw_step_count >= 69, f"Expected >= 69 raw step facts, got {raw_step_count}"

        res = await build_evidence_index(db, DEV_SNAPSHOT_ID)

        async with db.execute(
            "SELECT COUNT(*) c FROM bfi_evidence_occurrences WHERE snapshot_id=? AND edge_type='executes'",
            (DEV_SNAPSHOT_ID,),
        ) as cur:
            exec_count = (await cur.fetchone())["c"]

        # The whole point of P5-1: every JCL execution occurrence survives as its own row.
        assert exec_count >= 69, f"executes occurrences collapsed to {exec_count} (< 69)"
        assert res.total_occurrences > 0
    finally:
        await close_db()
