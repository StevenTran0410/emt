"""Pure RRF fusion/merge helpers: combine ranked signal lists (and fused+reranked lists) via reciprocal rank fusion, without any DB/graph orchestration."""

from __future__ import annotations

from haystack import Document
from haystack.utils.misc import _reciprocal_rank_fusion

from ..cross_encoder_rerank import rerank_fused_entries, release_gpu_cache
from ..types import FusedRankEntry, SignalRankEntry


def fuse_signal_lists(
    signal_lists: list[list[SignalRankEntry]],
    weights: list[float] | None = None,
) -> list[FusedRankEntry]:
    """Fuse multiple ranked signal lists via Reciprocal Rank Fusion (using haystack's _reciprocal_rank_fusion, k=61); returns FusedRankEntry list sorted by fused_score descending."""
    if not signal_lists or all(not lst for lst in signal_lists):
        return []

    # Build Document lists in rank order for each signal
    # Document.id must match chunk_id; content must be non-empty (for validation)
    document_lists = []
    signal_names: list[str] = []
    for signal_entries in signal_lists:
        if not signal_entries:
            continue
        signal_name = signal_entries[0].signal_name
        signal_names.append(signal_name)
        doc_list = [
            Document(
                id=entry.chunk_id,
                content=entry.excerpt or entry.chunk_id,  # non-empty required
                score=None,
            )
            for entry in signal_entries
        ]
        document_lists.append(doc_list)

    if not document_lists:
        return []

    # Call the real haystack RRF function (k=61 hardcoded inside)
    fused_docs = _reciprocal_rank_fusion(document_lists, weights=weights)

    # Pre-build chunk_id -> {ranks, excerpt, rel_path} in one O(total entries) pass instead of re-scanning every signal's entries per fused doc. Per signal, the FIRST occurrence of a chunk_id wins; for excerpt/rel_path, the first signal list (in order) yielding a truthy value wins.
    chunk_meta: dict[str, dict] = {}
    for signal_name, signal_entries in zip(signal_names, signal_lists):
        for rank, entry in enumerate(signal_entries, start=1):
            meta = chunk_meta.setdefault(
                entry.chunk_id,
                {"ranks": {}, "excerpt": "", "rel_path": "", "token_estimate": 0, "start_line": 0, "end_line": 0},
            )
            if signal_name not in meta["ranks"]:
                meta["ranks"][signal_name] = rank
            if not meta["excerpt"] and entry.excerpt:
                meta["excerpt"] = entry.excerpt
            if not meta["rel_path"] and entry.rel_path:
                meta["rel_path"] = entry.rel_path
            if not meta["token_estimate"] and entry.token_estimate:
                meta["token_estimate"] = entry.token_estimate
            if not meta["start_line"] and entry.start_line:
                meta["start_line"] = entry.start_line
                meta["end_line"] = entry.end_line

    fused_entries: list[FusedRankEntry] = []
    for fused_doc in fused_docs:
        chunk_id = fused_doc.id
        fused_score = float(fused_doc.score or 0.0)
        meta = chunk_meta.get(
            chunk_id,
            {"ranks": {}, "excerpt": "", "rel_path": "", "token_estimate": 0, "start_line": 0, "end_line": 0},
        )

        fused_entries.append(
            FusedRankEntry(
                chunk_id=chunk_id,
                rel_path=meta["rel_path"],
                fused_score=fused_score,
                per_signal_ranks=meta["ranks"],
                excerpt=meta["excerpt"],
                token_estimate=meta["token_estimate"],
                start_line=meta["start_line"],
                end_line=meta["end_line"],
            )
        )

    # _reciprocal_rank_fusion() returns results in insertion order, not sorted by fused_score -- this explicit sort is what actually re-ranks.
    fused_entries.sort(key=lambda e: e.fused_score, reverse=True)
    return fused_entries


def _rerank_pool_in_batches(
    query: str,
    pool: list[FusedRankEntry],
    chunk_content_by_id: dict[str, str],
    batch_size: int,
) -> tuple[list, str]:
    """Rerank a pre-determined pool of candidates in sequential batches. Batches over the `pool` argument exactly as received -- does NOT re-derive _rerank_coverage_target, to avoid silently truncating expansion-only candidates from an already-capped pool built by the caller. Returns (reranked_entries, status_code) sorted by rerank_score descending."""
    all_reranked: list = []
    last_status = "ok"
    for start in range(0, len(pool), batch_size):
        batch = pool[start : start + batch_size]
        reranked_batch, status = rerank_fused_entries(
            query, batch, chunk_content_by_id, rank_offset=start
        )
        last_status = status
        if status != "ok":
            break
        all_reranked.extend(reranked_batch)
        release_gpu_cache()

    if not all_reranked:
        return [], last_status

    all_reranked.sort(key=lambda e: e.rerank_score, reverse=True)
    return all_reranked, "ok"


