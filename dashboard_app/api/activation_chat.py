"""POST /api/activation/chat - drives one turn of the Managed Agent loop.

Each request:
  1. Rate-limit check per tenant (20 / 5 min).
  2. If body.reset, drop the stored session id so the agent starts fresh.
  3. Run one turn. The SDK event loop handles tool_use -> dispatch ->
     tool_result internally inside activation_agent.run_turn.
  4. Re-read ring state + credential state so the UI can update the ring
     grid + Connect-Google hint in the same round-trip.

The endpoint is synchronous. A well-behaved turn completes in under 30s.
Longer probes (GA4 / GSC creation) push against a 45s budget, at which
point run_turn returns reached_idle=False and the UI can prompt the user
to send again.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..agents import activation_agent
from ..services import activation_state, credentials, rate_limit, roster, validation_probe
from ..services.tenant_ctx import require_tenant


# 20 messages per 5 minutes per tenant. Belt-and-suspenders alongside the
# $2/tenant/day cost cap. A real activation is 4-8 messages end-to-end;
# anything past 20 in 5 minutes is either a bug or someone being cute.
activation_chat_limiter = rate_limit.SlidingWindowLimiter(
    max_events=20, window_seconds=300
)


router = APIRouter(tags=["activation_chat"])


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    reset: bool = False


@router.post("/api/activation/chat")
def activation_chat(
    body: ChatIn,
    _request: Request,
    tenant_id: str = Depends(require_tenant),
) -> dict[str, Any]:
    if not activation_chat_limiter.allow(tenant_id):
        raise HTTPException(
            status_code=429,
            detail="Slow down, I'm still working on the last message.",
        )

    if body.reset:
        activation_agent.reset_session(tenant_id)

    turn = activation_agent.run_turn(tenant_id, body.message.strip())

    # Re-read tenant state so the UI reflects any ring advances the turn
    # triggered (via activate_pipeline tool calls inside run_turn).
    slugs = roster.role_slugs()
    rings = activation_state.ring_view(tenant_id, slugs)
    google_cred = credentials.load(tenant_id, "google")
    probe = validation_probe.load_result(tenant_id, "google")

    return {
        "events": turn["events"],
        "reached_idle": turn["reached_idle"],
        "usage": turn["usage"],
        "rings": rings,
        "google_connected": google_cred is not None,
        "google_validation_status": (google_cred or {}).get("validation_status", ""),
        "probe_summary": probe,
    }
