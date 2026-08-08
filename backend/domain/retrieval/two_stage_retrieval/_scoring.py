"""Stage-1 BM25 candidate scoring, 1-hop graph expansion, and the full per-chunk
rerank score used by stage 3."""
from __future__ import annotations

from ..bm25_scorer import CHUNK_TYPE_WEIGHT, BM25Scorer
from ..types import StageCandidate, StageExpansion
from ._constants import (
    _CATEGORY_HINT_BONUS,
    _CENTRALITY_BONUS_MAX,
    _MODULE_PROXIMITY_BONUS,
    _SYMBOL_OVERLAP_BONUS,
    _WORD,
    _row_get,
)
from ._graph_context import _GraphContext


def _stage1_score_rows(rows: list, scorer: BM25Scorer | None, terms: list[str], top_k: int = 100) -> list[StageCandidate]:
    candidates: list[tuple[float, StageCandidate]] = []
    for r in rows:
        content = r["content"] or ""
        path_low = r["rel_path"].lower()
        chunk_type = _row_get(r, "chunk_type", "block")
        # Lowercase once; reused by both scorer and fallback path.
        content_low = content.lower()
        if scorer is not None:
            score = scorer.score(terms, content_low, path_low, chunk_type)
            if score <= 0.0:
                continue
        else:
            # Counter-based O(N+M) whole-word matching — aligns with the tokenisation
            # model used by the native BM25 scorer (word tokens, not substrings).
            token_counts = _WORD.findall(content_low)
            freq: dict[str, int] = {}
            for tok in token_counts:
                freq[tok] = freq.get(tok, 0) + 1
            hits = sum(freq.get(t, 0) for t in terms) + sum(2 for t in terms if t in path_low)
            if hits <= 0:
                continue
            score = float(hits)
        candidates.append((score, StageCandidate(
            chunk_id=r["id"],
            rel_path=r["rel_path"],
            chunk_index=int(r["chunk_index"]),
            bm25_score=score,
            token_estimate=int(r["token_estimate"] or max(1, len(content) // 4)),
            excerpt=content,
        )))
    candidates.sort(key=lambda x: -x[0])
    return [c for _, c in candidates[:top_k]]


def _python_1hop(seed: str, edge_tuples: list[tuple]) -> list[str]:
    neighbors: set[str] = set()
    for src, dst, _etype, is_ext in edge_tuples:
        if is_ext:
            continue
        if src == seed and dst != seed:
            neighbors.add(dst)
        elif dst == seed and src != seed:
            neighbors.add(src)
    return sorted(neighbors)[:50]


def _expand_one(seed: StageCandidate, ctx: _GraphContext, native) -> tuple[StageExpansion, set[str]]:
    symbol_refs = list(ctx.file_symbol_refs.get(seed.rel_path, set()))

    cid = ctx.file_community.get(seed.rel_path)
    if cid is not None:
        community_members = list(ctx.community_members.get(cid, set()) - {seed.rel_path})
    else:
        community_members = []

    if ctx.edge_tuples:
        if native:
            try:
                result = native.expand_neighbors(seed.rel_path, ctx.edge_tuples, 1, 50)
                neighbor_files = list(set(result["nodes"]) - {seed.rel_path})
            except Exception:
                neighbor_files = _python_1hop(seed.rel_path, ctx.edge_tuples)
        else:
            neighbor_files = _python_1hop(seed.rel_path, ctx.edge_tuples)
    else:
        neighbor_files = []

    expanded = set(symbol_refs) | set(community_members) | set(neighbor_files)
    expansion = StageExpansion(
        seed_path=seed.rel_path,
        symbol_refs=symbol_refs,
        community_members=community_members,
        neighbor_files=neighbor_files,
        net_new_count=len(expanded),
    )
    return expansion, expanded


def _compute_chunk_score(
    r: dict,
    terms: list[str],
    scorer: BM25Scorer | None,
    seed_files: set[str],
    ctx: _GraphContext,
    seed_community_ids: set[int],
    category_hints: set[str],
    symbol_index: dict[str, list[tuple[str, int, int]]] | None = None,
) -> tuple[float, float, float, float, float]:
    content = r["content"] or ""
    path_low = r["rel_path"].lower()
    chunk_type = _row_get(r, "chunk_type", "block")
    # Lowercase once; reused by both scorer and fallback path.
    content_low = content.lower()
    if scorer is not None:
        bm25 = scorer.score(terms, content_low, path_low, chunk_type)
    else:
        # Counter-based O(N+M) whole-word matching — aligns with the tokenisation
        # model used by the native BM25 scorer (word tokens, not substrings).
        token_counts = _WORD.findall(content_low)
        freq: dict[str, int] = {}
        for tok in token_counts:
            freq[tok] = freq.get(tok, 0) + 1
        bm25 = float(sum(freq.get(t, 0) for t in terms) + sum(2 for t in terms if t in path_low))

    # Definition bonus: if term matches symbol in this chunk's line range
    definition_bonus = 0.0
    if symbol_index:
        chunk_start = _row_get(r, "start_line", 0)
        chunk_end = _row_get(r, "end_line", 0)
        chunk_rel_path = r["rel_path"]
        for term in terms:
            matching_symbols = symbol_index.get(term, [])
            for sym_path, sym_start, sym_end in matching_symbols:
                if (
                    sym_path == chunk_rel_path
                    and chunk_start > 0
                    and sym_start >= chunk_start
                    and sym_end <= chunk_end
                ):
                    definition_bonus = 3.0
                    break
            if definition_bonus > 0:
                break
    bm25 += definition_bonus

    if r["category"] in category_hints:
        bm25 += _CATEGORY_HINT_BONUS

    sym_bonus = _SYMBOL_OVERLAP_BONUS if r["rel_path"] in seed_files else 1.0
    cid = ctx.file_community.get(r["rel_path"])
    mod_bonus = _MODULE_PROXIMITY_BONUS if (cid is not None and cid in seed_community_ids) else 1.0
    _query_term_set = frozenset(t.lower() for t in terms)
    _content_tokens = frozenset(_WORD.findall(content_low))
    cent_bonus = _CENTRALITY_BONUS_MAX if (
        r["rel_path"] in ctx.central_files
        and bool(_query_term_set & _content_tokens)
    ) else 0.0
    # Apply chunk_type weight to the FULL total (not just BM25) so graph bonuses
    # (symbol/module/centrality) are also suppressed for low-value chunk types
    # like import_group. Otherwise a central file's import-only chunk still ranks
    # top-K because cent_bonus=2.6 dominates even after BM25 is scaled down.
    chunk_weight = CHUNK_TYPE_WEIGHT.get(chunk_type, 1.0)
    total = (bm25 * sym_bonus * mod_bonus + cent_bonus) * chunk_weight
    return total, bm25, sym_bonus, mod_bonus, cent_bonus
