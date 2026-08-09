"""Type definitions for Doc↔Code Completeness & Structural Link Comparison."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class DocCodeCompareRequest(BaseModel):
    cluster_id: str
    snapshot_id: str


class TypeComparisonResult(BaseModel):
    type: Literal["program", "job", "step", "dd", "dataset"]
    doc_count: int
    code_count: int
    matched: int
    undocumented: list[dict[str, Any]] = Field(default_factory=list)
    missing: list[dict[str, Any]] = Field(default_factory=list)
    unknown: list[dict[str, Any]] = Field(default_factory=list)


class EligibilityInfo(BaseModel):
    authoritative: bool
    reason: str


class ComparisonSummary(BaseModel):
    matched: int
    undocumented: int
    missing: int
    unknown: int


class DocCodeCompareResponse(BaseModel):
    cluster_id: str
    snapshot_id: str
    per_type: list[TypeComparisonResult]
    not_assessed: list[str] = [
        "field",
        "section",
        "paragraph",
        "performs",
        "branch",
        "loop",
        "handler",
        "exec_block",
        "copybook",
    ]
    eligibility: EligibilityInfo
    summary: ComparisonSummary


# ── Structural Link v1 Types ──


class DocCodeRelationCompareRequest(BaseModel):
    cluster_id: str
    snapshot_id: str


class RelationEvidenceItem(BaseModel):
    occurrence_key: str | None = None
    rel_path: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    source_store: str | None = None
    match_kind: str | None = None


class RelationComparisonDetail(BaseModel):
    doc_assertion_id: int | None = None
    side: str | None = None
    subject_key: str
    object_key: str
    endpoint_verdict: Literal["MATCH", "DOC_ONLY", "CODE_ONLY", "UNKNOWN"]
    multiplicity_verdict: Literal[
        "EXACT_SITE_MATCH", "COUNT_ONLY_MATCH", "COUNT_MISMATCH", "NOT_APPLICABLE", "UNKNOWN"
    ]
    doc_count: int
    code_count: int
    eligibility: str
    reason: str
    evidence: list[RelationEvidenceItem] = Field(default_factory=list)


class PredicateRelationResult(BaseModel):
    predicate: str
    matched: int
    doc_only: int
    code_only: int
    unknown: int
    details: list[RelationComparisonDetail] = Field(default_factory=list)


class RelationSummary(BaseModel):
    matched: int
    doc_only: int
    code_only: int
    unknown: int


class DocCodeRelationCompareResponse(BaseModel):
    cluster_id: str
    snapshot_id: str
    status: Literal["OK", "STALE_INPUT", "UNBOUND"]
    per_predicate: list[PredicateRelationResult]
    not_assessed: list[str] = [
        "binds_dd(dd->dataset)",
        "field/PIC",
        "behavioral(br/tbd/ddlimit)",
        "access_mode_correctness",
    ]
    summary: RelationSummary
