"""
Per-tenant brand override.

GET /api/brand
    Returns the merged brand dict for the caller's tenant.

Static; safe to cache for 10 min on the client.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ..services import brand_resolver
from ..services.tenant_ctx import require_tenant

router = APIRouter(tags=["brand"])


@router.get("/api/brand")
async def api_brand(tenant_id: str = Depends(require_tenant)) -> JSONResponse:
    return JSONResponse({
        "tenant_id": tenant_id,
        "brand": brand_resolver.resolve(tenant_id),
    })
