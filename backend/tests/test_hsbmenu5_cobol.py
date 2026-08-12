"""Acceptance tests for HSBMENU5 COBOL extraction: misnamed .cob copybooks
(defect 1) and Japanese-katakana inline-comment lexer poisoning (defect 2).

Dataset-fitted: exercises the 2 real Fujitsu-dialect programs (HNIKLOT.cbl,
HNIXLOT.cbl) and their 5 real copybooks misnamed with a .cob extension
(JBCSIZE.cob, SHCNZAIB.cob, SHCXZAIB.cob, SHCZKIKM.cob, SHCZZAIB.cob) under
emt_data/input_emt/Source_HSBMENU5 (never copied into this repo). Skipped
entirely when that directory isn't present (e.g. CI without emt_data).

Hand-derived counts below were verified by column-7-aware grep over the real
files (sequence area cols 1-6, indicator col 7 excluding '*'/'/' comment
lines) - see inline comments for the exact method and any known gaps.
"""
from pathlib import Path

import pytest

from domain.structural_graph._cobol import (
    build_copybook_index,
    build_program_index,
    extract_cobol_all,
    extract_cobol_facts,
    is_copybook_shaped,
    resolve_cobol_copies,
)

SOURCE_DIR = Path(r"d:\Emt\emt_data\input_emt\Source_HSBMENU5")
PROGRAMS = ["HNIKLOT.cbl", "HNIXLOT.cbl"]
COPYBOOKS = ["JBCSIZE.cob", "SHCNZAIB.cob", "SHCXZAIB.cob", "SHCZKIKM.cob", "SHCZZAIB.cob"]
ALL_FILES = PROGRAMS + COPYBOOKS

pytestmark = pytest.mark.skipif(not SOURCE_DIR.is_dir(), reason="emt_data sample corpus not present")

# 01-level record count and field-entry count per copybook, hand-counted from
# level-number lines (grep -cE "^[0-9]{2}\s+[A-Z]" over the code area, col7
# comments excluded) minus the 1 record header itself.
REAL_RECORD_FIELD_COUNT = {
    "JBCSIZE.cob": (1, 24),
    "SHCNZAIB.cob": (1, 186),
    "SHCXZAIB.cob": (1, 169),
    "SHCZKIKM.cob": (1, 32),
    "SHCZZAIB.cob": (1, 187),
}

# PROCEDURE DIVISION paragraph labels (grep -nE "^[A-Z][A-Z0-9-]*\.\s*$",
# excluding the ENVIRONMENT DIVISION's "FILE-CONTROL." paragraph). Extraction
# reproduces every one of these exactly (verified line-for-line).
REAL_PARAGRAPH_COUNT = {"HNIKLOT.cbl": 26, "HNIXLOT.cbl": 28}

# Real COPY statements (col7 != '*'), keyed by source program.
REAL_COPY_MEMBERS = {
    "HNIKLOT.cbl": {"SHCZKIKM", "JBCSIZE"},
    "HNIXLOT.cbl": {"SHCXZAIB", "SHCZZAIB", "SHCNZAIB"},
}

REAL_CALL_TARGETS = {"IPFOPN", "IPFPFF", "IPFMIO", "IPFMRR", "IPFCVS"}

# PERFORM statement starts (grep for PERFORM excluding END-PERFORM). A few are
# lost to a pre-existing SLL-vs-full-LL grammar/prediction limitation in this
# ANTLR pipeline - unrelated to defect 2 and already present throughout the
# CardDemo corpus today (e.g. CBSTM03A.CBL, CBACT01C.cbl both parse "partial"
# in the accepted baseline for the identical "extraneous input '('" pattern on
# subscripted MOVE targets). Real count vs. what this pipeline recovers:
#   HNIXLOT.cbl: 29 real, 28 recovered (loses the PERFORM at line 1048)
#   HNIKLOT.cbl: 32 real, 29 recovered (loses PERFORMs at lines 822/886/951)
# Out of scope to fix (would require full LL prediction, ~40s/file - see task
# notes); asserted here as the exact, currently-recovered count.
REAL_PERFORM_RECOVERED_COUNT = {"HNIKLOT.cbl": 29, "HNIXLOT.cbl": 28}

