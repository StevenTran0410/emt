"""Tests for Phase 3 Code Flow extraction and Seeder (Ticket P3-1).

1. Unit tests for typed name-resolver (resolve_asset) using synthetic manifest.
2. Real pipeline oracle tests using Source_HSBMENU5 (verifying SPEC §10).
"""
from pathlib import Path
import pytest

from domain.business_flow_integrity import (
    ResolveResult,
    build_code_flow,
    resolve_asset,
    seed_files,
)
from domain.structural_graph.service import StructuralGraphService
from domain.structural_graph.types import BuildGraphRequest
from infrastructure.db.database import close_db, get_db, init_db
from shared.utils import new_id, utc_now_iso

SOURCE_DIR = Path(r"d:\Emt\emt_data\input_emt\Source_HSBMENU5")
SKIP_REASON = "Real HSBMENU5 source directory absent"

EXPECTED_12_ROUTE_FILES = {
    "HSBMENU5.pfd",
    "PHNIXLOT.clist",
    "HNIXLOT.cbl",
    "FHNIXLOT.ipf",
    "HNDM001N.jcl",
    "HNDM004J.jcl",
    "PHNIKLOT.clist",
    "HNIKLOT.cbl",
    "FHNIKLOT.ipf",
    "HNDK001N.jcl",
    "HND2UP5J.clist",
    "HND2UP5J.jcl",
}

EXPECTED_EXCLUDED_COPYBOOKS = {
    "AZAIB2.cob",
    "BZAIB2.cob",
    "CZAIB2.cob",
    "DZAIB2.cob",
    "EZAIB2.cob",
}


# ---------------------------------------------------------------------------
# 1. Typed Name-Resolver Unit Tests
# ---------------------------------------------------------------------------


def test_resolve_asset_exact_path_match():
    manifest = ["Source_HSBMENU5/HNDM001N.jcl", "Source_HSBMENU5/HNIXLOT.cbl"]
    res = resolve_asset("Source_HSBMENU5/HNDM001N.jcl", "JCL", manifest)
    assert res.status == "RESOLVED"
    assert res.rel_path == "Source_HSBMENU5/HNDM001N.jcl"


def test_resolve_asset_basename_match():
    manifest = ["a/b/HNDM001N.jcl", "a/b/HNIXLOT.cbl"]
    res = resolve_asset("HNDM001N.jcl", "JCL", manifest)
    assert res.status == "RESOLVED"
    assert res.rel_path == "a/b/HNDM001N.jcl"


def test_resolve_asset_typed_stem_match_success():
    manifest = ["Source_HSBMENU5/HNDM001N.jcl", "Source_HSBMENU5/HNIXLOT.cbl"]
    res = resolve_asset("HNDM001N", "JCL", manifest)
    assert res.status == "RESOLVED"
    assert res.rel_path == "Source_HSBMENU5/HNDM001N.jcl"


def test_resolve_asset_typed_stem_match_mismatch_unresolved():
    manifest = ["Source_HSBMENU5/HNDM001N.jcl"]
    res = resolve_asset("HNDM001N", "COBOL", manifest)
    assert res.status == "UNRESOLVED_EXTERNAL"
    assert res.rel_path is None


def test_resolve_asset_ambiguous():
    manifest = ["dirA/HNDM001N.jcl", "dirB/HNDM001N.jcl"]
    res = resolve_asset("HNDM001N", "JCL", manifest)
    assert res.status == "AMBIGUOUS"
    assert len(res.candidate_paths) == 2


# ---------------------------------------------------------------------------
# 2. Real Pipeline Acceptance Tests (SPEC §10 Oracle)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not SOURCE_DIR.is_dir(), reason=SKIP_REASON)
@pytest.mark.asyncio
async def test_bfi_code_flow_oracle_hsbmens5(tmp_path, monkeypatch):
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(db_dir))
    await init_db()

    try:
        db = get_db()
        repo_id = f"repo-{new_id()}"
        snap_id = f"snap-{new_id()}"

        await db.execute(
            "INSERT INTO repo_snapshots (id, local_repo_id, local_path, synced_at, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (snap_id, repo_id, str(SOURCE_DIR), utc_now_iso(), utc_now_iso()),
        )
        await db.commit()

        # Step A: Manifest & Structural graph build
        from domain.manifest.service import ManifestService
        from domain.manifest.types import BuildManifestRequest

        await ManifestService().build(BuildManifestRequest(snapshot_id=snap_id))

        service = StructuralGraphService()
        await service.build(BuildGraphRequest(snapshot_id=snap_id, force_rebuild=True))

        # Step B: Code flow extraction & seeder
        res = await build_code_flow(db, snap_id)

        # 1. Oracle check: 12 route files selected in seed set
        seed_basenames = {Path(s.rel_path).name for s in res.seed_files}
        assert seed_basenames == EXPECTED_12_ROUTE_FILES, f"Actual seed basenames: {seed_basenames}"
        assert len(res.seed_files) == 12

        # 2. Copybooks (.cob) strictly excluded from seed set
        for cb in EXPECTED_EXCLUDED_COPYBOOKS:
            assert cb not in seed_basenames

        # 3. HNDM004J.jcl reachable via stacks edge from HNDM001N.jcl
        hndm004j = next(s for s in res.seed_files if s.rel_path.endswith("HNDM004J.jcl"))
        assert hndm004j.reachable is True
        assert hndm004j.subpath_edges == ["menu_option", "submits", "stacks"]

        # 4. Check coarse subpath chains for key route files
        subpaths_by_name = {Path(s.rel_path).name: s.subpath_edges for s in res.seed_files}
        assert subpaths_by_name["HSBMENU5.pfd"] == []
        assert subpaths_by_name["PHNIXLOT.clist"] == ["menu_option"]
        assert subpaths_by_name["HNIXLOT.cbl"] == ["menu_option", "calls"]
        assert subpaths_by_name["FHNIXLOT.ipf"] == ["menu_option", "calls", "shows_panel"]
        assert subpaths_by_name["HNDM001N.jcl"] == ["menu_option", "submits"]

        # 5. JCL job node count = 1 per job file (coarse route-level collapse)
        async with db.execute(
            "SELECT COUNT(*) as cnt FROM code_flow_nodes WHERE snapshot_id=? AND node_kind='job'",
            (snap_id,),
        ) as cur:
            job_node_cnt = (await cur.fetchone())["cnt"]
        # HNDM001N, HNDM004J, HNDK001N, HND2UP5J
        assert job_node_cnt == 4

        # 6. External boundary nodes exist and are recorded
        async with db.execute(
            "SELECT rel_path FROM code_flow_nodes WHERE snapshot_id=? AND node_kind='external'",
            (snap_id,),
        ) as cur:
            ext_nodes = [r["rel_path"] for r in await cur.fetchall()]
        assert any("HNDM130" in p or "FTP0015" in p for p in ext_nodes)

        # 7. seed_files reader function returns identical seed list
        persisted_seeds = await seed_files(db, snap_id)
        persisted_basenames = {Path(s.rel_path).name for s in persisted_seeds}
        assert persisted_basenames == EXPECTED_12_ROUTE_FILES

    finally:
        await close_db()
