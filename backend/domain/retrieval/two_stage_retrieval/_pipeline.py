"""Top-level orchestration: retrieve_two_stage (stage1 → stage2 → stage3) and the
RetrievalBundle-compatible wrapper retrieve_two_stage_as_bundle."""
from __future__ import annotations

from infrastructure.db.database import get_db
from shared.logger import logger

from ..bm25_scorer import BM25Scorer, _query_terms
from ..quality import compute_retrieval_quality
from ..service import _CHUNK_FULL_COLS, _SECTION_CATEGORY_HINTS
from ..types import (
    RetrievalBundle,
    RetrievalEvidence,
    RetrievalMode,
    RetrievalSection,
    StageExpansion,
    TwoStageBundle,
    TwoStageStage3Result,
)
from ._constants import _RERANK_MIN_SCORE
from ._diversity import _load_file_size_stats
from ._graph_context import _load_graph_context, load_symbol_index
from ._native import _get_native
from ._ranking import _rank_and_budget
from ._scoring import _compute_chunk_score, _expand_one, _stage1_score_rows


async def retrieve_two_stage(
    snapshot_id: str,
    query: str,
    section: RetrievalSection,
    budget: int,
    min_confidence: float | None = None,
) -> TwoStageBundle:
    if not query.strip():
        raise ValueError("Query is required")
    terms = _query_terms(query)
    if not terms:
        raise ValueError("Query must contain searchable terms")

    native = _get_native()
    db = get_db()

    async with db.execute(
        f"SELECT {_CHUNK_FULL_COLS} FROM retrieval_chunks WHERE snapshot_id=?",
        (snapshot_id,),
    ) as cur:
        all_rows = await cur.fetchall()

    if not all_rows:
        raise ValueError("Retrieval index not built for this snapshot")

    async with db.execute(
        "SELECT avgdl, idf_json, k1, b FROM retrieval_bm25_stats WHERE snapshot_id=?",
        (snapshot_id,),
    ) as cur:
        bm25_row = await cur.fetchone()
    scorer = BM25Scorer.from_stats_row(bm25_row)
    if scorer is None:
        logger.debug("[two_stage] BM25 stats not found for %s — using lexical fallback", snapshot_id)

    ctx = await _load_graph_context(snapshot_id, min_confidence=min_confidence)
    symbol_index = await load_symbol_index(snapshot_id)

    stage1 = _stage1_score_rows(all_rows, scorer, terms, top_k=100)

    stage1_files: set[str] = {c.rel_path for c in stage1}
    stage2_expansions: list[StageExpansion] = []
    all_expanded_files: set[str] = set()

    for candidate in stage1[:20]:
        expansion, expanded = _expand_one(candidate, ctx, native)
        stage2_expansions.append(expansion)
        all_expanded_files.update(expanded)

    new_files = all_expanded_files - stage1_files
    row_lookup: dict[str, dict] = {r["id"]: r for r in all_rows}
    file_rows: dict[str, list[dict]] = {}
    for r in all_rows:
        file_rows.setdefault(r["rel_path"], []).append(r)

    expanded_rows: list[dict] = []
    for f in new_files:
        expanded_rows.extend(file_rows.get(f, []))

    stage1_rows: list[dict] = [row_lookup[c.chunk_id] for c in stage1 if c.chunk_id in row_lookup]
    all_candidate_rows = stage1_rows + expanded_rows

    seed_community_ids: set[int] = set()
    for c in stage1[:20]:
        cid = ctx.file_community.get(c.rel_path)
        if cid is not None:
            seed_community_ids.add(cid)

    category_hints = _SECTION_CATEGORY_HINTS.get(section, set())
    all_scored: list[tuple[float, float, float, float, float, dict]] = []
    _below_floor: list[tuple[float, float, float, float, float, dict]] = []
    for r in all_candidate_rows:
        total, bm25, sym, mod, cent = _compute_chunk_score(
            r, terms, scorer, stage1_files, ctx, seed_community_ids, category_hints, symbol_index
        )
        # Hard floor: chunks scoring below _RERANK_MIN_SCORE never enter the final
        # ranking. Prevents weak matches from filling the budget tail and polluting
        # LLM context. Safety fallback below ensures we never return empty if
        # ANY chunks scored > 0.
        if total >= _RERANK_MIN_SCORE:
            all_scored.append((total, bm25, sym, mod, cent, r))
        elif total > 0:
            _below_floor.append((total, bm25, sym, mod, cent, r))

    # Safety: if ZERO chunks cleared the floor but there are scoring candidates,
    # fall back to top-5 of the below-floor set so the caller never gets empty.
    if not all_scored and _below_floor:
        _below_floor.sort(key=lambda x: -x[0])
        all_scored = _below_floor[:5]
        logger.debug(
            "[two_stage] all chunks below rerank floor (%s); kept top %d by score as fallback",
            _RERANK_MIN_SCORE, len(all_scored),
        )

    # Build per-file total chunk counts + size/symbol stats for smart cap.
    file_total_chunks: dict[str, int] = {path: len(rows) for path, rows in file_rows.items()}
    file_size_stats = await _load_file_size_stats(snapshot_id)
    ranked, used_cpp = _rank_and_budget(
        all_scored, budget, native,
        central_files=ctx.central_files,
        file_total_chunks=file_total_chunks,
        file_size_stats=file_size_stats,
    )

    used_tokens = sum(c.token_estimate for c in ranked)

    stage3 = TwoStageStage3Result(
        ranked=ranked,
        used_tokens=used_tokens,
        budget_tokens=budget,
        used_cpp_ranker=used_cpp,
    )

    return TwoStageBundle(
        snapshot_id=snapshot_id,
        query=query,
        section=section,
        stage1={"candidates": [c.model_dump() for c in stage1]},
        stage2={"expansions": [e.model_dump() for e in stage2_expansions]},
        stage3=stage3,
    )


async def retrieve_two_stage_as_bundle(
    snapshot_id: str,
    query: str,
    section: RetrievalSection,
    budget: int,
    mode: RetrievalMode = RetrievalMode.HYBRID,
    min_confidence: float | None = None,
) -> RetrievalBundle:
    """Run the 2-stage pipeline and return a RetrievalBundle (agent-compatible interface)."""
    bundle = await retrieve_two_stage(snapshot_id, query, section, budget, min_confidence=min_confidence)
    evidences = [
        RetrievalEvidence(
            chunk_id=c.chunk_id,
            rel_path=c.rel_path,
            chunk_index=c.chunk_index,
            reason_codes=["2stage-bm25", "graph-expand"] if c.symbol_bonus > 1.0 or c.module_bonus > 1.0 else ["2stage-bm25"],
            score=c.score,
            token_estimate=c.token_estimate,
            excerpt=c.excerpt,
        )
        for c in bundle.stage3.ranked
    ]
    quality = None
    if bundle.stage3.ranked:
        terms = _query_terms(query)
        top_score = bundle.stage3.ranked[0].score if bundle.stage3.ranked else 0.0
        quality = compute_retrieval_quality(terms, bundle.stage3.ranked, top_score)
    return RetrievalBundle(
        snapshot_id=snapshot_id,
        mode=mode,
        section=section,
        query=query,
        budget_tokens=budget,
        used_tokens=bundle.stage3.used_tokens,
        evidences=evidences,
        quality=quality,
    )
