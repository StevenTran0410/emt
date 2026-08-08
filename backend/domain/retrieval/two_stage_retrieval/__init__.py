"""Two-stage retrieval pipeline: BM25 stage1 → graph expansion stage2 → rank_and_budget stage3.

Package facade — re-exports the original module's public and private names so
`from domain.retrieval.two_stage_retrieval import <name>` keeps working unchanged.
"""
from __future__ import annotations

# Re-exported for backward compatibility (originally imported into this module's
# namespace from sibling modules; other modules import them from here).
from ..bm25_scorer import CHUNK_TYPE_WEIGHT, BM25Scorer, _query_terms
from ..quality import compute_retrieval_quality
from ..service import _CHUNK_FULL_COLS, _SECTION_CATEGORY_HINTS
from ..types import (
    RankedChunk,
    RetrievalBundle,
    RetrievalEvidence,
    RetrievalMode,
    RetrievalSection,
    StageCandidate,
    StageExpansion,
    TwoStageBundle,
    TwoStageStage3Result,
)
from ._constants import (
    _CATEGORY_HINT_BONUS,
    _CENTRALITY_BONUS_MAX,
    _MODULE_PROXIMITY_BONUS,
    _RERANK_MIN_SCORE,
    _SECTION_SCORE_FLOOR,
    _SYMBOL_OVERLAP_BONUS,
    _WORD,
    _row_get,
)
from ._diversity import (
    _apply_diversity_filter,
    _compute_file_caps,
    _load_file_size_stats,
)
from ._graph_context import (
    _GraphContext,
    _load_graph_context,
    _symbol_cache,
    clear_symbol_cache,
    load_symbol_index,
)
from ._native import _NATIVE, _get_native
from ._pipeline import retrieve_two_stage, retrieve_two_stage_as_bundle
from ._ranking import _rank_and_budget
from ._scoring import (
    _compute_chunk_score,
    _expand_one,
    _python_1hop,
    _stage1_score_rows,
)

__all__ = [
    "CHUNK_TYPE_WEIGHT",
    "BM25Scorer",
    "compute_retrieval_quality",
    "RankedChunk",
    "RetrievalBundle",
    "RetrievalEvidence",
    "RetrievalMode",
    "RetrievalSection",
    "StageCandidate",
    "StageExpansion",
    "TwoStageBundle",
    "TwoStageStage3Result",
    "clear_symbol_cache",
    "load_symbol_index",
    "retrieve_two_stage",
    "retrieve_two_stage_as_bundle",
]
