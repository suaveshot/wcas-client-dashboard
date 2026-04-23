"""
/api/tenant - tenant-scoped admin actions the owner can take on themselves.

POST /api/tenant/pause    -> write tenant_config.json:status=paused
POST /api/tenant/resume   -> write tenant_config.json:status=active

Persists to /opt/wc-solns/<tenant>/tenant_config.json. Pipelines read this
file before firing; status=paused short-circuits to no-op. Kill-switch
design from ADR-016.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ..services import activity_feed, heartbeat_store
from ..services.tenant_ctx import require_tenant

log = logging.getLogger("dashboard.tenant")

router = APIRouter(tags=["tenant"])


def _config_path(tenant_id: str):
    return heartbeat_store.tenant_root(tenant_id) / "tenant_config.json"


def _set_status(tenant_id: str, status: str) -> dict:
    path = _config_path(tenant_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
    data["status"] = status
    data["status_updated_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


@router.post("/api/tenant/pause")
async def api_tenant_pause(tenant_id: str = Depends(require_tenant)) -> JSONResponse:
    data = _set_status(tenant_id, "paused")
    try:
        activity_feed.append_decision(
            tenant_id=tenant_id,
            actor="owner",
            kind="tenant.pause",
            text="Paused every role. No pipeline will send until resumed.",
        )
    except OSError:
        log.exception("decision log write failed tenant=%s", tenant_id)
    return JSONResponse({"ok": True, "status": "paused", "tenant_config": data})


@router.post("/api/tenant/resume")
async def api_tenant_resume(tenant_id: str = Depends(require_tenant)) -> JSONResponse:
    data = _set_status(tenant_id, "active")
    try:
        activity_feed.append_decision(
            tenant_id=tenant_id,
            actor="owner",
            kind="tenant.resume",
            text="Resumed all roles.",
        )
    except OSError:
        pass
    return JSONResponse({"ok": True, "status": "active", "tenant_config": data})
