"""End-to-end integration test for CardDemo COBOL + JCL code graph build."""
import json
import pytest
from pathlib import Path

from infrastructure.db.database import get_db, init_db, close_db
from shared.utils import new_id, utc_now_iso

from domain.manifest.service import ManifestService
from domain.manifest.types import BuildManifestRequest
from domain.structural_graph.service import StructuralGraphService
from domain.structural_graph.types import BuildGraphRequest

CARDDEMO_APP_DIR = Path(r"d:\Emt\emt_data\carddemo\app")


@pytest.mark.asyncio
async def test_carddemo_e2e_code_graph(tmp_path, monkeypatch):
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(tmp_path))

    # Initialize DB schema
    await init_db()
    db = get_db()

    repo_id = f"repo-{new_id()}"
    snap_id = f"snap-{new_id()}"

    await db.execute(
        "INSERT INTO repo_snapshots (id, local_repo_id, local_path, synced_at, created_at) VALUES (?, ?, ?, ?, ?)",
        (snap_id, repo_id, str(CARDDEMO_APP_DIR), utc_now_iso(), utc_now_iso()),
    )
    await db.commit()

    try:
        # 1. Build manifest
        manifest_service = ManifestService()
        await manifest_service.build(BuildManifestRequest(snapshot_id=snap_id))

        # 2. Build structural graph
        graph_service = StructuralGraphService()
        await graph_service.build(BuildGraphRequest(snapshot_id=snap_id, force_rebuild=True))

        # 3. Export graph JSON
        graph_export = await graph_service.export_graph_json(snap_id)
        assert graph_export["stats"]["total_edge_count"] > 0

        # 4. Verify Acceptance Criteria §11

        # Fact 1: Deduplicated CBSTM03A -> CBSTM03B symbol calls edge carrying 13 evidence lines
        async with db.execute(
            "SELECT src_symbol, dst_symbol, edge_type, evidence_lines FROM symbol_graph_edges WHERE snapshot_id=? AND edge_type='calls'",
            (snap_id,),
        ) as cur:
            symbol_calls = await cur.fetchall()

        cbstm03b_symbol_edges = [
            r for r in symbol_calls
            if "CBSTM03A" in r["src_symbol"] and "CBSTM03B" in r["dst_symbol"]
        ]
        assert len(cbstm03b_symbol_edges) == 1
        evidence = json.loads(cbstm03b_symbol_edges[0]["evidence_lines"])
        assert len(evidence) == 13

        # Fact 2: Four resolved copies edges (COSTM01, CVACT03Y, CUSTREC, CVACT01Y) for CBSTM03A
        async with db.execute(
            "SELECT src_path, dst_path, edge_type FROM structural_graph_edges WHERE snapshot_id=? AND edge_type='copies' AND is_external=0",
            (snap_id,),
        ) as cur:
            copies_edges = await cur.fetchall()

        copy_targets = [r["dst_path"] for r in copies_edges if "CBSTM03A" in r["src_path"]]
        assert any("COSTM01" in t for t in copy_targets)
        assert any("CVACT03Y" in t for t in copy_targets)
        assert any("CUSTREC" in t for t in copy_targets)
        assert any("CVACT01Y" in t for t in copy_targets)
        assert len(copy_targets) == 4

        # Fact 2b: EXEC SQL INCLUDE members resolve as internal copies edges (not just
        # ordinary COPY). COTRTLIC includes CSDB2RWY/CSDB2RPY/DCLTRTYP via EXEC SQL
        # INCLUDE — these must land as edges even though the program also has COPY.
        cotrtlic_copies = [r["dst_path"] for r in copies_edges if "COTRTLIC" in r["src_path"]]
        assert any(
            m in t for t in cotrtlic_copies for m in ("CSDB2RWY", "CSDB2RPY", "DCLTRTYP")
        ), f"EXEC SQL INCLUDE members missing from COTRTLIC copies edges: {cotrtlic_copies}"

        # Fact 3: One executes edge CREASTMT.STEP040 -> CBSTM03A
        async with db.execute(
            "SELECT src_path, dst_path, edge_type FROM structural_graph_edges WHERE snapshot_id=? AND edge_type='executes' AND is_external=0",
            (snap_id,),
        ) as cur:
            exec_edges = await cur.fetchall()

        assert len(exec_edges) >= 1
        step40_exec = [r for r in exec_edges if "CREASTMT" in r["src_path"] and "CBSTM03A" in r["dst_path"]]
        assert len(step40_exec) == 1

        # Fact 4: Unique synthetic dataset nodes from STEP040
        async with db.execute(
            "SELECT dst_path FROM structural_graph_edges WHERE snapshot_id=? AND edge_type='binds_dataset' AND src_path LIKE '%CREASTMT%'",
            (snap_id,),
        ) as cur:
            dataset_edges = await cur.fetchall()

        dataset_nodes = {r["dst_path"] for r in dataset_edges}
        assert any("AWS.M2.CARDDEMO.LOADLIB" in d for d in dataset_nodes)
        assert any("AWS.M2.CARDDEMO.TRXFL.VSAM.KSDS" in d for d in dataset_nodes)
        assert any("AWS.M2.CARDDEMO.CARDXREF.VSAM.KSDS" in d for d in dataset_nodes)
        # Fact 5: Invariant check — zero dangling non-external edges (every non-external dst is in nodes)
        exp = await graph_service.export_graph_json(snap_id)
        exp_node_set = set(exp["nodes"])
        dangling_edges = [
            e for e in exp["edges"]
            if not e["external"] and e["dst"] not in exp_node_set
        ]
        assert len(dangling_edges) == 0, f"Found dangling non-external edges: {dangling_edges}"
        assert any("AWS.M2.CARDDEMO.CUSTDATA.VSAM.KSDS" in d for d in dataset_nodes)
        assert any("AWS.M2.CARDDEMO.STATEMNT.PS" in d for d in dataset_nodes)
        assert any("AWS.M2.CARDDEMO.STATEMNT.HTML" in d for d in dataset_nodes)

        # Fact 5: CEE3ABD, IDCAMS, SORT, IEFBR14 appear as external/unresolved
        async with db.execute(
            "SELECT dst_path FROM structural_graph_edges WHERE snapshot_id=? AND is_external=1",
            (snap_id,),
        ) as cur:
            external_edges = await cur.fetchall()

        ext_targets = {r["dst_path"] for r in external_edges}
        assert any("CEE3ABD" in t for t in ext_targets)
        assert any("IDCAMS" in t for t in ext_targets)
        assert any("SORT" in t for t in ext_targets)
        assert any("IEFBR14" in t for t in ext_targets)

    finally:
        await close_db()