def _fuse_final_ranking(
    fused: list[FusedRankEntry],
    reranked: list,
    weights: tuple[float, float] = (0.6, 0.4),
) -> list[FusedRankEntry]:
    """Fuse fused_rank and cross_encoder_rank signals via RRF. `fused_rank` comes from the original `fused` list's order; `cross_encoder_rank` comes from `reranked`'s post-sort position (enumerate, not RerankedEntry.fused_rank). Expansion-only candidates (in reranked but not fused) contribute only via cross_encoder_rank. Returns FusedRankEntry list sorted by combined fused_score descending."""
    if not fused or not reranked:
        return []

    # Build two Document lists (mimicking fuse_signal_lists' pattern at lines 444-467)
    # List 1: fused-rank signal (built from the original fused list's order)
    fused_docs = [
        Document(
            id=entry.chunk_id,
            content=entry.excerpt or entry.chunk_id,
            score=None,
        )
        for entry in fused
    ]

    # List 2: cross_encoder_rank signal, built from reranked's post-sort position -- HARD REQUIREMENT: use enumerate(reranked, start=1), NOT RerankedEntry.fused_rank.
    reranked_docs = [
        Document(
            id=entry.chunk_id,
            content=entry.excerpt or entry.chunk_id,
            score=None,
        )
        for entry in reranked
    ]

    # Call _reciprocal_rank_fusion with the two lists and weights
    document_lists = [fused_docs, reranked_docs]
    fused_result_docs = _reciprocal_rank_fusion(document_lists, weights=list(weights))

    # Fresh dict literal per call -- dict(shared_template) would shallow-copy "ranks" across chunks.
    def _fresh_meta() -> dict:
        return {"ranks": {}, "excerpt": "", "rel_path": "", "token_estimate": 0, "start_line": 0, "end_line": 0}

    chunk_meta: dict[str, dict] = {}
    for rank, entry in enumerate(fused, start=1):
        meta = chunk_meta.setdefault(entry.chunk_id, _fresh_meta())
        if "fused" not in meta["ranks"]:
            meta["ranks"]["fused"] = rank
        if not meta["excerpt"] and entry.excerpt:
            meta["excerpt"] = entry.excerpt
        if not meta["rel_path"] and entry.rel_path:
            meta["rel_path"] = entry.rel_path
        if not meta["token_estimate"] and entry.token_estimate:
            meta["token_estimate"] = entry.token_estimate
        if not meta["start_line"] and entry.start_line:
            meta["start_line"] = entry.start_line
            meta["end_line"] = entry.end_line

    for rank, entry in enumerate(reranked, start=1):
        meta = chunk_meta.setdefault(entry.chunk_id, _fresh_meta())
        if "cross_encoder" not in meta["ranks"]:
            meta["ranks"]["cross_encoder"] = rank
        if not meta["excerpt"] and entry.excerpt:
            meta["excerpt"] = entry.excerpt
        if not meta["rel_path"] and entry.rel_path:
            meta["rel_path"] = entry.rel_path
        if not meta["token_estimate"] and entry.token_estimate:
            meta["token_estimate"] = entry.token_estimate
        if not meta["start_line"] and entry.start_line:
            meta["start_line"] = entry.start_line
            meta["end_line"] = entry.end_line

    # Unwrap Document results into FusedRankEntry (same pattern as fuse_signal_lists)
    final_entries: list[FusedRankEntry] = []
    for fused_doc in fused_result_docs:
        chunk_id = fused_doc.id
        fused_score = float(fused_doc.score or 0.0)
        meta = chunk_meta.get(chunk_id) or _fresh_meta()

        final_entries.append(
            FusedRankEntry(
                chunk_id=chunk_id,
                rel_path=meta["rel_path"],
                fused_score=fused_score,
                per_signal_ranks=meta["ranks"],
                excerpt=meta["excerpt"],
                token_estimate=meta["token_estimate"],
                start_line=meta["start_line"],
                end_line=meta["end_line"],
            )
        )

    # Sort descending by fused_score (same fix as rrf_fusion.py:512)
    final_entries.sort(key=lambda e: e.fused_score, reverse=True)
    return final_entries
