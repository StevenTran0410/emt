"""API router for Doc↔Code Completeness and Structural Link Comparison."""

from fastapi import APIRouter

from domain.doc_code_compare.service import DocCodeCompareService
from domain.doc_code_compare.types import (
    AiAssessmentResponse,
    AssessDocCodeRequest,
    DocCodeCompareRequest,
    DocCodeCompareResponse,
    DocCodeRelationCompareRequest,
    DocCodeRelationCompareResponse,
)

router = APIRouter(tags=["doc-code"])
_service = DocCodeCompareService()


@router.post("/compare", response_model=DocCodeCompareResponse)
async def compare_doc_code(body: DocCodeCompareRequest) -> DocCodeCompareResponse:
    """Compare document graph entities in a cluster against code graph snapshot."""
    return await _service.compare(body.cluster_id, body.snapshot_id)


@router.post("/compare-relations", response_model=DocCodeRelationCompareResponse)
async def compare_doc_code_relations(
    body: DocCodeRelationCompareRequest,
) -> DocCodeRelationCompareResponse:
    """Compare structural relationships (calls, copies, runs, binds_dd) between Doc and Code."""
    return await _service.compare_relations(body.cluster_id, body.snapshot_id)


@router.post("/assess", response_model=AiAssessmentResponse)
async def assess_doc_code(body: AssessDocCodeRequest) -> AiAssessmentResponse:
    """Run the AI Assessment for Doc↔Code validation."""
    return await _service.assess(body.cluster_id, body.snapshot_id, body.provider_id)


@router.get("/assessment", response_model=AiAssessmentResponse | None)
async def get_doc_code_assessment(
    cluster_id: str, snapshot_id: str
) -> AiAssessmentResponse | None:
    """Get the latest persisted AI Assessment for cluster and snapshot."""
    return await _service.get_latest_assessment(cluster_id, snapshot_id)
