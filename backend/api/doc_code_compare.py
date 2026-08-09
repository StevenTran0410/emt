"""API router for Doc↔Code Completeness and Structural Link Comparison."""

from fastapi import APIRouter

from domain.doc_code_compare.service import DocCodeCompareService
from domain.doc_code_compare.types import (
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
