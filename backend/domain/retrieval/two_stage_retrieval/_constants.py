"""Shared constants and small helpers used across the two-stage retrieval pipeline."""
from __future__ import annotations

import re

from ..types import RetrievalSection

_WORD = re.compile(r"[A-Za-z0-9_]+")


def _row_get(row, key: str, default):
    """Safe accessor for sqlite3.Row (no .get method) and dict-like rows."""
    try:
        val = row[key]
    except (IndexError, KeyError):
        return default
    return default if val is None else val

_SYMBOL_OVERLAP_BONUS: float = 1.5
_MODULE_PROXIMITY_BONUS: float = 1.3
# Flat binary bonus (not a decay function despite the "MAX" name) -- see _compute_chunk_score's
# cent_bonus assignment below. rrf_fusion.py's build_graph_confidence_rank_list applies its own
# separate flat centrality_boost (1.5x); the two are intentionally independent: one
# scores a single chunk's BM25-based candidacy, the other scores a file's confidence-weighted
# rank-fusion signal -- same underlying "is this file structurally central" concept, applied to
# two different scoring mechanisms (additive score vs RRF input list).
_CENTRALITY_BONUS_MAX: float = 2.6

# Hard floor on rerank final score — chunks below this are dropped entirely
# before entering _rank_and_budget. Rationale: a "good" match in this pipeline
# scores ~10-20 (bm25 5-10 × module_bonus 1.3 + centrality 2.6 ≈ 9-15+); scores
# below 10 are typically incidental token hits with little signal, and letting
# them fill the token budget tail pollutes LLM context. Safety fallback in
# retrieve_two_stage keeps top-5 below-floor chunks if ALL candidates score low,
# so callers never get an empty bundle when some scoring was possible.
_RERANK_MIN_SCORE: float = 10.0

_CATEGORY_HINT_BONUS: float = 1.4

# Section-specific score cutoffs (absolute, relative)
_SECTION_SCORE_FLOOR: dict[RetrievalSection, tuple[float, float]] = {
    RetrievalSection.QA:              (0.5, 0.35),
    RetrievalSection.ARCHITECTURE:    (0.3, 0.15),
    RetrievalSection.CONVENTIONS:     (0.5, 0.25),
    RetrievalSection.FEATURE_MAP:     (0.5, 0.25),
    RetrievalSection.IMPORTANT_FILES: (0.5, 0.25),
    RetrievalSection.GLOSSARY:        (0.4, 0.20),
}
