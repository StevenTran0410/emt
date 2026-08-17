"""FastAPI endpoints for Phase U User Flow Alignment (TICKET U4)."""
from __future__ import annotations

from typing import Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from domain.user_flow import (
    UserFlowRunResult,
    get_user_flow_graph,
    get_user_flow_report,
    import_user_flow_xlsx,
    run_user_flow_alignment,
)
from infrastructure.db.database import get_db

router = APIRouter(tags=["user-flow"])


class UserFlowImportRequest(BaseModel):
    path: str | None = None
    paths: list[str] | None = None
    provider_id: str | None = None


class UserFlowImportResponse(BaseModel):
    status: str
    doc_id: str


class UserFlowRunRequest(BaseModel):
    doc_id: str
    cluster_id: str
    snapshot_id: str
    provider_id: str | None = None


@router.get("/docs")
async def list_user_flow_docs() -> list[dict[str, Any]]:
    """List all imported customer user flow workbooks."""
    db = get_db()
    try:
        async with db.execute(
            "SELECT id, source_name, file_hash, imported_at FROM user_flow_docs ORDER BY imported_at DESC"
        ) as cur:
            rows = await cur.fetchall()
        return [
            dict(r) if hasattr(r, "keys") else {
                "id": r[0], "source_name": r[1], "file_hash": r[2], "imported_at": r[3]
            }
            for r in rows
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list user flow docs: {e}")


@router.post("/import", response_model=UserFlowImportResponse)
async def import_user_flow(body: UserFlowImportRequest) -> UserFlowImportResponse:
    """Import and structure a customer Excel test/flow scenario workbook or set of workbooks."""
    db = get_db()
    target_paths = body.paths if body.paths else ([body.path] if body.path else [])
    if not target_paths:
        raise HTTPException(status_code=400, detail="Must provide 'path' or 'paths'")
    try:
        doc_id = await import_user_flow_xlsx(db, target_paths, body.provider_id)
        return UserFlowImportResponse(status="ok", doc_id=doc_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"User flow import failed: {e}")


@router.post("/run", response_model=UserFlowRunResult)
async def run_user_flow(body: UserFlowRunRequest) -> UserFlowRunResult:
    """Execute User Flow Alignment pipeline (Fused Anchor+Mapper + Verdicts)."""
    db = get_db()
    try:
        return await run_user_flow_alignment(
            db=db,
            doc_id=body.doc_id,
            cluster_id=body.cluster_id,
            snapshot_id=body.snapshot_id,
            provider_id=body.provider_id,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"User flow run failed: {e}")


@router.get("/report")
async def get_report(
    doc_id: str,
    cluster_id: str,
    snapshot_id: str,
) -> dict[str, Any]:
    """Retrieve full User Flow Alignment report JSON."""
    db = get_db()
    try:
        return await get_user_flow_report(
            db=db,
            doc_id=doc_id,
            cluster_id=cluster_id,
            snapshot_id=snapshot_id,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"User flow report query failed: {e}")


@router.get("/graph")
async def get_graph(
    doc_id: str,
) -> dict[str, Any]:
    """Retrieve user flow as BD-graph compatible business_flows envelope (TICKET U5)."""
    db = get_db()
    try:
        return await get_user_flow_graph(
            db=db,
            doc_id=doc_id,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"User flow graph query failed: {e}")

