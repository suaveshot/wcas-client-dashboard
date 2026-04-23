"""
/api/settings - tenant preference updates.

POST /api/settings            -> update keyed prefs. Returns the merged dict.
POST /api/settings/pipeline/<pipeline_id>/require_approval -> toggle per-pipeline.

All session-gated via require_tenant.
"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..services import activity_feed, tenant_prefs
from ..services.tenant_ctx import require_tenant

log = logging.getLogger("dashboard.settings")

router = APIRouter(tags=["settings"])

_SAFE_PIPE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class PrefsUpdate(BaseModel):
    privacy_default: bool | None = None
    feed_dense_default: bool | None = None
    email_digest: bool | None = None
    errors_only: bool | None = None
    timezone: str | None = Field(default=None, max_length=48)


class ApprovalToggle(BaseModel):
    require_approval: bool


@router.get("/api/settings")
async def api_settings_get(tenant_id: str = Depends(require_tenant)) -> JSONResponse:
    return JSONResponse({"tenant_id": tenant_id, "prefs": tenant_prefs.read(tenant_id)})


@router.post("/api/settings")
async def api_settings_set(body: PrefsUpdate, tenant_id: str = Depends(require_tenant)) -> JSONResponse:
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    merged = tenant_prefs.write(tenant_id, updates)
    try:
        activity_feed.append_decision(
            tenant_id=tenant_id,
            actor="owner",
            kind="settings.update",
            text=f"Updated settings: {', '.join(updates.keys()) or 'no changes'}",
        )
    except OSError:
        log.exception("decision log write failed tenant=%s", tenant_id)
    return JSONResponse({"ok": True, "prefs": merged})


@router.post("/api/settings/pipeline/{pipeline_id}/require_approval")
async def api_settings_require_approval(
    pipeline_id: str,
    body: ApprovalToggle,
    tenant_id: str = Depends(require_tenant),
) -> JSONResponse:
    if not _SAFE_PIPE.match(pipeline_id):
        raise HTTPException(status_code=400, detail="invalid pipeline_id")
    merged = tenant_prefs.set_require_approval(tenant_id, pipeline_id, body.require_approval)
    verb = "on" if body.require_approval else "off"
    try:
        activity_feed.append_decision(
            tenant_id=tenant_id,
            actor="owner",
            kind="settings.require_approval",
            text=f"Turned 'Approve before send' {verb} for {pipeline_id}",
        )
    except OSError:
        pass
    return JSONResponse({"ok": True, "prefs": merged})
