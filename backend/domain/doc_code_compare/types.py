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


# ── AI Assessment Types ──


class AssessDocCodeRequest(BaseModel):
    cluster_id: str
    snapshot_id: str
    provider_id: str | None = None


class AiEvidenceRef(BaseModel):
    kind: Literal["entity", "relation", "file"]
    ref: str


class AiConcern(BaseModel):
    severity: Literal["error", "warning", "info"]
    title: str
    detail: str
    evidence_refs: list[AiEvidenceRef] = Field(default_factory=list)
    recommendation: str


class AiAssessmentData(BaseModel):
    overall_verdict: Literal["ADEQUATE", "GAPS_FOUND", "INSUFFICIENT_EVIDENCE"]
    confidence: Literal["low", "medium", "high"]
    completeness_note: str
    correctness_note: str
    concerns: list[AiConcern] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class AiAssessmentResponse(BaseModel):
    cluster_id: str
    snapshot_id: str
    overall_verdict: Literal["ADEQUATE", "GAPS_FOUND", "INSUFFICIENT_EVIDENCE"]
    confidence: Literal["low", "medium", "high"]
    completeness_note: str
    correctness_note: str
    concerns: list[AiConcern] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    model: str
    generated_at: str
    from_cache: bool = False
    stale: bool = False


# ── Linked Multi-Graph Foundation Types ──


class LinkedGraphNode(BaseModel):
    id: str
    node_type: str
    display_name: str
    in_bd: bool = False
    in_dd: bool = False
    in_code: bool = False
    code_rel_path: str | None = None
    name_fallback_used: bool = False
    entity_verdict: Literal["matched", "undocumented", "missing", "unknown", "out_of_scope"]
    assessed: bool = True
    provenance: list[dict[str, Any]] = Field(default_factory=list)
    # Raw doc-node attributes (e.g. field PIC/datatype) so the UI can render a fields table.
    attributes: dict[str, Any] = Field(default_factory=dict)


class LinkedGraphEdge(BaseModel):
    edge_key: str
    src: str
    dst: str
    edge_type: str
    layer: Literal["doc", "doc_code", "code"]
    in_bd: bool = False
    in_dd: bool = False
    endpoint_verdict: Literal["MATCH", "DOC_ONLY", "CODE_ONLY", "UNKNOWN"]
    multiplicity_verdict: Literal[
        "EXACT_SITE_MATCH", "COUNT_ONLY_MATCH", "COUNT_MISMATCH", "NOT_APPLICABLE", "UNKNOWN"
    ] = "NOT_APPLICABLE"
    doc_count: int = 0
    code_count: int = 0
    count_differs: bool = False
    # True only for predicates the relation comparator actually judges (calls/copies/runs/
    # binds_dd). Documentation-internal edges (defines/rule_about/cites/references/accesses)
    # are NOT assessed — the frontend must render them neutral, not as a DOC_ONLY "miss".
    assessed: bool = True
    status: Literal["asserted", "external", "unresolved", "not_derivable"] = "asserted"
    subject_ok: bool = True
    object_ok: bool = True
    doc_key: str | None = None
    code_key: str | None = None
    confidence: str = "corroborating"
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    symbol_edges: list[dict[str, Any]] = Field(default_factory=list)


class CrossLinkItem(BaseModel):
    doc_id: str
    code_rel_path: str
    match_method: Literal["exact_key", "name_fallback"]
    candidates: list[str] = Field(default_factory=list)
    verdict: Literal["matched", "ambiguous", "unmatched"] = "matched"


class NotAssessedEntityCoverage(BaseModel):
    node_type: str
    count: int


class NotAssessedRelationCoverage(BaseModel):
    edge_type: str
    exists: int
    assessed: int


class NotAssessedCoverage(BaseModel):
    entities: list[NotAssessedEntityCoverage] = Field(default_factory=list)
    relations: list[NotAssessedRelationCoverage] = Field(default_factory=list)


class BdGroupItem(BaseModel):
    group_id: str
    bd_doc_id: str
    section_id: str | None = None
    label: str
    member_dd_ids: list[str] = Field(default_factory=list)
    derivation: str = "bd_section"
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class LinkedGraphResponse(BaseModel):
    cluster_id: str
    snapshot_id: str
    eligibility: EligibilityInfo
    nodes: list[LinkedGraphNode] = Field(default_factory=list)
    edges: list[LinkedGraphEdge] = Field(default_factory=list)
    cross_links: list[CrossLinkItem] = Field(default_factory=list)
    bd_groups: list[BdGroupItem] = Field(default_factory=list)
    not_assessed: NotAssessedCoverage
