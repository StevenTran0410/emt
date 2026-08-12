"""Acceptance tests for the .pfd/.ipf/.clist parsers (menu -> clist -> program
-> jcl business-flow root).

Dataset-fitted: exercises the real HSBMENU5.pfd menu, its 3 real .clist
procedures (PHNIXLOT, PHNIKLOT, HND2UP5J) and 2 real .ipf screen panels
(FHNIXLOT, FHNIKLOT) under emt_data/input_emt/Source_HSBMENU5 (never copied
into this repo). Skipped entirely when that directory isn't present.
"""
from pathlib import Path

import pytest

from domain.structural_graph._ispf import (
    build_ext_index,
    extract_clist_facts,
    extract_ipf_facts,
    extract_pfd_facts,
    resolve_clist_calls,
    resolve_clist_submits,
    resolve_menu_options,
)

SOURCE_DIR = Path(r"d:\Emt\emt_data\input_emt\Source_HSBMENU5")

pytestmark = pytest.mark.skipif(not SOURCE_DIR.is_dir(), reason="emt_data sample corpus not present")


def _read(fn: str) -> str:
    return (SOURCE_DIR / fn).read_text(encoding="utf-8")


# ──── Hand-derived ground truth (grep -nE over the real files) ─────────────

# `grep -nE "CMD\(" HSBMENU5.pfd` -> 6 TRANS(val,'CMD(target)') pairs: options
# 1/2/3/4 active, options A/B wrapped in a single-line '/* ... */' comment.
PFD_OPTIONS_TOTAL = 6
PFD_OPTIONS_ACTIVE = 4
PFD_TARGETS_ACTIVE = {"1": "PHNIXLOT", "2": "PHNIKLOT", "3": "HND2UP1J", "4": "HND2UP5J"}
# Only PHNIXLOT.clist/PHNIKLOT.clist/HND2UP5J.clist exist on disk; HND2UP1J
# has no matching .clist file in this corpus (dir listing confirmed).
PFD_LOCAL_TARGETS = {"PHNIXLOT", "PHNIKLOT", "HND2UP5J"}
PFD_EXTERNAL_TARGETS = {"HND2UP1J"}

# `grep -nc "^\s*CALL" / "^\s*ALLOCATE" / "^\s*IF "` per file, and manual read
# for SUBMIT (1 active + 1 single-line-commented duplicate in PHNIXLOT.clist).
CLIST_CALL_COUNT = {"PHNIXLOT.clist": 1, "PHNIKLOT.clist": 1, "HND2UP5J.clist": 0}
CLIST_SUBMIT_COUNT = {"PHNIXLOT.clist": 1, "PHNIKLOT.clist": 1, "HND2UP5J.clist": 1}
CLIST_ALLOCATE_COUNT = {"PHNIXLOT.clist": 7, "PHNIKLOT.clist": 6, "HND2UP5J.clist": 0}
CLIST_GUARD_COUNT = {"PHNIXLOT.clist": 3, "PHNIKLOT.clist": 3, "HND2UP5J.clist": 1}
CLIST_CALL_TARGET = {"PHNIXLOT.clist": "HNIXLOT", "PHNIKLOT.clist": "HNIKLOT"}
CLIST_SUBMIT_TARGET = {
    "PHNIXLOT.clist": "HNDM001N",
    "PHNIKLOT.clist": "HNDK001N",
    "HND2UP5J.clist": "HND2UP5J",
}

# `awk 'NR>33 && NR<290'|grep -vc "^\s*$"` (FHNIXLOT.ipf <ACTION> body) and the
# equivalent bound (NR<258) for FHNIKLOT.ipf; every non-blank line in that
# range is one ordinal field row (verified: last ordinal == count, no gaps).
IPF_FIELD_COUNT = {"FHNIXLOT.ipf": 256, "FHNIKLOT.ipf": 224}


# ──── .pfd extraction ────────────────────────────────────────────────────


def test_pfd_trans_pairs_hand_count():
    res = extract_pfd_facts(_read("HSBMENU5.pfd"))
    assert len(res.menu_options) == PFD_OPTIONS_TOTAL
    active = [o for o in res.menu_options if not o.disabled]
    disabled = [o for o in res.menu_options if o.disabled]
    assert len(active) == PFD_OPTIONS_ACTIVE
    assert len(disabled) == PFD_OPTIONS_TOTAL - PFD_OPTIONS_ACTIVE
    assert {o.option: o.target for o in active} == PFD_TARGETS_ACTIVE


