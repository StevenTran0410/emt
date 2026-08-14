"""Unit tests for BFI deterministic snippet retrieval and citation gate (Ticket P5-2)."""
import hashlib
import pytest

from domain.business_flow_integrity import (
    build_evidence_index,
    resolve_citation,
    retrieve_unit_snippets,
)
from infrastructure.db.database import close_db, get_db, init_db
from shared.utils import new_id, utc_now_iso


@pytest.mark.asyncio
async def test_retrieval_and_citation_gate_unit(tmp_path, monkeypatch):
    """Comprehensive test suite for snippet retrieval and citation gate."""
    # Setup test workspace and DB
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    source_dir = tmp_path / "source"
    source_dir.mkdir()

    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(db_dir))
    await init_db()

    try:
        db = get_db()
        snap_id = f"snap-{new_id()}"
        repo_id = f"repo-{new_id()}"
        now = utc_now_iso()

        # Insert repo_snapshot
        await db.execute(
            "INSERT INTO repo_snapshots (id, local_repo_id, local_path, synced_at, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (snap_id, repo_id, str(source_dir), now, now),
        )

        # Create source files on disk
        # File 1: PROG1.cbl (100 lines)
        prog1_lines = [f"       * LINE {i:03d}" for i in range(1, 101)]
        prog1_lines[49] = "       IF X > 10 THEN PERFORM 1000-CAL"
        prog1_text = "\n".join(prog1_lines)
        # newline="\n" prevents Windows CRLF translation so the on-disk bytes (and their sha256,
        # the retrieval pin) match the "\n"-joined text this test hashes.
        (source_dir / "PROG1.cbl").write_text(prog1_text, encoding="utf-8", newline="\n")
        prog1_sha256 = hashlib.sha256(prog1_text.encode("utf-8")).hexdigest()

        # File 2: LONGFILE.cbl (150 lines, large lines to test 3000 char cap)
        long_lines = [f"       * LONG LINE {i:03d} " + ("X" * 50) for i in range(1, 151)]
        long_text = "\n".join(long_lines)
        (source_dir / "LONGFILE.cbl").write_text(long_text, encoding="utf-8", newline="\n")

        # Insert manifest rows (GHOST.cbl is registered but never written to disk -> FILE_UNREADABLE)
        for rel_p in ("PROG1.cbl", "LONGFILE.cbl", "GHOST.cbl"):
            await db.execute(
                "INSERT INTO manifest_files (id, snapshot_id, rel_path, language, category, size_bytes, mtime_ns, checksum) "
                "VALUES (?, ?, ?, 'cobol', 'source', 500, 0, 'hash')",
                (new_id(), snap_id, rel_p),
            )

        # Insert source_facts
        source_facts_data = [
            # PROG1 branch
            (snap_id, "PROG1.cbl", "cobol", "branch", "branch/PROG1.MAIN#1", 0, "section/PROG1.MAIN", "IF", "X > 10", '{"kind": "if"}', 50, 50, "cobol_antlr", "1.0.0", now),
            # PROG1 call
            (snap_id, "PROG1.cbl", "cobol", "call", "call/PROG1.MAIN.SUBPROG", 0, "paragraph/PROG1.MAIN", "SUBPROG", None, "{}", 80, 80, "cobol_antlr", "1.0.0", now),
            # LONGFILE branch
            (snap_id, "LONGFILE.cbl", "cobol", "branch", "branch/LONGFILE.MAIN#1", 0, "section/LONGFILE.MAIN", "IF", "Y = 1", '{"kind": "if"}', 10, 100, "cobol_antlr", "1.0.0", now),
        ]
        await db.executemany(
            "INSERT INTO source_facts (snapshot_id, rel_path, language, fact_type, semantic_key, occurrence_ix, parent_key, name, value, attributes, line_start, line_end, extractor, extractor_ver, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            source_facts_data,
        )
        await db.commit()

        # Build evidence index
        await build_evidence_index(db, snap_id)

        # -------------------------------------------------------------------
        # 1. Retrieval Tests
        # -------------------------------------------------------------------

        # A. Normal retrieval for branch unit (guard-first ordering, line padding ±10).
        # Targets are rel_paths — the realistic input from resolve_asset in P5-3.
        branch_unit = {"unit_id": "unit-1", "unit_kind": "branch", "bindings": ["PROG1.cbl"]}
        res1 = await retrieve_unit_snippets(db, snap_id, branch_unit)
        assert res1.abstain_reason is None
        assert len(res1.snippets) >= 1
        snip1 = res1.snippets[0]
        assert snip1.rel_path == "PROG1.cbl"
        # occurrence line 50 padded ±10 -> lines 40 to 60
        assert snip1.line_start == 40
        assert snip1.line_end == 60
        assert snip1.source_sha256 == prog1_sha256
        assert "IF X > 10" in snip1.text

        # B. Truncation test (LONGFILE snippet > 3000 chars -> ... [truncated])
        long_unit = {"unit_id": "unit-long", "unit_kind": "branch", "bindings": ["LONGFILE.cbl"]}
        res_long = await retrieve_unit_snippets(db, snap_id, long_unit)
        assert res_long.abstain_reason is None
        assert len(res_long.snippets) == 1
        snip_long = res_long.snippets[0]
        assert snip_long.text.endswith("... [truncated]")
        assert len(snip_long.text) <= 3100  # text capped + marker line

        # C. Abstention Reason Codes
        # C1: UNRESOLVED_ASSET
        res_unres = await retrieve_unit_snippets(db, snap_id, {"unit_id": "u-none", "bindings": []})
        assert res_unres.abstain_reason == "UNRESOLVED_ASSET"

        # C2: NO_OCCURRENCE
        res_no_occ = await retrieve_unit_snippets(db, snap_id, {"unit_id": "u-missing", "bindings": ["NONEXISTENT"]})
        assert res_no_occ.abstain_reason == "NO_OCCURRENCE"

        # C3: EXTERNAL_TARGET
        res_ext = await retrieve_unit_snippets(db, snap_id, {"unit_id": "u-ext", "bindings": ["__external__/program/FOO"]})
        assert res_ext.abstain_reason == "EXTERNAL_TARGET"

        # D. Determinism (two identical calls yield byte-identical result)
        res1_dup = await retrieve_unit_snippets(db, snap_id, branch_unit)
        assert res1 == res1_dup

        # -------------------------------------------------------------------
        # 2. Citation Gate Tests
        # -------------------------------------------------------------------

        # A. Valid citation
        cit_valid = await resolve_citation(db, snap_id, "PROG1.cbl", 45, 55)
        assert cit_valid.valid is True
        assert cit_valid.reject_reason is None
        assert cit_valid.source_sha256 == prog1_sha256
        assert "IF X > 10" in cit_valid.fetched_text
        assert len(cit_valid.fetched_text.splitlines()) == 11

        # B. Reject: NOT_IN_MANIFEST
        cit_no_manifest = await resolve_citation(db, snap_id, "UNKNOWN.cbl", 1, 10)
        assert cit_no_manifest.valid is False
        assert cit_no_manifest.reject_reason == "NOT_IN_MANIFEST"

        # C. Reject: RANGE_OUT_OF_FILE (line_end > file length or invalid bounds)
        cit_out_of_range = await resolve_citation(db, snap_id, "PROG1.cbl", 50, 200)
        assert cit_out_of_range.valid is False
        assert cit_out_of_range.reject_reason == "RANGE_OUT_OF_FILE"

        cit_bad_bounds = await resolve_citation(db, snap_id, "PROG1.cbl", 0, 10)
        assert cit_bad_bounds.valid is False
        assert cit_bad_bounds.reject_reason == "RANGE_OUT_OF_FILE"

        # D. Reject: SPAN_TOO_LONG (> 40 lines)
        cit_too_long = await resolve_citation(db, snap_id, "PROG1.cbl", 1, 50)
        assert cit_too_long.valid is False
        assert cit_too_long.reject_reason == "SPAN_TOO_LONG"

        # E. Reject: FILE_UNREADABLE (registered in manifest but absent on disk)
        cit_ghost = await resolve_citation(db, snap_id, "GHOST.cbl", 1, 5)
        assert cit_ghost.valid is False
        assert cit_ghost.reject_reason == "FILE_UNREADABLE"

    finally:
        await close_db()
