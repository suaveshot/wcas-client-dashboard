"""
Activation sample-output API.

Two routes:

    POST /api/activation/generate-samples
        Kick off a batch generation for all 7 pipelines. Returns the
        collected sample list once the batch completes. Gated by the §0
        onboarding-approval check. Rate-limited to avoid runaway regens.

    GET /api/activation/samples
        Return every cached sample for the current tenant. Missing samples
        are omitted. Always returns 200 with a (possibly empty) list.

Both require an authenticated tenant via the existing session middleware.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from ..services import audit_log, clients_repo, heartbeat_store, rate_limit, sample_outputs
from ..services.tenant_ctx import require_tenant

log = logging.getLogger("dashboard.activation_samples")

router = APIRouter(tags=["activation_samples"])


@router.post("/api/activation/generate-samples")
async def generate_samples(tenant_id: str = Depends(require_tenant)) -> JSONResponse:
    """Trigger the 7-pipeline sample batch for the current tenant."""
    # §0 gate: the tenant must be approved (sample generation is an
    # expensive Opus call - a leaked session on an unapproved tenant
    # shouldn't burn our budget).
    if not clients_repo.is_onboarding_approved_by_tenant(tenant_id):
        audit_log.record(
            tenant_id=tenant_id,
            event="samples_denied_unapproved",
            ok=False,
        )
        raise HTTPException(status_code=403, detail="onboarding_not_approved")

    if not rate_limit.activation_samples_limiter.allow(tenant_id):
        raise HTTPException(status_code=429, detail="rate_limited")

    audit_log.record(
        tenant_id=tenant_id,
        event="samples_generate_start",
        ok=True,
    )
    try:
        samples = sample_outputs.generate_all_for_tenant(tenant_id)
    except Exception as exc:  # defensive: never 500 the endpoint
        log.exception("generate_all_for_tenant failed tenant=%s", tenant_id)
        audit_log.record(
            tenant_id=tenant_id,
            event="samples_generate_failed",
            ok=False,
            error=str(exc),
        )
        raise HTTPException(status_code=502, detail="sample_generation_failed") from exc

    audit_log.record(
        tenant_id=tenant_id,
        event="samples_generate_done",
        ok=True,
        sample_count=len(samples),
    )
    return JSONResponse({"samples": samples})


@router.get("/api/activation/samples")
async def get_samples(tenant_id: str = Depends(require_tenant)) -> JSONResponse:
    """Return any cached samples for this tenant (empty list if none)."""
    samples = sample_outputs.list_samples(tenant_id)
    return JSONResponse({"samples": samples})


@router.get("/api/activation/provisioning-plan")
async def get_provisioning_plan(tenant_id: str = Depends(require_tenant)) -> JSONResponse:
    """Return the JSON payload written by record_provisioning_plan, or an
    empty shape if the agent hasn't recorded a plan yet."""
    try:
        root = heartbeat_store.tenant_root(tenant_id)
    except heartbeat_store.HeartbeatError:
        return JSONResponse({"items": []})
    path: Path = root / "state_snapshot" / "provisioning_plan.json"
    if not path.exists():
        return JSONResponse({"items": []})
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return JSONResponse({"items": []})
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return JSONResponse({"items": []})
    return JSONResponse(payload)