# section name -> real line_start, hand-verified via direct grep -niE
# "SECTION\." on the raw file (PROCEDURE DIVISION sections only).
REAL_SECTIONS = {
    "HNIKLOT.cbl": {
        "A00-PROCE": 379, "B00-INIT-SEC": 394, "C00-MAIN-SEC": 440, "D00-END-SEC": 474,
        "E00-DISP-SEC": 495, "ATR-SET-FHNIKLOT-SEC": 555, "F00-DATA-SEC": 596,
        "F50-RENUM": 972, "F60-OIBAN-SET": 1011, "G00-GAMEN-CLEAR": 1044,
        "M00-OUTPUT-NEXT": 1078, "K00-BACK": 1106, "L00-NEXT": 1119, "Y00-ERR-SEC": 1144,
    },
    "HNIXLOT.cbl": {
        "A00-PROCE": 352, "B00-INIT-SEC": 367, "C00-MAIN-SEC": 410, "D00-END-SEC": 444,
        "E00-DISP-SEC": 464, "ATR-SET-FHNIXLOT-SEC": 528, "F00-DATA-SEC": 593,
        "G00-GAMEN-CLEAR": 885, "H00-ZAI-CHK": 898, "M00-OUTPUT-NEXT": 958,
        "J00-INSERT": 984, "J10-OIBAN-SET": 1060, "K00-BACK": 1093, "L00-NEXT": 1106,
        "Y00-ERR-SEC": 1131,
    },
}

# paragraph name -> real line_start, hand-verified (exact match, all 26/28).
REAL_PARAGRAPHS = {
    "HNIKLOT.cbl": {"A10-START": 380, "F10-DATA": 597},
    "HNIXLOT.cbl": {"A10-START": 353, "F10-DATA": 594},
}


def _read(fn: str) -> str:
    return (SOURCE_DIR / fn).read_text(encoding="utf-8")


# ──── Defect 1: copybook-shaped .cob files ─────────────────────────────────


def test_copybooks_detected_as_copybook_shaped():
    for fn in COPYBOOKS:
        assert is_copybook_shaped(_read(fn)), fn
    for fn in PROGRAMS:
        assert not is_copybook_shaped(_read(fn)), fn


def test_copybooks_parse_ok_with_zero_errors():
    for fn in COPYBOOKS:
        _, _, diag = extract_cobol_all(_read(fn), fn)
        assert diag["status"] == "ok", f"{fn}: {diag}"
        assert diag["error_count"] == 0, fn


def test_copybooks_produce_record_and_field_facts():
    for fn in COPYBOOKS:
        _, facts, _ = extract_cobol_all(_read(fn), fn)
        records = [f for f in facts if f["fact_type"] == "record"]
        fields = [f for f in facts if f["fact_type"] == "field"]
        exp_rec, exp_field = REAL_RECORD_FIELD_COUNT[fn]
        assert len(records) == exp_rec, fn
        assert len(fields) == exp_field, fn
        # Never a callable program - purely a data layout.
        edge_result, _, _ = extract_cobol_all(_read(fn), fn)
        assert edge_result.program_id is None, fn


def test_copybooks_not_added_to_program_index():
    """A copybook's synthetic wrapper PROGRAM-ID must never leak into the
    program index - that would let a copybook masquerade as a CALL target."""
    extracted_ids = {fn: extract_cobol_facts(_read(fn)).program_id for fn in ALL_FILES}
    program_index = build_program_index(extracted_ids)
    for fn in COPYBOOKS:
        stem = fn.rsplit(".", 1)[0]
        assert stem not in program_index


def test_copy_statements_resolve_to_local_copybooks():
    file_set = set(ALL_FILES)
    contents = {fn: _read(fn) for fn in ALL_FILES}
    copybook_shaped = {fn for fn in ALL_FILES if is_copybook_shaped(contents[fn])}
    assert copybook_shaped == set(COPYBOOKS)
    copybook_index = build_copybook_index(file_set, copybook_shaped)

    for fn, expected_members in REAL_COPY_MEMBERS.items():
        res = extract_cobol_facts(contents[fn])
        assert {c.member for c in res.copies} == expected_members, fn
        edges = resolve_cobol_copies(fn, res.program_id, [c._asdict() for c in res.copies], copybook_index)
        assert len(edges) == len(expected_members), fn
        for e in edges:
            assert not e.is_external, f"{fn} -> {e.dst_file} should resolve locally"
            assert e.dst_file in COPYBOOKS, f"{fn} -> {e.dst_file} unexpected target"


