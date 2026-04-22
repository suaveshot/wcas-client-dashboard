"""
Tenant-scoped pipeline status.

GET /api/pipelines
    Returns the caller's own pipelines only. require_tenant raises
    401 if no session, so there's no path for an anonymous visitor
    to read another tenant's data.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ..services import telemetry
from ..services.tenant_ctx import require_tenant

router = APIRouter(tags=["telemetry"])


@router.get("/api/pipelines")
async def api_pipelines(tenant_id: str = Depends(require_tenant)) -> JSONResponse:
    return JSONResponse({
        "tenant_id": tenant_id,
        "pipelines": telemetry.pipelines_for(tenant_id),
    })
