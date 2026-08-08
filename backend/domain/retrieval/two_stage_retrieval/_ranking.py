"""Stage-3 rank-and-budget: native (C++) ranker with a Python fallback, followed
by the diversity filter."""
from __future__ import annotations

from ..types import RankedChunk
from ._constants import _row_get
from ._diversity import _apply_diversity_filter


def _rank_and_budget(
    scored: list[tuple[float, float, float, float, float, dict]],
    budget: int,
    native,
    central_files: set[str] | None = None,
    file_total_chunks: dict[str, int] | None = None,
    file_size_stats: dict[str, tuple[int, int]] | None = None,
) -> tuple[list[RankedChunk], bool]:
    used_cpp = False
    chunk_map: dict[str, tuple[float, float, float, float, float, dict]] = {}
    for total, bm25, sym, mod, cent, r in scored:
        chunk_map[r["id"]] = (total, bm25, sym, mod, cent, r)

    if native:
        try:
            inputs = [(r["id"], total, int(r["token_estimate"] or 1)) for total, _, _, _, _, r in scored]
            ranked_ids: list[str] = list(native.rank_and_budget(inputs, budget))
            used_cpp = True
            out: list[RankedChunk] = []
            for cid in ranked_ids:
                if cid not in chunk_map:
                    continue
                total, bm25, sym, mod, cent, r = chunk_map[cid]
                chunk_type = _row_get(r, "chunk_type", "block")
                out.append(RankedChunk(
                    chunk_id=cid,
                    rel_path=r["rel_path"],
                    chunk_index=int(r["chunk_index"]),
                    score=total,
                    chunk_type=chunk_type,
                    bm25_component=bm25,
                    symbol_bonus=sym,
                    module_bonus=mod,
                    centrality_bonus=cent,
                    token_estimate=int(r["token_estimate"] or 1),
                    excerpt=r["content"] or "",
                ))
            # Apply diversity filter with smart per-file caps.
            out = _apply_diversity_filter(
                out,
                central_files=central_files,
                file_total_chunks=file_total_chunks,
                file_size_stats=file_size_stats,
            )
            return out, used_cpp
        except Exception:
            pass

    scored.sort(key=lambda x: -x[0])
    out = []
    used = 0
    for total, bm25, sym, mod, cent, r in scored:
        tok = int(r["token_estimate"] or 1)
        if used + tok > budget:
            continue
        chunk_type = _row_get(r, "chunk_type", "block")
        out.append(RankedChunk(
            chunk_id=r["id"],
            rel_path=r["rel_path"],
            chunk_index=int(r["chunk_index"]),
            score=total,
            chunk_type=chunk_type,
            bm25_component=bm25,
            symbol_bonus=sym,
            module_bonus=mod,
            centrality_bonus=cent,
            token_estimate=tok,
            excerpt=r["content"] or "",
        ))
        used += tok
    # Apply diversity filter with smart per-file caps.
    out = _apply_diversity_filter(
        out,
        central_files=central_files,
        file_total_chunks=file_total_chunks,
        file_size_stats=file_size_stats,
    )
    return out, False
