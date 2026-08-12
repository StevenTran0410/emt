"""DocGraph endpoints."""

import json

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from domain.doc_graph.service import DocGraphService
from domain.doc_graph.types import (
    BuildBdFlowOnlyRequest,
    BuildBdFlowOnlyResponse,
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


@router.post("/bd-flow/build", response_model=BuildBdFlowOnlyResponse)
@handle_value_error
async def build_bd_flow_only(body: BuildBdFlowOnlyRequest) -> BuildBdFlowOnlyResponse:
    return await _service.build_bd_flow_only(body)


@router.post("/bd-flow/build-stream")
async def build_bd_flow_only_stream(body: BuildBdFlowOnlyRequest):
    async def generate():
        try:
            async for event in _service.build_bd_flow_only_stream(body):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


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


@router.get("/bd-flow/{cluster_id}")
@handle_value_error
async def get_bd_flow(cluster_id: str) -> dict:
    try:
        return await _service.bd_flow(cluster_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/bd-flow-overlay/{cluster_id}")
@handle_value_error
async def get_bd_flow_overlay(cluster_id: str) -> dict:
    try:
        return await _service.bd_flow_overlay(cluster_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/flow-integrity/{cluster_id}/{snapshot_id}/map")
@handle_value_error
async def get_flow_integrity_map(cluster_id: str, snapshot_id: str) -> dict:
    from domain.business_flow_integrity import get_e2e_flow_map
    from infrastructure.db.database import get_db
    db = get_db()
    return await get_e2e_flow_map(db, cluster_id, snapshot_id)


@router.get("/flow-integrity/{cluster_id}/{snapshot_id}/findings")
@handle_value_error
async def get_flow_integrity_findings_endpoint(cluster_id: str, snapshot_id: str) -> dict:
    from domain.business_flow_integrity import get_flow_integrity_findings
    from infrastructure.db.database import get_db
    db = get_db()
    return await get_flow_integrity_findings(db, cluster_id, snapshot_id)


class FlowIntegrityRunBody(BaseModel):
    provider_id: str | None = None


@router.post("/flow-integrity/{cluster_id}/{snapshot_id}/run")
@handle_value_error
async def run_flow_integrity_pipeline(
    cluster_id: str, snapshot_id: str, body: FlowIntegrityRunBody | None = None
) -> dict:
    from domain.business_flow_integrity import (
        align_bd_to_code,
        build_code_flow,
        get_flow_integrity_findings,
        run_flow_verdicts,
    )
    from infrastructure.db.database import get_db

    db = get_db()
    provider_id = body.provider_id if body else None

    await build_code_flow(db, snapshot_id)
    await align_bd_to_code(db, cluster_id, snapshot_id)
    await run_flow_verdicts(db, cluster_id, snapshot_id, provider_id=provider_id)
    return await get_flow_integrity_findings(db, cluster_id, snapshot_id)


class FlowIntegritySummaryBody(BaseModel):
    provider_id: str | None = None


@router.post("/flow-integrity/{cluster_id}/{snapshot_id}/summary")
@handle_value_error
async def generate_flow_integrity_summary_endpoint(
    cluster_id: str, snapshot_id: str, body: FlowIntegritySummaryBody | None = None
) -> dict:
    from domain.business_flow_integrity import generate_executive_summary
    from infrastructure.db.database import get_db

    db = get_db()
    provider_id = body.provider_id if body else None
    return await generate_executive_summary(db, cluster_id, snapshot_id, provider_id=provider_id)