def test_pfd_menu_options_resolve_local_and_external():
    res = extract_pfd_facts(_read("HSBMENU5.pfd"))
    file_set = {p.name for p in SOURCE_DIR.iterdir() if p.is_file()}
    clist_index = build_ext_index(file_set, ".clist")
    edges = resolve_menu_options("HSBMENU5.pfd", res.menu_options, clist_index)
    assert len(edges) == PFD_OPTIONS_ACTIVE
    by_target = {e.dst_file.rsplit("/", 1)[-1].split(".")[0] if e.is_external else e.dst_file: e for e in edges}
    for e in edges:
        target = e.dst_symbol.split("::")[-1] if e.dst_symbol else e.dst_file.rsplit("/", 1)[-1]
        if target in PFD_LOCAL_TARGETS:
            assert not e.is_external, e
            assert e.dst_file == f"{target}.clist"
        else:
            assert target in PFD_EXTERNAL_TARGETS, target
            assert e.is_external, e
            assert e.dst_file.startswith("__external__/clist/")


# ──── .clist extraction ──────────────────────────────────────────────────


@pytest.mark.parametrize("fn", ["PHNIXLOT.clist", "PHNIKLOT.clist", "HND2UP5J.clist"])
def test_clist_statement_counts_hand_derived(fn):
    res = extract_clist_facts(_read(fn))
    assert len(res.calls) == CLIST_CALL_COUNT[fn], fn
    assert len(res.submits) == CLIST_SUBMIT_COUNT[fn], fn
    assert len(res.allocates) == CLIST_ALLOCATE_COUNT[fn], fn
    assert len(res.guards) == CLIST_GUARD_COUNT[fn], fn


def test_clist_call_targets_resolve_to_local_cbl():
    file_set = {p.name for p in SOURCE_DIR.iterdir() if p.is_file()}
    cbl_index = build_ext_index(file_set, ".cbl")
    for fn, target in CLIST_CALL_TARGET.items():
        res = extract_clist_facts(_read(fn))
        edges = resolve_clist_calls(fn, res.calls, cbl_index)
        assert len(edges) == 1, fn
        e = edges[0]
        assert not e.is_external, (fn, e)
        assert e.dst_file == f"{target}.cbl", (fn, e)


def test_clist_submit_targets_resolve_to_local_jcl():
    """Every clist's SUBMIT resolves locally, including HND2UP5J.clist whose
    SUBMIT line sits inside an unterminated '/*' block (see extract.py
    docstring) - the ticket's hard gate requires this edge to exist."""
    file_set = {p.name for p in SOURCE_DIR.iterdir() if p.is_file()}
    jcl_index = build_ext_index(file_set, ".jcl")
    for fn, target in CLIST_SUBMIT_TARGET.items():
        res = extract_clist_facts(_read(fn))
        edges = resolve_clist_submits(fn, res.submits, jcl_index)
        assert len(edges) == 1, fn
        e = edges[0]
        assert not e.is_external, (fn, e)
        assert e.dst_file == f"{target}.jcl", (fn, e)


# ──── .ipf extraction ─────────────────────────────────────────────────────


@pytest.mark.parametrize("fn", ["FHNIXLOT.ipf", "FHNIKLOT.ipf"])
def test_ipf_action_field_count_hand_derived(fn):
    res = extract_ipf_facts(_read(fn))
    assert len(res.fields) == IPF_FIELD_COUNT[fn], fn
    # Ordinals are contiguous 1..N with no gaps or duplicates.
    assert sorted(f.ordinal for f in res.fields) == list(range(1, IPF_FIELD_COUNT[fn] + 1)), fn
    # Every field's INIT(\PARMn) ordinal matches its own row ordinal 1:1.
    assert all(f.ordinal == f.parm for f in res.fields), fn
    assert sum(1 for f in res.fields if f.cursor) == 1, fn


