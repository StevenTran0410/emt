"""DocGraph endpoints."""

import json

from fastapi import APIRouter, HTTPException, Query
from starlette.responses import StreamingResponse

from domain.doc_graph.service import DocGraphService
from domain.doc_graph.types import (
    BuildDocGraphRequest,
    DocGraphClusterSummary,
    DocGraphEdgesResponse,
    DocGraphMismatchesResponse,
    DocGraphNodesResponse,
    DocGraphSummary,
)
from shared.http_utils import handle_value_error

router = APIRouter(tags=["doc-graph"])
_service = DocGraphService()


@router.get("/clusters", response_model=list[DocGraphClusterSummary])
@handle_value_error
async def list_doc_graph_clusters() -> list[DocGraphClusterSummary]:
    return await _service.list_clusters()


@router.delete("/clusters/{cluster_id}")
@handle_value_error
async def delete_doc_graph_cluster(cluster_id: str) -> dict[str, bool]:
    await _service.delete_cluster(cluster_id)
    return {"ok": True}


@router.post("/build", response_model=DocGraphSummary)
@handle_value_error
async def build_doc_graph(body: BuildDocGraphRequest) -> DocGraphSummary:
    return await _service.build(body)


@router.post("/build-stream")
async def build_doc_graph_stream(body: BuildDocGraphRequest):
    async def generate():
        try:
            async for event in _service.build_stream(body):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/summary/{cluster_id}", response_model=DocGraphSummary)
@handle_value_error
async def get_doc_graph_summary(cluster_id: str) -> DocGraphSummary:
    try:
        return await _service.summary(cluster_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/nodes/{cluster_id}", response_model=DocGraphNodesResponse)
@handle_value_error
async def list_doc_graph_nodes(
    cluster_id: str,
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    node_type: str | None = Query(None),
) -> DocGraphNodesResponse:
    try:
        return await _service.nodes(cluster_id, limit=limit, offset=offset, node_type=node_type)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/edges/{cluster_id}", response_model=DocGraphEdgesResponse)
@handle_value_error
async def list_doc_graph_edges(
    cluster_id: str,
    limit: int = Query(2000, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    edge_type: str | None = Query(None),
) -> DocGraphEdgesResponse:
    try:
        return await _service.edges(cluster_id, limit=limit, offset=offset, edge_type=edge_type)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/mismatches/{cluster_id}", response_model=DocGraphMismatchesResponse)
@handle_value_error
async def list_doc_graph_mismatches(
    cluster_id: str,
    severity: str | None = Query(None),
) -> DocGraphMismatchesResponse:
    try:
        return await _service.mismatches(cluster_id, severity=severity)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/export/{cluster_id}")
@handle_value_error
async def export_doc_graph_json(cluster_id: str) -> dict:
    try:
        return await _service.export_json(cluster_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