# ──── Defect 2: Japanese katakana '*>' inline comments ─────────────────────


def test_no_lexer_token_recognition_errors():
    """The '*>' floating-comment strip must eliminate every lexer-level
    'token recognition error' (there were ~160 before the fix, all from
    half-width katakana following '*>' outside column 7)."""
    for fn in PROGRAMS:
        _, _, diag = extract_cobol_all(_read(fn), fn)
        assert diag["status"] != "failed", f"{fn}: {diag}"
        assert "token recognition error" not in (diag.get("first_error") or ""), fn


def test_programs_yield_known_skeleton_sections():
    for fn in PROGRAMS:
        _, facts, _ = extract_cobol_all(_read(fn), fn)
        sections = {f["name"]: f["line_start"] for f in facts if f["fact_type"] == "section"}
        for name, line in REAL_SECTIONS[fn].items():
            assert sections.get(name) == line, f"{fn}: section {name}"


def test_programs_yield_exact_paragraph_facts():
    for fn in PROGRAMS:
        _, facts, _ = extract_cobol_all(_read(fn), fn)
        paragraphs = {f["name"]: f["line_start"] for f in facts if f["fact_type"] == "paragraph"}
        assert len(paragraphs) == REAL_PARAGRAPH_COUNT[fn], fn
        for name, line in REAL_PARAGRAPHS[fn].items():
            assert paragraphs.get(name) == line, f"{fn}: paragraph {name}"


def test_programs_yield_performs_facts():
    for fn in PROGRAMS:
        _, facts, _ = extract_cobol_all(_read(fn), fn)
        performs = [f for f in facts if f["fact_type"] == "performs"]
        assert len(performs) == REAL_PERFORM_RECOVERED_COUNT[fn], fn


def test_programs_call_known_ipf_targets():
    for fn in PROGRAMS:
        res = extract_cobol_facts(_read(fn))
        assert {c.callee for c in res.calls} == REAL_CALL_TARGETS, fn
        assert len(res.calls) == len(REAL_CALL_TARGETS), fn