# ──── Real pipeline (throwaway DB) ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_real_pipeline_menu_flow(tmp_path, monkeypatch):
    """Runs the production build (ManifestService + StructuralGraphService)
    against a throwaway temp DB, mirroring test_hsbmenu5_cobol.py. Never
    touches d:\\Emt\\.database\\codespectra.db."""
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

        # 1. .pfd/.ipf/.clist now have a language and 0 failed diagnostics.
        async with db.execute(
            "SELECT rel_path, language FROM manifest_files WHERE snapshot_id=? AND rel_path IN "
            "('HSBMENU5.pfd','PHNIXLOT.clist','PHNIKLOT.clist','HND2UP5J.clist','FHNIXLOT.ipf','FHNIKLOT.ipf')",
            (snap_id,),
        ) as cur:
            mf_rows = await cur.fetchall()
        assert len(mf_rows) == 6, [dict(r) for r in mf_rows]
        expected_lang = {
            "HSBMENU5.pfd": "pfd", "PHNIXLOT.clist": "clist", "PHNIKLOT.clist": "clist",
            "HND2UP5J.clist": "clist", "FHNIXLOT.ipf": "ipf", "FHNIKLOT.ipf": "ipf",
        }
        for r in mf_rows:
            assert r["language"] == expected_lang[r["rel_path"]], dict(r)

        async with db.execute(
            "SELECT rel_path, language, status FROM source_parse_diagnostics WHERE snapshot_id=? "
            "AND language IN ('pfd','clist','ipf')",
            (snap_id,),
        ) as cur:
            diags = await cur.fetchall()
        assert len(diags) == 6, [dict(d) for d in diags]
        assert all(d["status"] != "failed" for d in diags), [dict(d) for d in diags]

        # 2. menu_option edges from HSBMENU5.pfd: 3 local, 1 external.
        async with db.execute(
            "SELECT src_path, dst_path, is_external FROM structural_graph_edges "
            "WHERE snapshot_id=? AND edge_type='menu_option'",
            (snap_id,),
        ) as cur:
            menu_edges = await cur.fetchall()
        assert len(menu_edges) == PFD_OPTIONS_ACTIVE, [dict(e) for e in menu_edges]
        local_targets = {e["dst_path"] for e in menu_edges if e["is_external"] == 0}
        assert local_targets == {f"{t}.clist" for t in PFD_LOCAL_TARGETS}
        external = [e for e in menu_edges if e["is_external"] == 1]
        assert len(external) == 1
        assert external[0]["dst_path"] == "__external__/clist/HND2UP1J"

        # 3. clist 'calls' edges (scoped to clist sources - HNIKLOT.cbl/HNIXLOT.cbl
        # also receive COBOL-internal 'calls' edges checked separately).
        async with db.execute(
            "SELECT src_path, dst_path, is_external FROM structural_graph_edges "
            "WHERE snapshot_id=? AND edge_type='calls' AND src_path LIKE '%.clist'",
            (snap_id,),
        ) as cur:
            clist_calls = await cur.fetchall()
        assert len(clist_calls) == 2, [dict(c) for c in clist_calls]
        assert {c["src_path"]: c["dst_path"] for c in clist_calls} == {
            "PHNIXLOT.clist": "HNIXLOT.cbl", "PHNIKLOT.clist": "HNIKLOT.cbl",
        }
        assert all(c["is_external"] == 0 for c in clist_calls)

        # 4. clist 'submits' edges: 3, all local.
        async with db.execute(
            "SELECT src_path, dst_path, is_external FROM structural_graph_edges "
            "WHERE snapshot_id=? AND edge_type='submits'",
            (snap_id,),
        ) as cur:
            submits = await cur.fetchall()
        assert len(submits) == 3, [dict(s) for s in submits]
        assert {s["src_path"]: s["dst_path"] for s in submits} == {
            "PHNIXLOT.clist": "HNDM001N.jcl",
            "PHNIKLOT.clist": "HNDK001N.jcl",
            "HND2UP5J.clist": "HND2UP5J.jcl",
        }
        assert all(s["is_external"] == 0 for s in submits)

        # 5. Full chain HSBMENU5 -> PHNIXLOT -> HNIXLOT -> HNDM001N is connected.
        menu_to_clist = {e["dst_path"] for e in menu_edges if e["src_path"] == "HSBMENU5.pfd" and e["is_external"] == 0}
        assert "PHNIXLOT.clist" in menu_to_clist
        clist_to_pgm = {c["dst_path"] for c in clist_calls if c["src_path"] == "PHNIXLOT.clist"}
        assert "HNIXLOT.cbl" in clist_to_pgm
        clist_to_jcl = {s["dst_path"] for s in submits if s["src_path"] == "PHNIXLOT.clist"}
        assert "HNDM001N.jcl" in clist_to_jcl

        # 5b. shows_panel edges: each COBOL program embeds its .ipf panel's
        # basename as a quoted literal (`grep -c "'FHNIXLOT'" HNIXLOT.cbl` = 31,
        # `grep -c "'FHNIKLOT'" HNIKLOT.cbl` = 31) - deterministic, exactly 2 edges.
        async with db.execute(
            "SELECT src_path, dst_path, is_external FROM structural_graph_edges "
            "WHERE snapshot_id=? AND edge_type='shows_panel'",
            (snap_id,),
        ) as cur:
            panel_edges = await cur.fetchall()
        assert len(panel_edges) == 2, [dict(p) for p in panel_edges]
        assert {p["src_path"]: p["dst_path"] for p in panel_edges} == {
            "HNIXLOT.cbl": "FHNIXLOT.ipf", "HNIKLOT.cbl": "FHNIKLOT.ipf",
        }
        assert all(p["is_external"] == 0 for p in panel_edges)
        # The two .ipf panels are no longer orphans - each has an inbound edge.
        panel_targets = {p["dst_path"] for p in panel_edges}
        assert panel_targets == {"FHNIXLOT.ipf", "FHNIKLOT.ipf"}

        # 6. Regression guard: JCL numbers from prior tickets unchanged.
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

        # Assert stacks edge emitted: HNDM001N.jcl --stacks--> HNDM004J.jcl (is_external=0)
        async with db.execute(
            "SELECT src_path, dst_path, is_external FROM structural_graph_edges "
            "WHERE snapshot_id=? AND edge_type='stacks'",
            (snap_id,),
        ) as cur:
            stack_edges = await cur.fetchall()
        assert len(stack_edges) == 1, [dict(e) for e in stack_edges]
        assert stack_edges[0]["src_path"].endswith("HNDM001N.jcl")
        assert stack_edges[0]["dst_path"].endswith("HNDM004J.jcl")
        assert stack_edges[0]["is_external"] == 0

        # 7. Regression guard: COBOL 'calls' edges (IPF* external) unchanged.
        async with db.execute(
            "SELECT dst_path, is_external FROM structural_graph_edges "
            "WHERE snapshot_id=? AND edge_type='calls' AND src_path IN ('HNIKLOT.cbl','HNIXLOT.cbl')",
            (snap_id,),
        ) as cur:
            cobol_calls = await cur.fetchall()
        assert len(cobol_calls) == 10, [dict(c) for c in cobol_calls]
        assert all(c["is_external"] == 1 and c["dst_path"].startswith("__external__/program/") for c in cobol_calls)

        async with db.execute(
            "SELECT dst_path, is_external FROM structural_graph_edges "
            "WHERE snapshot_id=? AND edge_type='copies'",
            (snap_id,),
        ) as cur:
            copies = await cur.fetchall()
        assert len(copies) == 5, [dict(c) for c in copies]
        assert all(c["is_external"] == 0 for c in copies)

        # 8. Spot-check 5 emitted facts/edges against the real files' line numbers.
        async with db.execute(
            "SELECT rel_path, fact_type, name, value, line_start FROM source_facts "
            "WHERE snapshot_id=? AND fact_type IN ('panel','field_binding','allocate','guard')",
            (snap_id,),
        ) as cur:
            ispf_facts = await cur.fetchall()
        by_key = {(r["rel_path"], r["fact_type"], r["name"]): r["line_start"] for r in ispf_facts}
        # HSBMENU5.pfd panel fact at line 1 (whole-file span).
        assert by_key.get(("HSBMENU5.pfd", "panel", "HSBMENU5")) == 1
        # PHNIXLOT.clist: ALLOCATE FILE(I01) DATASET('SHXZAIB2.K00') SHR is line 11.
        assert by_key.get(("PHNIXLOT.clist", "allocate", "I01")) == 11
        # PHNIKLOT.clist: SUBMIT-guarding IF &RETCODE=21 THEN GOTO END is line 19.
        assert by_key.get(("PHNIKLOT.clist", "guard", "IF")) is not None
        # FHNIXLOT.ipf: <ACTION> field ordinal 1 (YYMMDD) starts at line 34.
        xlot_field1 = [r for r in ispf_facts if r["rel_path"] == "FHNIXLOT.ipf" and r["name"] == "YYMMDD"]
        assert xlot_field1 and xlot_field1[0]["line_start"] == 34
        # FHNIKLOT.ipf field_binding facts total 224 (hand-derived count above).
        klot_fields = [r for r in ispf_facts if r["rel_path"] == "FHNIKLOT.ipf" and r["fact_type"] == "field_binding"]
        assert len(klot_fields) == IPF_FIELD_COUNT["FHNIKLOT.ipf"]
    finally:
        await close_db()
