"""Acceptance tests for TICKET P5-3 / P6 (source-aware unit verifier + immutable run artifacts).

OFFLINE ONLY — every provider call is a monkeypatched stub, no real LLM/network calls.
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from domain.business_flow_integrity import EnrichResult, build_evidence_index, run_source_aware_verdicts
from domain.model_connector.service import ProviderConfigService
from infrastructure.db.database import close_db, get_db, init_db
from shared.utils import new_id, utc_now_iso


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


def _write_prog1(source_dir: Path) -> None:
    """A 100-line COBOL source file with a real branch statement at line 50."""
    lines = [f"       * LINE {i:03d}" for i in range(1, 101)]
    lines[49] = "       IF X > 10 THEN PERFORM 1000-CAL"
    text = "\n".join(lines)
    (source_dir / "PROG1.cbl").write_text(text, encoding="utf-8", newline="\n")


async def _seed_common(db, snap_id: str, repo_id: str, source_dir: Path) -> None:
    """repo_snapshot + manifest_files + one evidence occurrence, all on PROG1.cbl."""
    now = utc_now_iso()
    await db.execute(
        "INSERT INTO repo_snapshots (id, local_repo_id, local_path, synced_at, created_at) VALUES (?, ?, ?, ?, ?)",
        (snap_id, repo_id, str(source_dir), now, now),
    )
    await db.execute(
        "INSERT INTO manifest_files (id, snapshot_id, rel_path, language, category, size_bytes, mtime_ns, checksum) "
        "VALUES (?, ?, 'PROG1.cbl', 'cobol', 'source', 500, 0, 'hash')",
        (new_id(), snap_id),
    )
    await db.execute(
        "INSERT INTO source_facts (snapshot_id, rel_path, language, fact_type, semantic_key, occurrence_ix, parent_key, name, value, attributes, line_start, line_end, extractor, extractor_ver, created_at) "
        "VALUES (?, 'PROG1.cbl', 'cobol', 'branch', 'branch/PROG1.MAIN#1', 0, 'section/PROG1.MAIN', 'IF', 'X > 10', '{\"kind\": \"if\"}', 50, 50, 'cobol_antlr', '1.0.0', ?)",
        (snap_id, now),
    )
    await db.commit()
    await build_evidence_index(db, snap_id)


async def _seed_flow(db, cluster_id: str) -> str:
    now = utc_now_iso()
    flow_id = f"bf:{new_id()}"
    await db.execute(
        "INSERT INTO bd_business_flows (id, cluster_id, doc_id, sub_ix, block_key, name, description, ordinal, origin, model_id, created_at) "
        "VALUES (?, ?, 'doc1', 2, 'blk1', 'Test Flow', 'desc', 1, 'fallback', NULL, ?)",
        (flow_id, cluster_id, now),
    )
    await db.commit()
    return flow_id


async def _seed_step(
    db, cluster_id: str, flow_id: str, binding: str | None, ordinal: int = 1,
    name: str = "Test Step", functionality: str = "Does the test thing.",
    doc_line_start: int = 10, doc_line_end: int = 12,
) -> tuple[str, str | None]:
    """Insert one bd_business_steps row, plus a bd_flow_nodes row when `binding` is given.

    `binding` is deliberately the BARE program name ("PROG1", no extension) — exercising the
    ticket's "CRITICAL correctness trap": _verifier.py must resolve this to "PROG1.cbl" via
    resolve_asset BEFORE calling retrieve_unit_snippets, never pass the bare token through.
    """
    now = utc_now_iso()
    step_id = f"bs:{new_id()}"
    node_id: str | None = None
    source_node_ids = "[]"
    if binding is not None:
        node_id = f"bdnode:{new_id()}"
        await db.execute(
            "INSERT INTO bd_flow_nodes (id, cluster_id, doc_id, node_kind, binding, binding_type, provenance_tier, created_at) "
            "VALUES (?, ?, 'doc1', 'step', ?, 'PROGRAM', 'declared', ?)",
            (node_id, cluster_id, binding, now),
        )
        source_node_ids = json.dumps([node_id])
    await db.execute(
        "INSERT INTO bd_business_steps (id, flow_id, name, functionality, ordinal, source_node_ids, doc_line_start, doc_line_end, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (step_id, flow_id, name, functionality, ordinal, source_node_ids, doc_line_start, doc_line_end, now),
    )
    await db.commit()
    return step_id, node_id


async def _mark_contradicted(db, cluster_id: str, snap_id: str, bd_node_id: str) -> None:
    """Insert a flow_alignment DOC_CONTRADICTED row (the same signal run_unit_verdicts' floor uses)."""
    now = utc_now_iso()
    await db.execute(
        "INSERT INTO flow_alignment (id, cluster_id, snapshot_id, bd_node_id, code_node_id, match_method, tag, ordinal_in_subpath, created_at) "
        "VALUES (?, ?, ?, ?, NULL, 'unresolved_token', 'DOC_CONTRADICTED', 1, ?)",
        (f"align:{new_id()}", cluster_id, snap_id, bd_node_id, now),
    )
    await db.commit()


def _fake_result(
    uid: str, verdict: str = "MATCH", citations: list[dict] | None = None,
    aspects: dict[str, str] | None = None, reason_codes: list[str] | None = None,
    reason: str = "Snippet confirms the claim.",
) -> dict:
    if citations is None:
        citations = [{"rel_path": "PROG1.cbl", "line_start": 41, "line_end": 45}]
    if aspects is None:
        aspects = {
            "target_reachable": "YES", "guard_equivalence": "NOT_APPLICABLE",
            "route_order": "YES", "negative_modality": "NOT_APPLICABLE",
        }
    if reason_codes is None:
        reason_codes = ["target_reachable_confirmed"]
    return {
        "unit_id": uid, "subclaims": ["Test subclaim."], "aspects": aspects,
        "citations": citations, "verdict": verdict, "reason_codes": reason_codes, "reason": reason,
    }


async def _fetch_uv_map(db, cluster_id: str, snap_id: str) -> dict[str, dict]:
    async with db.execute(
        "SELECT unit_id, unit_kind, mapping_status, mapping_method, verdict, guard_verdict, ai_bucket, reason, evidence_json, comparator_version "
        "FROM business_unit_verdicts WHERE cluster_id=? AND snapshot_id=?",
        (cluster_id, snap_id),
    ) as cur:
        return {r["unit_id"]: dict(r) for r in await cur.fetchall()}


async def _fetch_artifacts(db, unit_id: str) -> list[dict]:
    async with db.execute(
        "SELECT id, run_id, payload, created_at FROM bfi_run_artifacts WHERE unit_id=? ORDER BY created_at ASC",
        (unit_id,),
    ) as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    for r in rows:
        r["payload"] = json.loads(r["payload"])
    return rows


def _make_chat_stream_stub(verifier_stub):
    """Wrap a verifier stub so that LLM #1 Enricher and LLM #2 Matcher calls pass automatically."""
    async def _routed_stub(self, request):
        sys_content = request.messages[0].content
        if "mainframe codebase binding enricher" in sys_content:
            payload = json.loads(request.messages[1].content)
            results = [
                {
                    "unit_id": u["unit_id"],
                    "confirmed_rel_paths": u.get("seed_rel_paths") or (["PROG1.cbl"] if u.get("binding") == "PROG1" else []),
                    "no_file": not bool(u.get("seed_rel_paths") or u.get("binding") == "PROG1"),
                    "reason": "Confirmed PROG1.cbl" if (u.get("seed_rel_paths") or u.get("binding") == "PROG1") else "No file",
                }
                for u in payload.get("units", [])
            ]
            yield {"type": "content", "text": json.dumps({"results": results})}
            return
        if "mainframe codebase business flow matcher" in sys_content:
            payload = json.loads(request.messages[1].content)
            results = []
            for u in payload:
                snippets = u.get("snippets", [])
                if snippets:
                    s = snippets[0]
                    cits = [{"rel_path": s["rel_path"], "line_start": s["line_start"], "line_end": s["line_end"]}]
                    maps = True
                else:
                    cits = []
                    maps = False
                results.append({
                    "unit_id": u["unit_id"],
                    "subclaims": ["Matches snippet"],
                    "citations": cits,
                    "maps_to_code": maps,
                    "reason": "Matched" if maps else "No snippet",
                })
            yield {"type": "content", "text": json.dumps({"results": results})}
            return

        async for evt in verifier_stub(self, request):
            yield evt

    return _routed_stub


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batching_le5_and_unit_id_coverage_enforcement(tmp_path, monkeypatch):
    """7 ready units -> 2 batches, never >5/call; a model that omits an id forces the ladder's
    one low-effort retry (P5-0 discipline), which the caller re-uses to recover full coverage."""
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
        cluster_id = f"cluster-{new_id()}"
        _write_prog1(source_dir)
        await _seed_common(db, snap_id, repo_id, source_dir)
        flow_id = await _seed_flow(db, cluster_id)

        step_ids = []
        for i in range(7):
            sid, _ = await _seed_step(db, cluster_id, flow_id, binding="PROG1", ordinal=i + 1, name=f"Step {i}")
            step_ids.append(sid)

        captured = []

        async def stub(self, request):
            captured.append(request)
            payload = json.loads(request.messages[1].content)
            unit_ids = [item["unit_id"] for item in payload]
            assert len(unit_ids) <= 5, "batch must never exceed 5 units"
            assert all(item["snippets"] for item in payload), "every ready unit must carry >=1 snippet"
            if request.reasoning_effort == "high":
                ids_to_return = unit_ids[:-1]  # misbehave: drop last id -> coverage mismatch
            else:
                ids_to_return = unit_ids
            results = [_fake_result(uid) for uid in ids_to_return]
            yield {"type": "content", "text": json.dumps({"results": results})}

        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", _make_chat_stream_stub(stub))

        result = await run_source_aware_verdicts(db, cluster_id, snap_id, provider_id="stub-provider")

        assert result.total_units == 7
        assert result.llm_batches_issued == 6  # 2 enrich + 2 match + 2 verify

        efforts_seen = [r.reasoning_effort for r in captured]
        assert efforts_seen.count("high") == 2
        assert efforts_seen.count("low") == 2  # both batches needed the coverage-mismatch retry

        uv_map = await _fetch_uv_map(db, cluster_id, snap_id)
        assert len(uv_map) == 7
        for sid in step_ids:
            assert uv_map[sid]["verdict"] == "MATCH"
            assert uv_map[sid]["comparator_version"] == 3
    finally:
        await close_db()


@pytest.mark.asyncio
async def test_invalid_citation_downgrades_to_unknown(tmp_path, monkeypatch):
    """Rule 1: ANY invalid citation forces INSUFFICIENT_EVIDENCE (stored UNKNOWN); the model's
    original verdict is kept in the artifact for audit."""
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
        cluster_id = f"cluster-{new_id()}"
        _write_prog1(source_dir)
        await _seed_common(db, snap_id, repo_id, source_dir)
        flow_id = await _seed_flow(db, cluster_id)
        step_id, _ = await _seed_step(db, cluster_id, flow_id, binding="PROG1")

        async def stub(self, request):
            payload = json.loads(request.messages[1].content)
            uid = payload[0]["unit_id"]
            # line_end=210 is out of PROG1.cbl's 100 lines -> RANGE_OUT_OF_FILE.
            res = _fake_result(uid, verdict="MATCH", citations=[{"rel_path": "PROG1.cbl", "line_start": 200, "line_end": 210}])
            yield {"type": "content", "text": json.dumps({"results": [res]})}

        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", _make_chat_stream_stub(stub))
        await run_source_aware_verdicts(db, cluster_id, snap_id, provider_id="stub-provider")

        uv_map = await _fetch_uv_map(db, cluster_id, snap_id)
        row = uv_map[step_id]
        assert row["verdict"] == "UNKNOWN"
        evidence = json.loads(row["evidence_json"])
        assert "CITATION_INVALID" in evidence["reason_codes"]
        assert evidence["model_verdict"] == "MATCH"  # audit trail keeps the model's raw verdict

        artifacts = await _fetch_artifacts(db, step_id)
        assert len(artifacts) == 1
        payload = artifacts[0]["payload"]
        assert payload["model_raw_output"]["verdict"] == "MATCH"
        assert payload["fused_verdict"] == "INSUFFICIENT_EVIDENCE"
        assert payload["citation_resolutions"][0]["valid"] is False
        assert payload["citation_resolutions"][0]["reject_reason"] == "RANGE_OUT_OF_FILE"
    finally:
        await close_db()


@pytest.mark.asyncio
async def test_match_without_citation_downgraded(tmp_path, monkeypatch):
    """Rule 2: MATCH with zero citations -> INSUFFICIENT_EVIDENCE (stored UNKNOWN), reason NO_CITATION."""
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
        cluster_id = f"cluster-{new_id()}"
        _write_prog1(source_dir)
        await _seed_common(db, snap_id, repo_id, source_dir)
        flow_id = await _seed_flow(db, cluster_id)
        step_id, _ = await _seed_step(db, cluster_id, flow_id, binding="PROG1")

        async def stub(self, request):
            payload = json.loads(request.messages[1].content)
            uid = payload[0]["unit_id"]
            res = _fake_result(uid, verdict="MATCH", citations=[])
            yield {"type": "content", "text": json.dumps({"results": [res]})}

        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", _make_chat_stream_stub(stub))
        await run_source_aware_verdicts(db, cluster_id, snap_id, provider_id="stub-provider")

        uv_map = await _fetch_uv_map(db, cluster_id, snap_id)
        row = uv_map[step_id]
        assert row["verdict"] == "UNKNOWN"
        evidence = json.loads(row["evidence_json"])
        assert "NO_CITATION" in evidence["reason_codes"]
    finally:
        await close_db()


@pytest.mark.asyncio
async def test_contradiction_floor_holds_even_when_model_says_match(tmp_path, monkeypatch):
    """Rule 3 (kept floor): a resolved DOC_CONTRADICTED alignment forces BROKEN regardless of a
    MATCH verdict returned by the (mocked) LLM."""
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
        cluster_id = f"cluster-{new_id()}"
        _write_prog1(source_dir)
        await _seed_common(db, snap_id, repo_id, source_dir)
        flow_id = await _seed_flow(db, cluster_id)
        step_id, node_id = await _seed_step(db, cluster_id, flow_id, binding="PROG1")
        await _mark_contradicted(db, cluster_id, snap_id, node_id)

        async def stub(self, request):
            payload = json.loads(request.messages[1].content)
            uid = payload[0]["unit_id"]
            yield {"type": "content", "text": json.dumps({"results": [_fake_result(uid, verdict="MATCH")]})}

        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", _make_chat_stream_stub(stub))
        await run_source_aware_verdicts(db, cluster_id, snap_id, provider_id="stub-provider")

        uv_map = await _fetch_uv_map(db, cluster_id, snap_id)
        row = uv_map[step_id]
        assert row["verdict"] == "BROKEN"
        assert row["ai_bucket"] == "stale_missing"
        assert "contradicted by code graph" in row["reason"]
        evidence = json.loads(row["evidence_json"])
        assert "CONTRADICTION_FLOOR" in evidence["reason_codes"]
        assert evidence["model_verdict"] == "MATCH"  # model output kept for audit despite the floor
    finally:
        await close_db()


@pytest.mark.asyncio
async def test_abstained_units_bypass_llm(tmp_path, monkeypatch):
    """Units abstained on (here: UNRESOLVED_ASSET, no bd_flow_nodes binding at all) never
    reach the LLM — the batched prompt must not contain their unit_id."""
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
        cluster_id = f"cluster-{new_id()}"
        _write_prog1(source_dir)
        await _seed_common(db, snap_id, repo_id, source_dir)
        flow_a_id = await _seed_flow(db, cluster_id)
        flow_b_id = await _seed_flow(db, cluster_id)
        step_abstain_id, _ = await _seed_step(db, cluster_id, flow_b_id, binding=None, ordinal=1, name="Abstain Step")
        step_ready_id, _ = await _seed_step(db, cluster_id, flow_a_id, binding="PROG1", ordinal=2, name="Ready Step")

        captured_ids: list[str] = []

        async def stub(self, request):
            payload = json.loads(request.messages[1].content)
            for item in payload:
                captured_ids.append(item["unit_id"])
            results = [_fake_result(item["unit_id"]) for item in payload]
            yield {"type": "content", "text": json.dumps({"results": results})}

        # Enricher mock: keep abstain step residual (empty confirmed paths, no_file=False)
        async def mock_enrich(db, snap, units, manifest, provider):
            res = {}
            for u in units:
                if u.get("binding") == "PROG1":
                    res[u["unit_id"]] = EnrichResult(
                        unit_id=u["unit_id"], confirmed_rel_paths=["PROG1.cbl"], no_file=False, reason="Confirmed", agent_raw=None, retry_count=0
                    )
                else:
                    res[u["unit_id"]] = EnrichResult(
                        unit_id=u["unit_id"], confirmed_rel_paths=[], no_file=False, reason="Unresolved", agent_raw=None, retry_count=0
                    )
            return res

        monkeypatch.setattr("domain.business_flow_integrity._verifier.enrich_unit_bindings", mock_enrich)
        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", _make_chat_stream_stub(stub))
        result = await run_source_aware_verdicts(db, cluster_id, snap_id, provider_id="stub-provider")

        assert step_abstain_id not in captured_ids
        assert len(captured_ids) == 1
        assert "u1" in captured_ids

        uv_map = await _fetch_uv_map(db, cluster_id, snap_id)
        abstain_row = uv_map[step_abstain_id]
        assert abstain_row["verdict"] == "UNKNOWN"
        evidence = json.loads(abstain_row["evidence_json"])
        assert evidence["reason_codes"] == ["UNRESOLVED_ASSET"]
        assert uv_map[step_ready_id]["verdict"] == "MATCH"
        assert result.total_units == 2
    finally:
        await close_db()


@pytest.mark.asyncio
async def test_artifacts_appended_second_run_new_run_id_old_rows_untouched(tmp_path, monkeypatch):
    """bfi_run_artifacts is append-only: a second Run adds a new row with a new run_id and never
    mutates the first row; business_unit_verdicts stays delete+replace (latest-state), not append-only."""
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
        cluster_id = f"cluster-{new_id()}"
        _write_prog1(source_dir)
        await _seed_common(db, snap_id, repo_id, source_dir)
        flow_id = await _seed_flow(db, cluster_id)
        step_id, _ = await _seed_step(db, cluster_id, flow_id, binding="PROG1")

        result1 = await run_source_aware_verdicts(db, cluster_id, snap_id, provider_id=None)
        artifacts1 = await _fetch_artifacts(db, step_id)
        assert len(artifacts1) == 1
        first_row = artifacts1[0]

        result2 = await run_source_aware_verdicts(db, cluster_id, snap_id, provider_id=None)
        assert result2.run_id != result1.run_id

        artifacts2 = await _fetch_artifacts(db, step_id)
        assert len(artifacts2) == 2
        assert artifacts2[0]["id"] == first_row["id"]
        assert artifacts2[0]["run_id"] == result1.run_id
        assert artifacts2[0]["payload"] == first_row["payload"]  # untouched
        assert artifacts2[1]["run_id"] == result2.run_id

        async with db.execute(
            "SELECT COUNT(*) c FROM business_unit_verdicts WHERE cluster_id=? AND snapshot_id=?",
            (cluster_id, snap_id),
        ) as cur:
            cnt = (await cur.fetchone())["c"]
        assert cnt == 1  # verdicts are delete+replace, not append-only
    finally:
        await close_db()


@pytest.mark.asyncio
async def test_offline_path_provider_none_end_to_end(tmp_path, monkeypatch):
    """provider_id=None: no LLM call is ever attempted; verdicts are abstentions + the
    contradiction floor only, and artifacts are still written."""
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
        cluster_id = f"cluster-{new_id()}"
        _write_prog1(source_dir)
        await _seed_common(db, snap_id, repo_id, source_dir)
        flow_a_id = await _seed_flow(db, cluster_id)
        flow_b_id = await _seed_flow(db, cluster_id)

        step_ready_id, _ = await _seed_step(db, cluster_id, flow_a_id, binding="PROG1", ordinal=1, name="Ready Step")
        step_abstain_id, _ = await _seed_step(db, cluster_id, flow_b_id, binding=None, ordinal=2, name="Abstain Step")
        step_contra_id, contra_node = await _seed_step(db, cluster_id, flow_a_id, binding="PROG1", ordinal=3, name="Contra Step")
        await _mark_contradicted(db, cluster_id, snap_id, contra_node)

        async def must_not_be_called(self, request):
            raise AssertionError("LLM must not be called when provider_id=None")
            yield  # pragma: no cover - unreachable, keeps this an async generator

        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", must_not_be_called)

        result = await run_source_aware_verdicts(db, cluster_id, snap_id, provider_id=None)
        assert result.llm_batches_issued == 0
        assert result.total_units == 3

        uv_map = await _fetch_uv_map(db, cluster_id, snap_id)

        ready_row = uv_map[step_ready_id]
        assert ready_row["verdict"] == "UNKNOWN"
        assert json.loads(ready_row["evidence_json"])["reason_codes"] == ["OFFLINE_NO_LLM"]
        assert ready_row["mapping_method"] == "source_aware_offline"

        abstain_row = uv_map[step_abstain_id]
        assert abstain_row["verdict"] == "UNKNOWN"
        assert json.loads(abstain_row["evidence_json"])["reason_codes"] == ["UNRESOLVED_ASSET"]

        contra_row = uv_map[step_contra_id]
        assert contra_row["verdict"] == "BROKEN"
        assert contra_row["ai_bucket"] == "stale_missing"

        artifacts = await _fetch_artifacts(db, step_ready_id)
        assert len(artifacts) == 1
        payload = artifacts[0]["payload"]
        assert payload["model_raw_output"] is None
        assert payload["provider_id"] is None
        assert payload["fused_verdict"] == "INSUFFICIENT_EVIDENCE"
    finally:
        await close_db()


@pytest.mark.asyncio
async def test_tri_state_aspects_and_aspect_no_downgrades_match(tmp_path, monkeypatch):
    """Aspects parse as tri-state values end-to-end; a MATCH verdict with an aspect=NO violates
    rule 2 (MATCH requires no aspect=NO) and is downgraded to PARTIAL. guard_equivalence=NO also
    maps onto the guard_verdict column (CLASS_MISMATCH)."""
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
        cluster_id = f"cluster-{new_id()}"
        _write_prog1(source_dir)
        await _seed_common(db, snap_id, repo_id, source_dir)
        flow_id = await _seed_flow(db, cluster_id)
        step_id, _ = await _seed_step(db, cluster_id, flow_id, binding="PROG1")

        async def stub(self, request):
            payload = json.loads(request.messages[1].content)
            uid = payload[0]["unit_id"]
            res = _fake_result(uid, verdict="MATCH", aspects={
                "target_reachable": "YES", "guard_equivalence": "NO",
                "route_order": "INSUFFICIENT", "negative_modality": "NOT_APPLICABLE",
            })
            yield {"type": "content", "text": json.dumps({"results": [res]})}

        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", _make_chat_stream_stub(stub))
        await run_source_aware_verdicts(db, cluster_id, snap_id, provider_id="stub-provider")

        uv_map = await _fetch_uv_map(db, cluster_id, snap_id)
        row = uv_map[step_id]
        assert row["verdict"] == "PARTIAL"
        assert row["guard_verdict"] == "CLASS_MISMATCH"
        evidence = json.loads(row["evidence_json"])
        assert "ASPECT_CONTRADICTION" in evidence["reason_codes"]

        artifacts = await _fetch_artifacts(db, step_id)
        aspects = artifacts[0]["payload"]["model_raw_output"]["aspects"]
        assert aspects["route_order"] == "INSUFFICIENT"
        assert aspects["guard_equivalence"] == "NO"
    finally:
        await close_db()


@pytest.mark.asyncio
async def test_broken_without_citation_downgrades_to_unknown(tmp_path, monkeypatch):
    """FIX 1 rule 2: BROKEN with zero valid citations is just as uncited as a MATCH -> UNKNOWN,
    reason NO_CITATION (previously only MATCH was gated on citation presence)."""
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
        cluster_id = f"cluster-{new_id()}"
        _write_prog1(source_dir)
        await _seed_common(db, snap_id, repo_id, source_dir)
        flow_id = await _seed_flow(db, cluster_id)
        step_id, _ = await _seed_step(db, cluster_id, flow_id, binding="PROG1")

        async def stub(self, request):
            payload = json.loads(request.messages[1].content)
            uid = payload[0]["unit_id"]
            res = _fake_result(uid, verdict="BROKEN", citations=[], aspects={
                "target_reachable": "NO", "guard_equivalence": "NOT_APPLICABLE",
                "route_order": "YES", "negative_modality": "NOT_APPLICABLE",
            })
            yield {"type": "content", "text": json.dumps({"results": [res]})}

        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", _make_chat_stream_stub(stub))
        await run_source_aware_verdicts(db, cluster_id, snap_id, provider_id="stub-provider")

        uv_map = await _fetch_uv_map(db, cluster_id, snap_id)
        row = uv_map[step_id]
        assert row["verdict"] == "UNKNOWN"
        evidence = json.loads(row["evidence_json"])
        assert "NO_CITATION" in evidence["reason_codes"]
    finally:
        await close_db()


@pytest.mark.asyncio
async def test_broken_with_contradiction_aspect_and_valid_citation_stays_broken(tmp_path, monkeypatch):
    """FIX 1: a BROKEN with a real contradiction aspect (target_reachable=NO) and a valid, in-window
    citation must survive as BROKEN -- this is the real "wrong target" case the OLD aspect-only
    downgrade heuristic (guard_equivalence/negative_modality only) wrongly wiped out."""
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
        cluster_id = f"cluster-{new_id()}"
        _write_prog1(source_dir)
        await _seed_common(db, snap_id, repo_id, source_dir)
        flow_id = await _seed_flow(db, cluster_id)
        step_id, _ = await _seed_step(db, cluster_id, flow_id, binding="PROG1")

        async def stub(self, request):
            payload = json.loads(request.messages[1].content)
            uid = payload[0]["unit_id"]
            res = _fake_result(uid, verdict="BROKEN", aspects={
                "target_reachable": "NO", "guard_equivalence": "NOT_APPLICABLE",
                "route_order": "YES", "negative_modality": "NOT_APPLICABLE",
            })
            yield {"type": "content", "text": json.dumps({"results": [res]})}

        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", _make_chat_stream_stub(stub))
        await run_source_aware_verdicts(db, cluster_id, snap_id, provider_id="stub-provider")

        uv_map = await _fetch_uv_map(db, cluster_id, snap_id)
        row = uv_map[step_id]
        assert row["verdict"] == "BROKEN"
        evidence = json.loads(row["evidence_json"])
        assert "BROKEN_NO_CONTRADICTION_ASPECT" not in evidence["reason_codes"]
        assert "NO_CITATION" not in evidence["reason_codes"]
    finally:
        await close_db()


@pytest.mark.asyncio
async def test_broken_without_no_aspect_downgrades_to_unknown(tmp_path, monkeypatch):
    """FIX 1 rule 3: a BROKEN where NONE of the four aspects is "NO" carries no contradiction
    signal at all -- incoherent absence, not a real conflict -> UNKNOWN, BROKEN_NO_CONTRADICTION_ASPECT."""
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
        cluster_id = f"cluster-{new_id()}"
        _write_prog1(source_dir)
        await _seed_common(db, snap_id, repo_id, source_dir)
        flow_id = await _seed_flow(db, cluster_id)
        step_id, _ = await _seed_step(db, cluster_id, flow_id, binding="PROG1")

        async def stub(self, request):
            payload = json.loads(request.messages[1].content)
            uid = payload[0]["unit_id"]
            res = _fake_result(uid, verdict="BROKEN", aspects={
                "target_reachable": "YES", "guard_equivalence": "NOT_APPLICABLE",
                "route_order": "INSUFFICIENT", "negative_modality": "NOT_APPLICABLE",
            })
            yield {"type": "content", "text": json.dumps({"results": [res]})}

        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", _make_chat_stream_stub(stub))
        await run_source_aware_verdicts(db, cluster_id, snap_id, provider_id="stub-provider")

        uv_map = await _fetch_uv_map(db, cluster_id, snap_id)
        row = uv_map[step_id]
        assert row["verdict"] == "UNKNOWN"
        evidence = json.loads(row["evidence_json"])
        assert "BROKEN_NO_CONTRADICTION_ASPECT" in evidence["reason_codes"]
    finally:
        await close_db()


@pytest.mark.asyncio
async def test_partial_without_citation_downgrades_to_unknown(tmp_path, monkeypatch):
    """FIX 1 rule 2: PARTIAL with zero valid citations -> UNKNOWN, reason NO_CITATION."""
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
        cluster_id = f"cluster-{new_id()}"
        _write_prog1(source_dir)
        await _seed_common(db, snap_id, repo_id, source_dir)
        flow_id = await _seed_flow(db, cluster_id)
        step_id, _ = await _seed_step(db, cluster_id, flow_id, binding="PROG1")

        async def stub(self, request):
            payload = json.loads(request.messages[1].content)
            uid = payload[0]["unit_id"]
            res = _fake_result(uid, verdict="PARTIAL", citations=[])
            yield {"type": "content", "text": json.dumps({"results": [res]})}

        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", _make_chat_stream_stub(stub))
        await run_source_aware_verdicts(db, cluster_id, snap_id, provider_id="stub-provider")

        uv_map = await _fetch_uv_map(db, cluster_id, snap_id)
        row = uv_map[step_id]
        assert row["verdict"] == "UNKNOWN"
        evidence = json.loads(row["evidence_json"])
        assert "NO_CITATION" in evidence["reason_codes"]
    finally:
        await close_db()


@pytest.mark.asyncio
async def test_citation_outside_shown_window_treated_invalid(tmp_path, monkeypatch):
    """FIX 2: a citation that resolves fine on its own (in-manifest, in-bounds, span<=40) but falls
    OUTSIDE every snippet window actually shown to the model for this unit must be treated invalid,
    not accepted at face value -- the occurrence is at line 50, so the shown window is ~lines 40-60;
    a citation at 70-75 is a different, unseen part of the same file."""
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
        cluster_id = f"cluster-{new_id()}"
        _write_prog1(source_dir)
        await _seed_common(db, snap_id, repo_id, source_dir)
        flow_id = await _seed_flow(db, cluster_id)
        step_id, _ = await _seed_step(db, cluster_id, flow_id, binding="PROG1")

        async def stub(self, request):
            payload = json.loads(request.messages[1].content)
            uid = payload[0]["unit_id"]
            res = _fake_result(uid, verdict="MATCH", citations=[{"rel_path": "PROG1.cbl", "line_start": 70, "line_end": 75}])
            yield {"type": "content", "text": json.dumps({"results": [res]})}

        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", _make_chat_stream_stub(stub))
        await run_source_aware_verdicts(db, cluster_id, snap_id, provider_id="stub-provider")

        uv_map = await _fetch_uv_map(db, cluster_id, snap_id)
        row = uv_map[step_id]
        assert row["verdict"] == "UNKNOWN"
        evidence = json.loads(row["evidence_json"])
        assert "CITATION_INVALID" in evidence["reason_codes"]
        assert "CITATION_OUT_OF_WINDOW" in evidence["reason_codes"]

        artifacts = await _fetch_artifacts(db, step_id)
        cit = artifacts[0]["payload"]["citation_resolutions"][0]
        assert cit["valid"] is False
        assert cit["reject_reason"] == "CITATION_OUT_OF_WINDOW"
    finally:
        await close_db()


@pytest.mark.asyncio
async def test_duplicate_unit_id_in_batch_reply_forces_retry(tmp_path, monkeypatch):
    """FIX 7: a reply with a duplicated unit_id (e.g. [A, A] for expected {A, B}) must not silently
    pass -- a naive set comparison over-counts coverage. It must raise so the ladder's one
    low-effort retry recovers full, exact coverage."""
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
        cluster_id = f"cluster-{new_id()}"
        _write_prog1(source_dir)
        await _seed_common(db, snap_id, repo_id, source_dir)
        flow_id = await _seed_flow(db, cluster_id)
        step_a, _ = await _seed_step(db, cluster_id, flow_id, binding="PROG1", ordinal=1, name="Step A")
        step_b, _ = await _seed_step(db, cluster_id, flow_id, binding="PROG1", ordinal=2, name="Step B")

        efforts_seen: list[str] = []

        async def stub(self, request):
            efforts_seen.append(request.reasoning_effort)
            payload = json.loads(request.messages[1].content)
            unit_ids = [item["unit_id"] for item in payload]
            if request.reasoning_effort == "high":
                # misbehave: duplicate the first id, drop the second entirely
                results = [_fake_result(unit_ids[0]), _fake_result(unit_ids[0])]
            else:
                results = [_fake_result(uid) for uid in unit_ids]
            yield {"type": "content", "text": json.dumps({"results": results})}

        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", _make_chat_stream_stub(stub))
        await run_source_aware_verdicts(db, cluster_id, snap_id, provider_id="stub-provider")

        assert efforts_seen == ["high", "low"]  # exactly one retry, coverage recovered

        uv_map = await _fetch_uv_map(db, cluster_id, snap_id)
        assert uv_map[step_a]["verdict"] == "MATCH"
        assert uv_map[step_b]["verdict"] == "MATCH"
    finally:
        await close_db()


def test_resolve_unit_rel_paths_tries_whole_binding_before_splitting():
    """FIX 8: a binding that resolves as a whole relative path (e.g. "a/PROG.cbl") must resolve
    directly via the whole-binding attempt, never shredded on "/" first -- splitting first would hit
    an AMBIGUOUS same-basename collision against a decoy file in a different directory."""
    from domain.business_flow_integrity._verifier import _resolve_unit_rel_paths

    bd_nodes_map = {"n1": {"binding": "a/PROG.cbl", "binding_type": "PROGRAM"}}
    manifest_paths = ["a/PROG.cbl", "decoy/PROG.cbl"]

    rel_paths = _resolve_unit_rel_paths(["n1"], bd_nodes_map, manifest_paths)

    assert rel_paths == ["a/PROG.cbl"]


# ---------------------------------------------------------------------------
# TICKET P6-FIX acceptance tests (Verifier as final authority + corrector)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_p6_fix_matcher_gate_removed_reaches_verifier(tmp_path, monkeypatch):
    """§4.1: Gate removed — A unit whose matcher mock returns maps_to_code=false but which HAS
    expanded snippets reaches the verifier, and its final verdict comes from the verifier."""
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
        cluster_id = f"cluster-{new_id()}"
        _write_prog1(source_dir)
        await _seed_common(db, snap_id, repo_id, source_dir)
        flow_id = await _seed_flow(db, cluster_id)
        step_id, _ = await _seed_step(db, cluster_id, flow_id, binding="PROG1")

        verifier_invoked = []

        async def custom_stub(self, request):
            sys_content = request.messages[0].content
            if "mainframe codebase binding enricher" in sys_content:
                payload = json.loads(request.messages[1].content)
                results = [
                    {"unit_id": u["unit_id"], "confirmed_rel_paths": ["PROG1.cbl"], "no_file": False, "reason": "Confirmed"}
                    for u in payload.get("units", [])
                ]
                yield {"type": "content", "text": json.dumps({"results": results})}
                return
            if "mainframe codebase business flow matcher" in sys_content:
                payload = json.loads(request.messages[1].content)
                # Matcher returns maps_to_code: false
                results = [
                    {
                        "unit_id": u["unit_id"],
                        "subclaims": [],
                        "citations": [],
                        "maps_to_code": False,
                        "reason": "Matcher failed to match",
                    }
                    for u in payload
                ]
                yield {"type": "content", "text": json.dumps({"results": results})}
                return

            # Verifier call
            verifier_invoked.append(request)
            payload = json.loads(request.messages[1].content)
            # Verify matcher proposal was provided in prompt payload
            assert payload[0]["matcher_proposal"] is not None
            assert payload[0]["matcher_proposal"]["maps_to_code"] is False
            uid = payload[0]["unit_id"]
            res = _fake_result(uid, verdict="MATCH", citations=[{"rel_path": "PROG1.cbl", "line_start": 50, "line_end": 50}])
            yield {"type": "content", "text": json.dumps({"results": [res]})}

        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", custom_stub)
        await run_source_aware_verdicts(db, cluster_id, snap_id, provider_id="stub-provider")

        assert len(verifier_invoked) == 1, "Verifier MUST be invoked even when matcher returned maps_to_code=false"
        uv_map = await _fetch_uv_map(db, cluster_id, snap_id)
        assert uv_map[step_id]["verdict"] == "MATCH"

        artifacts = await _fetch_artifacts(db, step_id)
        assert len(artifacts) == 1
        p = artifacts[0]["payload"]
        assert p["verifier_corrected"] is True
        assert p["matcher_maps_to_code"] is False
    finally:
        await close_db()


@pytest.mark.asyncio
async def test_p6_fix_corrector_flips_false_match_to_broken(tmp_path, monkeypatch):
    """§4.2: Corrector flips a false MATCH -> BROKEN. Matcher mock claims MATCH on happy path line.
    Verifier mock receives full window + proposal, detects contradiction, returns BROKEN with
    guard_equivalence=NO and the contradicting line. Final verdict BROKEN, verifier_corrected=True."""
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
        cluster_id = f"cluster-{new_id()}"
        _write_prog1(source_dir)
        await _seed_common(db, snap_id, repo_id, source_dir)
        flow_id = await _seed_flow(db, cluster_id)
        step_id, _ = await _seed_step(db, cluster_id, flow_id, binding="PROG1")

        async def custom_stub(self, request):
            sys_content = request.messages[0].content
            if "mainframe codebase binding enricher" in sys_content:
                payload = json.loads(request.messages[1].content)
                results = [
                    {"unit_id": u["unit_id"], "confirmed_rel_paths": ["PROG1.cbl"], "no_file": False, "reason": "Confirmed"}
                    for u in payload.get("units", [])
                ]
                yield {"type": "content", "text": json.dumps({"results": results})}
                return
            if "mainframe codebase business flow matcher" in sys_content:
                payload = json.loads(request.messages[1].content)
                # Matcher claims happy-path match on lines 41-45
                results = [
                    {
                        "unit_id": u["unit_id"],
                        "subclaims": ["Happy path claim"],
                        "citations": [{"rel_path": "PROG1.cbl", "line_start": 41, "line_end": 45}],
                        "maps_to_code": True,
                        "reason": "Found lines 41-45",
                    }
                    for u in payload
                ]
                yield {"type": "content", "text": json.dumps({"results": results})}
                return

            # Verifier detects contradiction at line 50
            payload = json.loads(request.messages[1].content)
            uid = payload[0]["unit_id"]
            res = _fake_result(
                uid,
                verdict="BROKEN",
                citations=[{"rel_path": "PROG1.cbl", "line_start": 50, "line_end": 50}],
                aspects={
                    "target_reachable": "YES",
                    "guard_equivalence": "NO",
                    "route_order": "YES",
                    "negative_modality": "NOT_APPLICABLE",
                },
                reason="Line 50 IF condition contradicts the business design",
            )
            yield {"type": "content", "text": json.dumps({"results": [res]})}

        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", custom_stub)
        await run_source_aware_verdicts(db, cluster_id, snap_id, provider_id="stub-provider")

        uv_map = await _fetch_uv_map(db, cluster_id, snap_id)
        assert uv_map[step_id]["verdict"] == "BROKEN"
        assert uv_map[step_id]["guard_verdict"] == "CLASS_MISMATCH"

        artifacts = await _fetch_artifacts(db, step_id)
        p = artifacts[0]["payload"]
        assert p["verifier_corrected"] is True
        assert p["matcher_maps_to_code"] is True
        # Resolved citation is the verifier's line (line 50)
        assert p["citation_resolutions"][0]["line_start"] == 50
        assert p["citation_resolutions"][0]["line_end"] == 50
    finally:
        await close_db()


@pytest.mark.asyncio
async def test_p6_fix_corrector_fixes_bad_matcher_citation(tmp_path, monkeypatch):
    """§4.3: Corrector fixes a bad citation. Matcher cites out-of-bounds lines (lines 200-210).
    Verifier corrects to in-bounds line 50 and returns MATCH. Final MATCH, verifier_corrected=True."""
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
        cluster_id = f"cluster-{new_id()}"
        _write_prog1(source_dir)
        await _seed_common(db, snap_id, repo_id, source_dir)
        flow_id = await _seed_flow(db, cluster_id)
        step_id, _ = await _seed_step(db, cluster_id, flow_id, binding="PROG1")

        async def custom_stub(self, request):
            sys_content = request.messages[0].content
            if "mainframe codebase binding enricher" in sys_content:
                payload = json.loads(request.messages[1].content)
                results = [
                    {"unit_id": u["unit_id"], "confirmed_rel_paths": ["PROG1.cbl"], "no_file": False, "reason": "Confirmed"}
                    for u in payload.get("units", [])
                ]
                yield {"type": "content", "text": json.dumps({"results": results})}
                return
            if "mainframe codebase business flow matcher" in sys_content:
                payload = json.loads(request.messages[1].content)
                # Matcher proposes bad/out-of-bounds citation
                results = [
                    {
                        "unit_id": u["unit_id"],
                        "subclaims": ["Bad citation claim"],
                        "citations": [{"rel_path": "PROG1.cbl", "line_start": 200, "line_end": 210}],
                        "maps_to_code": True,
                        "reason": "Proposing invalid citation",
                    }
                    for u in payload
                ]
                yield {"type": "content", "text": json.dumps({"results": results})}
                return

            # Verifier corrects citation to line 50
            payload = json.loads(request.messages[1].content)
            uid = payload[0]["unit_id"]
            res = _fake_result(
                uid,
                verdict="MATCH",
                citations=[{"rel_path": "PROG1.cbl", "line_start": 50, "line_end": 50}],
                reason="Corrected citation to line 50",
            )
            yield {"type": "content", "text": json.dumps({"results": [res]})}

        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", custom_stub)
        await run_source_aware_verdicts(db, cluster_id, snap_id, provider_id="stub-provider")

        uv_map = await _fetch_uv_map(db, cluster_id, snap_id)
        assert uv_map[step_id]["verdict"] == "MATCH"
        evidence = json.loads(uv_map[step_id]["evidence_json"])
        assert "CITATION_INVALID" not in evidence["reason_codes"]

        artifacts = await _fetch_artifacts(db, step_id)
        p = artifacts[0]["payload"]
        assert p["verifier_corrected"] is True
        assert p["citation_resolutions"][0]["valid"] is True
        assert p["citation_resolutions"][0]["line_start"] == 50
    finally:
        await close_db()


@pytest.mark.asyncio
async def test_p6_fix_alias_roundtrip_long_unit_ids(tmp_path, monkeypatch):
    """§4.4: Alias round-trip with ~120-char unit IDs. Matcher and Verifier prompts send u1..uN;
    results map back accurately to the long unit_id and assert_exact_id_coverage passes."""
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
        cluster_id = f"cluster-{new_id()}"
        _write_prog1(source_dir)
        await _seed_common(db, snap_id, repo_id, source_dir)
        flow_id = await _seed_flow(db, cluster_id)

        # Create a ~120-char long unit_id
        long_uid = "bdbf:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef:doc/EMT.BD-HSBMENU5.report.md:1_evtab:b1"
        now = utc_now_iso()
        node_id = f"bdnode:{new_id()}"
        await db.execute(
            "INSERT INTO bd_flow_nodes (id, cluster_id, doc_id, node_kind, binding, binding_type, provenance_tier, created_at) "
            "VALUES (?, ?, 'doc1', 'step', 'PROG1', 'PROGRAM', 'declared', ?)",
            (node_id, cluster_id, now),
        )
        await db.execute(
            "INSERT INTO bd_business_steps (id, flow_id, name, functionality, ordinal, source_node_ids, doc_line_start, doc_line_end, created_at) "
            "VALUES (?, ?, 'Long Step', 'Functionality', 1, ?, 10, 12, ?)",
            (long_uid, flow_id, json.dumps([node_id]), now),
        )
        await db.commit()

        seen_matcher_uids = []
        seen_verifier_uids = []

        async def custom_stub(self, request):
            sys_content = request.messages[0].content
            if "mainframe codebase binding enricher" in sys_content:
                payload = json.loads(request.messages[1].content)
                results = [
                    {"unit_id": u["unit_id"], "confirmed_rel_paths": ["PROG1.cbl"], "no_file": False, "reason": "Confirmed"}
                    for u in payload.get("units", [])
                ]
                yield {"type": "content", "text": json.dumps({"results": results})}
                return
            if "mainframe codebase business flow matcher" in sys_content:
                payload = json.loads(request.messages[1].content)
                seen_matcher_uids.extend(u["unit_id"] for u in payload)
                results = [
                    {
                        "unit_id": u["unit_id"],
                        "subclaims": ["Claim"],
                        "citations": [{"rel_path": "PROG1.cbl", "line_start": 50, "line_end": 50}],
                        "maps_to_code": True,
                        "reason": "Matched",
                    }
                    for u in payload
                ]
                yield {"type": "content", "text": json.dumps({"results": results})}
                return

            payload = json.loads(request.messages[1].content)
            seen_verifier_uids.extend(u["unit_id"] for u in payload)
            results = [_fake_result(u["unit_id"], verdict="MATCH", citations=[{"rel_path": "PROG1.cbl", "line_start": 50, "line_end": 50}]) for u in payload]
            yield {"type": "content", "text": json.dumps({"results": results})}

        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", custom_stub)
        await run_source_aware_verdicts(db, cluster_id, snap_id, provider_id="stub-provider")

        # Assert matcher and verifier only saw short aliases ("u1"), never the 120-char ID
        assert seen_matcher_uids == ["u1"]
        assert seen_verifier_uids == ["u1"]

        uv_map = await _fetch_uv_map(db, cluster_id, snap_id)
        assert long_uid in uv_map
        assert uv_map[long_uid]["verdict"] == "MATCH"
    finally:
        await close_db()


@pytest.mark.asyncio
async def test_p6_fix_no_snippet_unit_stays_abstained(tmp_path, monkeypatch):
    """§4.5: A unit with empty expanded window never enters ready_for_verifier_units and remains UNKNOWN."""
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
        cluster_id = f"cluster-{new_id()}"
        _write_prog1(source_dir)
        await _seed_common(db, snap_id, repo_id, source_dir)
        flow_id = await _seed_flow(db, cluster_id)
        # Seed a step with no binding
        step_id, _ = await _seed_step(db, cluster_id, flow_id, binding=None)

        verifier_called = []

        async def custom_stub(self, request):
            sys_content = request.messages[0].content
            if "mainframe codebase binding enricher" in sys_content:
                payload = json.loads(request.messages[1].content)
                results = [
                    {"unit_id": u["unit_id"], "confirmed_rel_paths": [], "no_file": False, "reason": "Unresolved"}
                    for u in payload.get("units", [])
                ]
                yield {"type": "content", "text": json.dumps({"results": results})}
                return
            verifier_called.append(True)
            yield {"type": "content", "text": json.dumps({"results": []})}

        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", custom_stub)
        result = await run_source_aware_verdicts(db, cluster_id, snap_id, provider_id="stub-provider")

        assert len(verifier_called) == 0, "Verifier MUST not be called for abstained unit"
        uv_map = await _fetch_uv_map(db, cluster_id, snap_id)
        assert uv_map[step_id]["verdict"] == "UNKNOWN"
        evidence = json.loads(uv_map[step_id]["evidence_json"])
        assert "NO_FILE_IN_SNAPSHOT" in evidence["reason_codes"] or "UNRESOLVED_ASSET" in evidence["reason_codes"]
    finally:
        await close_db()


@pytest.mark.asyncio
async def test_p6_fix_seed_rel_paths_persisted_in_artifact(tmp_path, monkeypatch):
    """§4.6: Artifact for a deterministically-resolvable unit has non-empty enrich.seed_rel_paths."""
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
        cluster_id = f"cluster-{new_id()}"
        _write_prog1(source_dir)
        await _seed_common(db, snap_id, repo_id, source_dir)
        flow_id = await _seed_flow(db, cluster_id)
        step_id, _ = await _seed_step(db, cluster_id, flow_id, binding="PROG1")

        async def custom_stub(self, request):
            sys_content = request.messages[0].content
            if "mainframe codebase binding enricher" in sys_content:
                payload = json.loads(request.messages[1].content)
                results = [
                    {"unit_id": u["unit_id"], "confirmed_rel_paths": ["PROG1.cbl"], "no_file": False, "reason": "Confirmed"}
                    for u in payload.get("units", [])
                ]
                yield {"type": "content", "text": json.dumps({"results": results})}
                return
            if "mainframe codebase business flow matcher" in sys_content:
                payload = json.loads(request.messages[1].content)
                results = [
                    {
                        "unit_id": u["unit_id"],
                        "subclaims": ["Claim"],
                        "citations": [{"rel_path": "PROG1.cbl", "line_start": 50, "line_end": 50}],
                        "maps_to_code": True,
                        "reason": "Matched",
                    }
                    for u in payload
                ]
                yield {"type": "content", "text": json.dumps({"results": results})}
                return

            payload = json.loads(request.messages[1].content)
            results = [_fake_result(u["unit_id"], verdict="MATCH", citations=[{"rel_path": "PROG1.cbl", "line_start": 50, "line_end": 50}]) for u in payload]
            yield {"type": "content", "text": json.dumps({"results": results})}

        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", custom_stub)
        await run_source_aware_verdicts(db, cluster_id, snap_id, provider_id="stub-provider")

        artifacts = await _fetch_artifacts(db, step_id)
        assert len(artifacts) == 1
        p = artifacts[0]["payload"]
        assert p["enrich"] is not None
        assert p["enrich"]["seed_rel_paths"] == ["PROG1.cbl"]
    finally:
        await close_db()


# ---------------------------------------------------------------------------
# TICKET P6-FIX-2 acceptance tests (Enrich no_file advisory + flow fallback)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_p6_fix2_enrich_no_file_rescued_by_flow_files(tmp_path, monkeypatch):
    """§3.1: Enrich no_file + flow HAS files -> unit is rescued by flow files,
    reaches verifier, and gets verdict (e.g. PARTIAL) with retrieval_flow_fallback=True."""
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
        cluster_id = f"cluster-{new_id()}"
        _write_prog1(source_dir)
        await _seed_common(db, snap_id, repo_id, source_dir)
        flow_id = await _seed_flow(db, cluster_id)
        step_a, _ = await _seed_step(db, cluster_id, flow_id, binding="PROG1", ordinal=1, name="Step A")
        step_b, _ = await _seed_step(db, cluster_id, flow_id, binding="SENTINEL_WILDCARD", ordinal=2, name="Step B")

        verifier_called_units = []

        async def custom_stub(self, request):
            sys_content = request.messages[0].content
            if "mainframe codebase binding enricher" in sys_content:
                payload = json.loads(request.messages[1].content)
                results = []
                for u in payload.get("units", []):
                    if u["unit_id"] == step_a:
                        results.append({"unit_id": u["unit_id"], "confirmed_rel_paths": ["PROG1.cbl"], "no_file": False, "reason": "Confirmed PROG1"})
                    else:
                        # Enrich mistakenly says no_file: True for Step B
                        results.append({"unit_id": u["unit_id"], "confirmed_rel_paths": [], "no_file": True, "reason": "Abstract wildcard"})
                yield {"type": "content", "text": json.dumps({"results": results})}
                return
            if "mainframe codebase business flow matcher" in sys_content:
                payload = json.loads(request.messages[1].content)
                results = [
                    {
                        "unit_id": u["unit_id"],
                        "subclaims": ["Claim"],
                        "citations": [{"rel_path": "PROG1.cbl", "line_start": 50, "line_end": 50}],
                        "maps_to_code": True,
                        "reason": "Matched",
                    }
                    for u in payload
                ]
                yield {"type": "content", "text": json.dumps({"results": results})}
                return

            payload = json.loads(request.messages[1].content)
            verifier_called_units.extend(u["unit_id"] for u in payload)
            results = [
                _fake_result(
                    u["unit_id"],
                    verdict="PARTIAL",
                    citations=[{"rel_path": "PROG1.cbl", "line_start": 50, "line_end": 50}],
                    aspects={
                        "target_reachable": "YES",
                        "guard_equivalence": "NOT_APPLICABLE",
                        "route_order": "NOT_APPLICABLE",
                        "negative_modality": "NOT_APPLICABLE",
                    },
                    reason="Found partial logic in flow file PROG1.cbl",
                )
                for u in payload
            ]
            yield {"type": "content", "text": json.dumps({"results": results})}

        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", custom_stub)
        await run_source_aware_verdicts(db, cluster_id, snap_id, provider_id="stub-provider")

        assert len(verifier_called_units) == 2, "Both Step A and rescued Step B MUST reach the verifier"
        uv_map = await _fetch_uv_map(db, cluster_id, snap_id)
        assert uv_map[step_b]["verdict"] == "PARTIAL"

        artifacts_b = await _fetch_artifacts(db, step_b)
        assert len(artifacts_b) == 1
        payload_b = artifacts_b[0]["payload"]
        assert payload_b["retrieval_flow_fallback"] is True
        assert payload_b["enrich"]["no_file"] is True

        artifacts_a = await _fetch_artifacts(db, step_a)
        assert len(artifacts_a) == 1
        assert artifacts_a[0]["payload"]["retrieval_flow_fallback"] is False
    finally:
        await close_db()


@pytest.mark.asyncio
async def test_p6_fix2_enrich_no_file_empty_flow_stays_no_file(tmp_path, monkeypatch):
    """§3.2: Enrich no_file + flow has NO files anywhere -> stays NO_FILE_IN_SNAPSHOT."""
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
        cluster_id = f"cluster-{new_id()}"
        _write_prog1(source_dir)
        await _seed_common(db, snap_id, repo_id, source_dir)
        flow_id = await _seed_flow(db, cluster_id)
        step_id, _ = await _seed_step(db, cluster_id, flow_id, binding="COMPLETELY_UNKNOWN_EXTERNAL")

        verifier_called = []

        async def custom_stub(self, request):
            sys_content = request.messages[0].content
            if "mainframe codebase binding enricher" in sys_content:
                payload = json.loads(request.messages[1].content)
                results = [
                    {"unit_id": u["unit_id"], "confirmed_rel_paths": [], "no_file": True, "reason": "No backing file anywhere"}
                    for u in payload.get("units", [])
                ]
                yield {"type": "content", "text": json.dumps({"results": results})}
                return
            verifier_called.append(True)
            yield {"type": "content", "text": json.dumps({"results": []})}

        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", custom_stub)
        await run_source_aware_verdicts(db, cluster_id, snap_id, provider_id="stub-provider")

        assert len(verifier_called) == 0, "Verifier MUST not be called when flow has no files"
        uv_map = await _fetch_uv_map(db, cluster_id, snap_id)
        assert uv_map[step_id]["verdict"] == "UNKNOWN"
        evidence = json.loads(uv_map[step_id]["evidence_json"])
        assert evidence["reason_codes"] == ["NO_FILE_IN_SNAPSHOT"]

        artifacts = await _fetch_artifacts(db, step_id)
        assert len(artifacts) == 1
        assert artifacts[0]["payload"]["retrieval_flow_fallback"] is False
    finally:
        await close_db()


@pytest.mark.asyncio
async def test_p6_fix2_empty_seed_enrich_not_no_file_rescued(tmp_path, monkeypatch):
    """§3.3: Empty deterministic seed, enrich NOT no_file, flow has files -> rescued via flow files."""
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
        cluster_id = f"cluster-{new_id()}"
        _write_prog1(source_dir)
        await _seed_common(db, snap_id, repo_id, source_dir)
        flow_id = await _seed_flow(db, cluster_id)
        step_a, _ = await _seed_step(db, cluster_id, flow_id, binding="PROG1", ordinal=1, name="Step A")
        step_b, _ = await _seed_step(db, cluster_id, flow_id, binding="UNRESOLVED_TOKEN", ordinal=2, name="Step B")

        verifier_called_units = []

        async def custom_stub(self, request):
            sys_content = request.messages[0].content
            if "mainframe codebase binding enricher" in sys_content:
                payload = json.loads(request.messages[1].content)
                results = []
                for u in payload.get("units", []):
                    if u["unit_id"] == step_a:
                        results.append({"unit_id": u["unit_id"], "confirmed_rel_paths": ["PROG1.cbl"], "no_file": False, "reason": "Confirmed PROG1"})
                    else:
                        # Enrich returns empty confirmed_rel_paths, but no_file: False
                        results.append({"unit_id": u["unit_id"], "confirmed_rel_paths": [], "no_file": False, "reason": "Not sure"})
                yield {"type": "content", "text": json.dumps({"results": results})}
                return
            if "mainframe codebase business flow matcher" in sys_content:
                payload = json.loads(request.messages[1].content)
                results = [
                    {
                        "unit_id": u["unit_id"],
                        "subclaims": ["Claim"],
                        "citations": [{"rel_path": "PROG1.cbl", "line_start": 50, "line_end": 50}],
                        "maps_to_code": True,
                        "reason": "Matched",
                    }
                    for u in payload
                ]
                yield {"type": "content", "text": json.dumps({"results": results})}
                return

            payload = json.loads(request.messages[1].content)
            verifier_called_units.extend(u["unit_id"] for u in payload)
            results = [
                _fake_result(
                    u["unit_id"],
                    verdict="MATCH",
                    citations=[{"rel_path": "PROG1.cbl", "line_start": 50, "line_end": 50}],
                    reason="Matched in PROG1.cbl",
                )
                for u in payload
            ]
            yield {"type": "content", "text": json.dumps({"results": results})}

        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", custom_stub)
        await run_source_aware_verdicts(db, cluster_id, snap_id, provider_id="stub-provider")

        assert len(verifier_called_units) == 2
        uv_map = await _fetch_uv_map(db, cluster_id, snap_id)
        assert uv_map[step_b]["verdict"] == "MATCH"

        artifacts_b = await _fetch_artifacts(db, step_b)
        assert artifacts_b[0]["payload"]["retrieval_flow_fallback"] is True
    finally:
        await close_db()


@pytest.mark.asyncio
async def test_p6_fix2_unit_with_own_resolved_files_unaffected(tmp_path, monkeypatch):
    """§3.4: Unit with its own resolved files is unaffected (retrieval_flow_fallback is False)."""
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
        cluster_id = f"cluster-{new_id()}"
        _write_prog1(source_dir)
        await _seed_common(db, snap_id, repo_id, source_dir)
        flow_id = await _seed_flow(db, cluster_id)
        step_a, _ = await _seed_step(db, cluster_id, flow_id, binding="PROG1", ordinal=1, name="Step A")

        async def custom_stub(self, request):
            sys_content = request.messages[0].content
            if "mainframe codebase binding enricher" in sys_content:
                payload = json.loads(request.messages[1].content)
                results = [
                    {"unit_id": u["unit_id"], "confirmed_rel_paths": ["PROG1.cbl"], "no_file": False, "reason": "Confirmed PROG1"}
                    for u in payload.get("units", [])
                ]
                yield {"type": "content", "text": json.dumps({"results": results})}
                return
            if "mainframe codebase business flow matcher" in sys_content:
                payload = json.loads(request.messages[1].content)
                results = [
                    {
                        "unit_id": u["unit_id"],
                        "subclaims": ["Claim"],
                        "citations": [{"rel_path": "PROG1.cbl", "line_start": 50, "line_end": 50}],
                        "maps_to_code": True,
                        "reason": "Matched",
                    }
                    for u in payload
                ]
                yield {"type": "content", "text": json.dumps({"results": results})}
                return

            payload = json.loads(request.messages[1].content)
            results = [_fake_result(u["unit_id"], verdict="MATCH", citations=[{"rel_path": "PROG1.cbl", "line_start": 50, "line_end": 50}])]
            yield {"type": "content", "text": json.dumps({"results": results})}

        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", custom_stub)
        await run_source_aware_verdicts(db, cluster_id, snap_id, provider_id="stub-provider")

        artifacts = await _fetch_artifacts(db, step_a)
        assert len(artifacts) == 1
        assert artifacts[0]["payload"]["retrieval_flow_fallback"] is False
    finally:
        await close_db()


