"""
PC -> VPS heartbeat receiver.

Each Americal Patrol pipeline calls `Americal Patrol/shared/push_heartbeat.py`
at the end of its run; that script POSTs to us with:

    Header  X-Heartbeat-Secret   shared secret from HEARTBEAT_SHARED_SECRET
    Header  X-Tenant-Id          tenant slug (e.g. "americal_patrol")
    Body    JSON                 {pipeline_id, status, last_run, summary, state_summary}

No session cookie is expected here - the shared secret plus per-tenant
rate limit is the security model. Tenant slug is trusted because only
the shared secret grants access to this endpoint in the first place.
"""

import logging
import os

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from ..services import dispatch, heartbeat_store, rate_limit

log = logging.getLogger("dashboard.heartbeat")

router = APIRouter(tags=["heartbeat"])


@router.post("/api/heartbeat")
async def api_heartbeat(
    request: Request,
    x_heartbeat_secret: str | None = Header(default=None, alias="X-Heartbeat-Secret"),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> JSONResponse:
    expected = os.getenv("HEARTBEAT_SHARED_SECRET", "")
    if not expected or x_heartbeat_secret != expected:
        raise HTTPException(status_code=401, detail="unauthorized")

    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001  malformed body
        raise HTTPException(status_code=400, detail="invalid json")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="invalid payload")

    # Header is the trusted source; fall back to body.tenant_id for pipelines
    # that haven't been updated yet. Both get stamped through the same slug
    # validator in heartbeat_store.write_snapshot.
    tenant_id = (x_tenant_id or str(payload.get("tenant_id") or "")).strip().lower()
    if not tenant_id:
        return JSONResponse({"received": True, "stored": False, "status": "no_tenant"})

    if not rate_limit.heartbeat_limiter.allow(tenant_id):
        raise HTTPException(status_code=429, detail="slow down")

    pipeline_id = str(payload.get("pipeline_id", "")).strip().lower()
    if not pipeline_id:
        raise HTTPException(status_code=400, detail="missing pipeline_id")

    try:
        path = heartbeat_store.write_snapshot(tenant_id, pipeline_id, payload)
    except heartbeat_store.HeartbeatError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except OSError:
        log.exception("heartbeat write failed tenant=%s pipeline=%s", tenant_id, pipeline_id)
        return JSONResponse({"received": True, "stored": False, "status": "write_failed"})

    # W3: drain optional events array into goal bumpers (closes goals F1).
    # Backward-compatible: heartbeats without an events key are no-ops.
    # Pipelines that emit events do {"events": [{"kind": "lead.created"}, ...]}.
    events = payload.get("events")
    if isinstance(events, list) and events:
        dispatch.handle_heartbeat_events(tenant_id, events)

    return JSONResponse({
        "received": True,
        "stored": True,
        "path": str(path),
    })
