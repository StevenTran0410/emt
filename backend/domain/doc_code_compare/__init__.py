"""Doc↔Code completeness and Structural Link comparison package."""

from .relation_adapter import CanonicalCodeRelation, load_canonical_code_relations
from .service import DocCodeCompareService
from .types import (
    ComparisonSummary,
    DocCodeCompareRequest,
    DocCodeCompareResponse,
    DocCodeRelationCompareRequest,
    DocCodeRelationCompareResponse,
    EligibilityInfo,
    PredicateRelationResult,
    RelationComparisonDetail,
    RelationEvidenceItem,
    RelationSummary,
    TypeComparisonResult,
)

__all__ = [
    "DocCodeCompareService",
    "DocCodeCompareRequest",
    "DocCodeCompareResponse",
    "TypeComparisonResult",
    "EligibilityInfo",
    "ComparisonSummary",
    "DocCodeRelationCompareRequest",
    "DocCodeRelationCompareResponse",
    "PredicateRelationResult",
    "RelationComparisonDetail",
    "RelationEvidenceItem",
    "RelationSummary",
    "CanonicalCodeRelation",
    "load_canonical_code_relations",
]
