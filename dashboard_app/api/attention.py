"""
/api/attention/act - records a caller's response to the home attention banner.

Three actions are accepted: `apply`, `dismiss`, `snooze`. Each writes a single
row to the tenant's decisions.jsonl so the transparency feed can show the
acknowledgement on the next render. There is no state to mutate yet (the
banner is content-driven, not DB-driven), so this endpoint is intentionally
thin: it's the audit log for what the owner clicked.

All three require a valid session cookie; require_tenant raises 401 otherwise.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..services import activity_feed
from ..services.tenant_ctx import require_tenant

log = logging.getLogger("dashboard.attention")

router = APIRouter(tags=["attention"])

_ALLOWED = {"apply", "dismiss", "snooze"}

_MESSAGES = {
    "apply": "Applied the attention banner recommendation.",
    "dismiss": "Dismissed the attention banner.",
    "snooze": "Snoozed the attention banner for 24 hours.",
}


class AttentionAct(BaseModel):
    action: str = Field(..., min_length=3, max_length=16)


@router.post("/api/attention/act")
async def api_attention_act(body: AttentionAct, tenant_id: str = Depends(require_tenant)) -> JSONResponse:
    action = body.action.strip().lower()
    if action not in _ALLOWED:
        raise HTTPException(status_code=400, detail="invalid action")

    text = _MESSAGES[action]
    try:
        activity_feed.append_decision(
            tenant_id=tenant_id,
            actor="owner",
            kind=f"attention.{action}",
            text=text,
        )
    except OSError:
        log.exception("decisions.jsonl write failed tenant=%s", tenant_id)
        raise HTTPException(status_code=500, detail="could not record action")

    return JSONResponse({"ok": True, "action": action})
