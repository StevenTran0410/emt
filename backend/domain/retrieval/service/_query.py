"""RetrievalService mixin: file-chunk lookup, retrieval (RRF/2-stage/legacy fallback), and A/B compare."""

from __future__ import annotations

import json

from infrastructure.db.database import get_db
from shared.logger import logger

from ..bm25_scorer import BM25Scorer, _query_terms
from ..types import (
    FileChunk,
    FileChunksResponse,
    RetrievalBundle,
    RetrievalCompareResponse,
    RetrievalEvidence,
    RetrievalMode,
    RetrieveRequest,
    RrfFusionBundle,
    RrfFusionRequest,
    TwoStageBundle,
    TwoStageRequest,
)
from ._chunking import _maybe_expand_to_boundary, _token_estimate
from ._constants import _CHUNK_FULL_COLS, _SECTION_BUDGETS, _SECTION_CATEGORY_HINTS


class _QueryMixin:
    async def chunks_for_file(
        self, snapshot_id: str, rel_path: str, symbol_chunks_only: bool = False
    ) -> FileChunksResponse:
        """Fetch a known file's own chunks directly by path — no search, no ranking, no risk of drifting to a wrong-but-similar-sounding file."""
        query = f"SELECT {_CHUNK_FULL_COLS} FROM retrieval_chunks WHERE snapshot_id=? AND rel_path=?"
        params: tuple = (snapshot_id, rel_path)
        if symbol_chunks_only:
            query += " AND chunk_type IN ('function', 'class')"
        query += " ORDER BY chunk_index ASC"

        async with get_db().execute(query, params) as cur:
            rows = await cur.fetchall()

        return FileChunksResponse(
            snapshot_id=snapshot_id,
            rel_path=rel_path,
            chunks=[
                FileChunk(
                    chunk_id=r["id"],
                    rel_path=r["rel_path"],
                    chunk_index=r["chunk_index"],
                    chunk_type=r["chunk_type"],
                    content=r["content"] or "",
                    token_estimate=int(r["token_estimate"] or 0),
                    start_line=r["start_line"] or 0,
                    end_line=r["end_line"] or 0,
                    split_part=r["split_part"] or 0,
                    split_of=r["split_of"] or 1,
                )
                for r in rows
            ],
        )

    async def retrieve(self, req: RetrieveRequest) -> RetrievalBundle:
        if not req.query.strip():
            raise ValueError("Query is required")
        terms = _query_terms(req.query)
        if not terms:
            raise ValueError("Query must contain searchable terms")

        budget = _SECTION_BUDGETS[req.section]

        # Try RRF fusion first, fall back to 2-stage, then legacy single-pass on error.
        try:
            from ..rrf_fusion import retrieve_rrf_fusion_as_bundle

            return await retrieve_rrf_fusion_as_bundle(
                snapshot_id=req.snapshot_id,
                query=req.query,
                section=req.section,
                budget=budget,
                mode=req.mode,
                min_confidence=req.min_confidence,
            )
        except Exception:
            logger.warning(
                "[retrieve] RRF fusion pipeline failed for snapshot=%s query=%r — falling back to 2-stage",
                req.snapshot_id,
                req.query[:60],
                exc_info=True,
            )

        try:
            from ..two_stage_retrieval import retrieve_two_stage_as_bundle

            return await retrieve_two_stage_as_bundle(
                snapshot_id=req.snapshot_id,
                query=req.query,
                section=req.section,
                budget=budget,
                mode=req.mode,
                min_confidence=req.min_confidence,
            )
        except Exception:
            logger.warning(
                "[retrieve] 2-stage pipeline failed for snapshot=%s query=%r — using fallback single-pass",
                req.snapshot_id,
                req.query[:60],
                exc_info=True,
            )

        logger.debug("[retrieve] using fallback single-pass for snapshot=%s", req.snapshot_id)
        category_hints = _SECTION_CATEGORY_HINTS[req.section]

        async with get_db().execute(
            f"""
            SELECT {_CHUNK_FULL_COLS}
            FROM retrieval_chunks
            WHERE snapshot_id=?
            """,
            (req.snapshot_id,),
        ) as cur:
            rows = list(await cur.fetchall())

        if not rows:
            raise ValueError("Retrieval index not built for this snapshot")

        # Gather graph hints.
        async with get_db().execute(
            "SELECT top_central_files FROM structural_graph_summaries WHERE snapshot_id=?",
            (req.snapshot_id,),
        ) as cur:
            graph_summary = await cur.fetchone()
        central_rank: dict[str, int] = {}
        if graph_summary and graph_summary["top_central_files"]:
            try:
                arr = json.loads(graph_summary["top_central_files"])
            except Exception:
                arr = []
            for i, item in enumerate(arr):
                rp = item.get("rel_path")
                if isinstance(rp, str):
                    central_rank[rp] = i + 1

        # Load BM25 scorer
        bm25_row = None
        async with get_db().execute(
            "SELECT avgdl, idf_json, k1, b FROM retrieval_bm25_stats WHERE snapshot_id=?",
            (req.snapshot_id,),
        ) as cur:
            bm25_row = await cur.fetchone()
        scorer = BM25Scorer.from_stats_row(bm25_row)
        if scorer is None:
            logger.warning(
                "BM25 stats not found for snapshot %s — using lexical fallback", req.snapshot_id
            )

        scored: list[tuple[float, RetrievalEvidence]] = []
        for r in rows:
            rel_path = r["rel_path"]
            cat = r["category"]
            content = r["content"] or ""
            path_low = rel_path.lower()

            if scorer is not None:
                raw = scorer.score(terms, content.lower(), path_low)
                if raw <= 0.0:
                    continue
                reason_codes: list[str] = ["bm25-hit"]
                score = raw
            else:
                # Cold-start fallback: original lexical hit counting
                low = content.lower()
                lexical_hits = 0
                for t in terms:
                    lexical_hits += low.count(t)
                    if t in path_low:
                        lexical_hits += 2
                if lexical_hits <= 0:
                    continue
                reason_codes = ["lexical-hit"]
                score = float(lexical_hits)

            if cat in category_hints:
                score += 1.4
                reason_codes.append("section-category-match")

            if rel_path in central_rank:
                # Better rank => bigger bonus.
                score += max(0.0, 2.6 - (central_rank[rel_path] * 0.08))
                reason_codes.append("graph-centrality-hint")

            if req.mode == RetrievalMode.VECTORLESS:
                # Vectorless path favors graph-shape + path semantics.
                if rel_path in central_rank:
                    score += 1.8
                if "index" in path_low or "router" in path_low or "service" in path_low:
                    score += 0.9
                reason_codes.append("vectorless-graph-prior")
            else:
                # Hybrid path: symbol-ish hints by token overlap.
                symbolish = sum(1 for t in terms if t in path_low)
                if symbolish > 0:
                    score += 0.6 * symbolish
                    reason_codes.append("symbol-path-hint")

            ev = RetrievalEvidence(
                chunk_id=r["id"],
                rel_path=rel_path,
                chunk_index=r["chunk_index"],
                reason_codes=reason_codes,
                score=score,
                token_estimate=int(r["token_estimate"] or _token_estimate(content)),
                excerpt=content,  # full chunk content — render_bundle truncates for LLM prompt
            )
            scored.append((score, ev))

        scored.sort(key=lambda x: (-x[0], x[1].rel_path, x[1].chunk_index))

        # Build a lookup for adjacent-chunk expansion: (rel_path, chunk_index) -> row
        chunk_lookup: dict[tuple[str, int], dict] = {
            (r["rel_path"], r["chunk_index"]): dict(r) for r in rows
        }

        used = 0
        picked: list[RetrievalEvidence] = []
        limit = max(1, min(req.max_results, 80))
        for _, ev in scored:
            if len(picked) >= limit:
                break
            if used + ev.token_estimate > budget:
                continue

            # Expand chunk to function boundary if needed (at most one hop).
            ev = _maybe_expand_to_boundary(ev, chunk_lookup)

            # Re-check budget after potential expansion
            if used + ev.token_estimate > budget:
                continue

            picked.append(ev)
            used += ev.token_estimate

        return RetrievalBundle(
            snapshot_id=req.snapshot_id,
            mode=req.mode,
            section=req.section,
            query=req.query,
            budget_tokens=budget,
            used_tokens=used,
            evidences=picked,
        )

    async def retrieve_two_stage(self, req: TwoStageRequest) -> TwoStageBundle:
        from ..two_stage_retrieval import retrieve_two_stage as _run

        budget = req.budget or _SECTION_BUDGETS.get(req.section, 10_000)
        return await _run(
            snapshot_id=req.snapshot_id,
            query=req.query,
            section=req.section,
            budget=budget,
            min_confidence=req.min_confidence,
        )

    async def retrieve_rrf_fusion(self, req: RrfFusionRequest) -> RrfFusionBundle:
        from ..rrf_fusion import retrieve_rrf_fusion as _run

        return await _run(
            snapshot_id=req.snapshot_id,
            query=req.query,
            section=req.section,
            budget=req.budget,
            min_confidence=req.min_confidence,
            symbol_chunks_only=req.symbol_chunks_only,
        )

    async def compare(self, req: RetrieveRequest) -> RetrievalCompareResponse:
        base_req = RetrieveRequest(
            snapshot_id=req.snapshot_id,
            query=req.query,
            section=req.section,
            mode=RetrievalMode.HYBRID,
            max_results=req.max_results,
        )
        vec_req = RetrieveRequest(
            snapshot_id=req.snapshot_id,
            query=req.query,
            section=req.section,
            mode=RetrievalMode.VECTORLESS,
            max_results=req.max_results,
        )
        baseline = await self.retrieve(base_req)
        vectorless = await self.retrieve(vec_req)

        # Simple comparable metrics for A/B logging.
        def _precision_at_5(bundle: RetrievalBundle) -> float:
            top = bundle.evidences[:5]
            if not top:
                return 0.0
            good = sum(1 for e in top if "section-category-match" in e.reason_codes)
            return float(good) / float(len(top))

        def _evidence_hit_rate(bundle: RetrievalBundle) -> float:
            if not bundle.evidences:
                return 0.0
            with_query = 0
            q_terms = _query_terms(bundle.query)
            for e in bundle.evidences:
                low = e.excerpt.lower()
                if any(t in low for t in q_terms):
                    with_query += 1
            return float(with_query) / float(len(bundle.evidences))

        return RetrievalCompareResponse(
            snapshot_id=req.snapshot_id,
            section=req.section,
            query=req.query,
            baseline=baseline,
            vectorless=vectorless,
            precision_at_5_delta=_precision_at_5(vectorless) - _precision_at_5(baseline),
            evidence_hit_rate_delta=_evidence_hit_rate(vectorless) - _evidence_hit_rate(baseline),
            token_cost_delta=vectorless.used_tokens - baseline.used_tokens,
        )