# ──── Real pipeline (throwaway DB) ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_real_pipeline_build(tmp_path, monkeypatch):
    """Runs the production build (ManifestService + StructuralGraphService)
    against a throwaway temp DB, mirroring test_carddemo_integration.py.
    Never touches d:\\Emt\\.database\\codespectra.db."""
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(tmp_path))

    from infrastructure.db.database import get_db, init_db, close_db
    from shared.utils import new_id, utc_now_iso
    from domain.manifest.service import ManifestService
    from domain.manifest.types import BuildManifestRequest
    from domain.structural_graph.service import StructuralGraphService
    from domain.structural_graph.types import BuildGraphRequest

    await init_db()
    db = get_db()
    repo_id = f"repo-{new_id()}"
    snap_id = f"snap-{new_id()}"
    try:
        await db.execute(
            "INSERT INTO repo_snapshots (id, local_repo_id, local_path, synced_at, created_at) VALUES (?, ?, ?, ?, ?)",
            (snap_id, repo_id, str(SOURCE_DIR), utc_now_iso(), utc_now_iso()),
        )
        await db.commit()

        await ManifestService().build(BuildManifestRequest(snapshot_id=snap_id))
        await StructuralGraphService().build(BuildGraphRequest(snapshot_id=snap_id, force_rebuild=True))

        # 1. Zero failed cobol diagnostics across the 7 files.
        async with db.execute(
            "SELECT rel_path, status, first_error FROM source_parse_diagnostics "
            "WHERE snapshot_id=? AND language='cobol' AND rel_path IN "
            "('HNIKLOT.cbl','HNIXLOT.cbl','JBCSIZE.cob','SHCNZAIB.cob','SHCXZAIB.cob','SHCZKIKM.cob','SHCZZAIB.cob')",
            (snap_id,),
        ) as cur:
            diags = await cur.fetchall()
        assert len(diags) == 7, [dict(d) for d in diags]
        failed = [d for d in diags if d["status"] == "failed"]
        assert not failed, [dict(d) for d in failed]

        # 2. copies edges from the 2 programs point at local .cob rel_paths.
        async with db.execute(
            "SELECT src_path, dst_path, is_external FROM structural_graph_edges "
            "WHERE snapshot_id=? AND edge_type='copies'",
            (snap_id,),
        ) as cur:
            copies = await cur.fetchall()
        assert len(copies) == 5, [dict(c) for c in copies]
        for c in copies:
            assert c["is_external"] == 0, dict(c)
            assert c["dst_path"] in COPYBOOKS, dict(c)
            assert not c["dst_path"].startswith("__unresolved__"), dict(c)

        # 3. calls edges to IPF* stay external.
        # Scoped to the 2 COBOL programs: the PFD/IPF/CLIST ticket added 2 more
        # 'calls' edges (PHNIXLOT.clist/PHNIKLOT.clist -> HNIXLOT.cbl/HNIKLOT.cbl,
        # both local) from a different src_path set - see test_hsbmenu5_menu_flow.py.
        async with db.execute(
            "SELECT src_path, dst_path, is_external FROM structural_graph_edges "
            "WHERE snapshot_id=? AND edge_type='calls' AND src_path IN ('HNIKLOT.cbl','HNIXLOT.cbl')",
            (snap_id,),
        ) as cur:
            calls = await cur.fetchall()
        assert len(calls) == 10, [dict(c) for c in calls]
        for c in calls:
            assert c["is_external"] == 1, dict(c)
            assert c["dst_path"].startswith("__external__/program/")

        # 4. JCL numbers from the previous task are unchanged (regression guard).
        async with db.execute(
            "SELECT edge_type, COUNT(*) as n FROM structural_graph_edges "
            "WHERE snapshot_id=? AND edge_type IN ('executes','binds_dataset') GROUP BY edge_type",
            (snap_id,),
        ) as cur:
            jcl_edges = {r["edge_type"]: r["n"] for r in await cur.fetchall()}
        assert jcl_edges.get("executes") == 69, jcl_edges
        assert jcl_edges.get("binds_dataset") == 146, jcl_edges

        async with db.execute(
            "SELECT fact_type, COUNT(*) as n FROM source_facts "
            "WHERE snapshot_id=? AND language='jcl' AND fact_type IN ('cond_gate','stack') GROUP BY fact_type",
            (snap_id,),
        ) as cur:
            jcl_facts = {r["fact_type"]: r["n"] for r in await cur.fetchall()}
        assert jcl_facts.get("cond_gate") == 65, jcl_facts
        assert jcl_facts.get("stack") == 1, jcl_facts

        # 5. cobol source_facts section/paragraph/performs rows exist with
        # line numbers matching the real files (5 spot checks per program).
        async with db.execute(
            "SELECT rel_path, fact_type, name, line_start FROM source_facts "
            "WHERE snapshot_id=? AND language='cobol' AND fact_type IN ('section','paragraph','performs') "
            "AND rel_path IN ('HNIKLOT.cbl','HNIXLOT.cbl')",
            (snap_id,),
        ) as cur:
            skel_rows = await cur.fetchall()
        by_file = {"HNIKLOT.cbl": [], "HNIXLOT.cbl": []}
        for r in skel_rows:
            by_file[r["rel_path"]].append((r["fact_type"], r["name"], r["line_start"]))

        spot_checks = {
            "HNIKLOT.cbl": [
                ("section", "A00-PROCE", 379),
                ("section", "Y00-ERR-SEC", 1144),
                ("paragraph", "A10-START", 380),
                ("paragraph", "F10-DATA", 597),
                ("performs", "B00-INIT-SEC", 381),
            ],
            "HNIXLOT.cbl": [
                ("section", "A00-PROCE", 352),
                ("section", "Y00-ERR-SEC", 1131),
                ("paragraph", "A10-START", 353),
                ("paragraph", "F10-DATA", 594),
                ("performs", "B00-INIT-SEC", 354),
            ],
        }
        for fn, checks in spot_checks.items():
            for check in checks:
                assert check in by_file[fn], f"{fn}: expected {check} in {by_file[fn][:10]}..."
    finally:
        await close_db()
