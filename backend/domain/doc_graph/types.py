"""Type definitions and Pydantic schemas for doc_graph domain."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

PARSER_VERSION = 1


class BuildDocGraphRequest(BaseModel):
    source_dir: str
    snapshot_id: str | None = None
    force_rebuild: bool = False
    llm_provider_id: str | None = None
    llm_enabled: bool = True


class BuildBdFlowOnlyRequest(BaseModel):
    bd_path: str
    snapshot_id: str | None = None
    llm_enabled: bool = False
    llm_provider_id: str | None = None


class BuildBdFlowOnlyResponse(BaseModel):
    cluster_id: str
    cluster_name: str
    node_count: int
    edge_count: int
    overlay_counts: dict[str, int] | None = None


class DocGraphSummary(BaseModel):
    cluster_id: str
    cluster_name: str
    source_dir: str
    bd_path: str
    document_count: int
    assertion_count: int
    node_count: int
    edge_count: int
    mismatch_count: int
    mismatches_by_severity: dict[str, int]
    generated_at: str
    snapshot_id: str | None = None


class DocGraphClusterSummary(BaseModel):
    cluster_id: str
    cluster_name: str
    generated_at: str
    node_count: int
    edge_count: int
    mismatch_count: int
    # The code snapshot this cluster was validated against — pair the linked-graph with THIS,
    # not an arbitrary snapshot, or the relation comparator returns STALE_INPUT.
    snapshot_id: str | None = None


class DocGraphNode(BaseModel):
    id: str
    cluster_id: str
    node_type: str
    display_name: str
    attributes: dict[str, Any] = {}
    provenance: list[dict[str, Any]] = []
    created_at: str


class DocGraphEdge(BaseModel):
    id: int | None = None
    cluster_id: str
    src_node_id: str
    dst_node_id: str
    edge_type: str
    edge_key: str
    attributes: dict[str, Any] = {}
    created_at: str


class DocGraphMismatch(BaseModel):
    id: int | None = None
    cluster_id: str
    fingerprint: str
    mismatch_type: str
    severity: str
    derivation: str = "deterministic"
    bd_location: dict[str, Any] | None = None
    dd_location: dict[str, Any] | None = None
    description: str
    evidence: list[Any] | None = None
    confidence: str | None = None
    created_at: str


class DocGraphNodesResponse(BaseModel):
    cluster_id: str
    total: int
    limit: int
    offset: int
    nodes: list[DocGraphNode]


class DocGraphEdgesResponse(BaseModel):
    cluster_id: str
    total: int
    limit: int
    offset: int
    edges: list[DocGraphEdge]


class DocGraphMismatchesResponse(BaseModel):
    cluster_id: str
    total: int
    mismatches: list[DocGraphMismatch]


@dataclass
class SectionMapHeading:
    section_id: str
    title: str
    payload_kind: str  # table, prose, labeled_list, mermaid, container


@dataclass
class SectionMapInfo:
    headings: list[dict[str, Any]] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class MermaidBlock:
    text: str
    fence_line: int  # 1-based line of the ```mermaid fence line


@dataclass
class ParsedDoc:
    id: str
    doc_kind: str  # 'bd' | 'dd_cobol' | 'dd_jcl'
    artifact_name: str
    doc_path: str
    content_sha256: str
    generated_at: str
    section_map: SectionMapInfo = field(default_factory=SectionMapInfo)
    tables: list[dict[str, Any]] = field(default_factory=list)
    labeled_ids: list[dict[str, Any]] = field(default_factory=list)
    mermaid_diagrams: list[MermaidBlock] = field(default_factory=list)
    raw_content: str = ""
    warnings: list[str] = field(default_factory=list)  # §7.7: recorded, never a hard-fail


@dataclass
class DocAssertion:
    side: str  # 'bd' | 'dd'
    # calls, copies, accesses, binds_dd, runs, field_width_relation, field_pic, references, cites
    predicate: str
    subject: str  # canonical node id or field id
    object: str | None = None  # target node id
    value: str | None = None  # literal or numeric value
    # {mode:'read'}, {relation:'wider_than'}, {dcb:{...}}
    qualifiers: dict[str, Any] = field(default_factory=dict)
    status: str | None = "asserted"  # 'asserted' | 'external' | 'unresolved' | 'not_derivable'
    doc_id: str = ""
    doc_span: dict[str, Any] = field(default_factory=dict)  # {section_id, line_start, line_end}
    # {source_artifact, line_start, line_end}
    source_span: dict[str, Any] = field(default_factory=dict)
    confidence: str = "authoritative"  # authoritative | corroborating | unverified
