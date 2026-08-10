"""Integration tests for AI Assessment (assess and get_latest_assessment)."""

import pytest
from unittest.mock import AsyncMock, patch
from infrastructure.db.database import close_db, get_db, init_db
from domain.doc_code_compare.service import DocCodeCompareService, _normalize_llm_assessment
from domain.model_connector.types import ChatResponse


def test_normalize_llm_assessment_tolerates_drift():
    """Drifted LLM output (bare-string refs, unknown enums, missing fields) must coerce
    into the strict schema instead of blowing up AiAssessmentResponse validation."""
    drifted = {
        "overall_verdict": "gaps found",  # wrong case + space
        "confidence": "pretty sure",  # not an enum
        "concerns": [
            {
                "severity": "critical",  # not an enum
                "title": "t",
                "evidence_refs": ["program/CBSTM03A", "calls program/A -> program/B", "x.CBL:44"],
            },
            "not-a-dict",  # junk entry
        ],
        "caveats": ["ok", None],
    }
    out = _normalize_llm_assessment(drifted)
    assert out["overall_verdict"] == "GAPS_FOUND"
    assert out["confidence"] == "low"
    assert len(out["concerns"]) == 1
    c = out["concerns"][0]
    assert c["severity"] == "info"
    kinds = [r["kind"] for r in c["evidence_refs"]]
    assert kinds == ["entity", "relation", "file"]
    assert out["caveats"] == ["ok"]


@pytest.mark.asyncio
async def test_assess_caching_and_stale(tmp_path, monkeypatch):
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(db_dir))

    await init_db()
    try:
        db = get_db()
        await db.execute(
            """
            INSERT INTO doc_graph_clusters (
                id, cluster_name, source_dir, bd_path, input_fingerprint,
                parser_version, created_at
            )
            VALUES ('cluster-1', 'CREASTMT', '/tmp/docs', '/tmp/bd.md', 'fp123', 1, '2026-01-01T00:00:00')
            """
        )
        # Insert dummy doc_graph_nodes
        await db.execute(
            """
            INSERT INTO doc_graph_nodes (id, cluster_id, node_type, display_name, attributes, provenance, created_at)
            VALUES ('node-1', 'cluster-1', 'program', 'CBSTM03A', '{}', '[]', '2026-01-01T00:00:00')
            """
        )
        # Insert dummy source_facts
        await db.execute(
            """
            INSERT INTO source_facts (snapshot_id, fact_type, semantic_key, rel_path, line_start, line_end, name, language, occurrence_ix, extractor, extractor_ver, created_at)
            VALUES ('snap-1', 'program', 'program/CBSTM03A', 'app/cbl/CBSTM03A.CBL', 1, 100, 'CBSTM03A', 'cobol', 0, 'cobol', '1.0', '2026-01-01T00:00:00')
            """
        )
        await db.commit()

        svc = DocCodeCompareService()

        fake_llm_json = """
        {
            "overall_verdict": "ADEQUATE",
            "confidence": "high",
            "completeness_note": "All documented programs exist in implementation code.",
            "correctness_note": "Program call graphs match evidence.",
            "concerns": [
                {
                    "severity": "info",
                    "title": "Minor naming convention",
                    "detail": "Program CBSTM03A uses standard mainframes entry.",
                    "evidence_refs": [{"kind": "entity", "ref": "program/CBSTM03A"}],
                    "recommendation": "Maintain standard naming."
                }
            ],
            "caveats": []
        }
        """

        with patch("domain.model_connector.service.ProviderConfigService.chat", new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = ChatResponse(content=fake_llm_json, provider_id="prov-1", model_id="test-model")

            # First call: cache miss, invokes LLM
            res1 = await svc.assess("cluster-1", "snap-1")
            assert res1.from_cache is False
            assert res1.overall_verdict == "ADEQUATE"
            assert len(res1.concerns) == 1
            assert mock_chat.call_count == 1

            # Second call: cache hit, returns from_cache=True without invoking LLM
            res2 = await svc.assess("cluster-1", "snap-1")
            assert res2.from_cache is True
            assert res2.overall_verdict == "ADEQUATE"
            assert mock_chat.call_count == 1

            # GET latest assessment
            latest = await svc.get_latest_assessment("cluster-1", "snap-1")
            assert latest is not None
            assert latest.overall_verdict == "ADEQUATE"
            assert latest.stale is False

            # Add a new doc node to change evidence -> test stale detection
            await db.execute(
                """
                INSERT INTO doc_graph_nodes (id, cluster_id, node_type, display_name, attributes, provenance, created_at)
                VALUES ('node-2', 'cluster-1', 'program', 'CBSTM03B', '{}', '[]', '2026-01-01T00:00:00')
                """
            )
            await db.commit()

            # GET latest assessment should now reflect stale=True
            latest_stale = await svc.get_latest_assessment("cluster-1", "snap-1")
            assert latest_stale is not None
            assert latest_stale.stale is True
    finally:
        await close_db()
