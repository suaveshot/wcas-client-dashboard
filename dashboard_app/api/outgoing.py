"""
/api/outgoing - approval queue for pipeline drafts.

All endpoints require a session cookie via require_tenant.

    GET  /api/outgoing/pending
    POST /api/outgoing/{draft_id}/approve         (body optional: {edited_body})
    POST /api/outgoing/{draft_id}/skip            (body: {reason})

Each successful transition writes to the tenant's decisions.jsonl so the
transparency feed shows the owner what they just did.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..services import activity_feed, dispatch, outgoing_queue
from ..services.tenant_ctx import require_tenant

log = logging.getLogger("dashboard.outgoing")

router = APIRouter(tags=["outgoing"])


class ApproveBody(BaseModel):
    edited_body: str | None = Field(default=None, max_length=20_000)


class SkipBody(BaseModel):
    reason: str = Field(default="", max_length=240)


@router.get("/api/outgoing/pending")
async def api_outgoing_pending(tenant_id: str = Depends(require_tenant)) -> JSONResponse:
    drafts = outgoing_queue.list_pending(tenant_id)
    return JSONResponse({
        "tenant_id": tenant_id,
        "drafts": drafts,
        "summary": outgoing_queue.summary(tenant_id),
    })


@router.post("/api/outgoing/{draft_id}/approve")
async def api_outgoing_approve(
    draft_id: str,
    body: ApproveBody,
    tenant_id: str = Depends(require_tenant),
) -> JSONResponse:
    try:
        entry = outgoing_queue.approve(tenant_id, draft_id, edited_body=body.edited_body)
    except outgoing_queue.OutgoingError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    verb = "Edited and approved" if body.edited_body is not None else "Approved"
    try:
        activity_feed.append_decision(
            tenant_id=tenant_id,
            actor="owner",
            kind="outgoing.approve",
            text=f"{verb} a draft from {entry['pipeline_id']}: {entry.get('subject') or '(no subject)'}",
        )
    except OSError:
        log.exception("decision log write failed tenant=%s", tenant_id)

    # W3: dispatch the approved entry. Closes audits/phase0_approvals.md::F1.
    # On dispatch failure the archived entry is flipped to status=approved_send_failed
    # by dispatch.deliver_approved -> outgoing_queue.mark_send_failed. The
    # response surfaces the dispatch outcome separately so the FE can render a
    # green "Approved & sent" toast or a yellow "Approved - send failed" toast.
    dispatch_result = dispatch.deliver_approved(tenant_id, entry)

    return JSONResponse({
        "ok": True,
        "status": entry["status"],
        "draft_id": entry["id"],
        "dispatch": dispatch_result,
    })


@router.post("/api/outgoing/{draft_id}/skip")
async def api_outgoing_skip(
    draft_id: str,
    body: SkipBody,
    tenant_id: str = Depends(require_tenant),
) -> JSONResponse:
    try:
        entry = outgoing_queue.skip(tenant_id, draft_id, reason=body.reason)
    except outgoing_queue.OutgoingError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        activity_feed.append_decision(
            tenant_id=tenant_id,
            actor="owner",
            kind="outgoing.skip",
            text=f"Skipped a draft from {entry['pipeline_id']}: {entry.get('subject') or '(no subject)'}",
        )
    except OSError:
        log.exception("decision log write failed tenant=%s", tenant_id)

    return JSONResponse({"ok": True, "status": entry["status"], "draft_id": entry["id"]})
