"""
/api/receipts - per-pipeline and tenant-wide receipt lookups.

GET /api/receipts                  -> last 50 receipts across all pipelines
GET /api/receipts/{pipeline_id}    -> last 25 receipts for one pipeline
    ?limit=N overrides the cap (max 100)

Session-gated via require_tenant; never exposes another tenant's receipts.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from ..services import receipts
from ..services.tenant_ctx import require_tenant

router = APIRouter(tags=["receipts"])

_SAFE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


@router.get("/api/receipts")
async def api_receipts_all(
    tenant_id: str = Depends(require_tenant),
    limit: int = Query(default=50, ge=1, le=100),
) -> JSONResponse:
    rows = receipts.list_all(tenant_id, limit=limit)
    return JSONResponse({"tenant_id": tenant_id, "receipts": rows})


@router.get("/api/receipts/{pipeline_id}")
async def api_receipts_pipeline(
    pipeline_id: str,
    tenant_id: str = Depends(require_tenant),
    limit: int = Query(default=25, ge=1, le=100),
) -> JSONResponse:
    if not _SAFE.match(pipeline_id):
        raise HTTPException(status_code=404, detail="pipeline not found")
    rows = receipts.list_for_pipeline(tenant_id, pipeline_id, limit=limit)
    return JSONResponse({"tenant_id": tenant_id, "pipeline_id": pipeline_id, "receipts": rows})
