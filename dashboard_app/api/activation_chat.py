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

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..agents import activation_agent
from ..services import (
    activation_state,
    audit_log,
    credentials,
    crm_mapping,
    rate_limit,
    roster,
    screenshot_vision,
    validation_probe,
    voice_card,
)
from ..services.tenant_ctx import require_tenant

log = logging.getLogger("dashboard.activation_chat")


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
    # Server-generated filenames from POST /api/activation/screenshot.
    # Max 3 attachments per turn - plenty for a 4-step setup flow.
    screenshots: list[str] = Field(default_factory=list, max_length=3)


def _prepend_screenshot_context(tenant_id: str, message: str, filenames: list[str]) -> str:
    """Describe each screenshot via Opus Vision + inline the descriptions so
    the Managed Agent (text-only loop) can reason about what's on screen."""
    if not filenames:
        return message
    described: list[str] = []
    for name in filenames:
        try:
            described.append(screenshot_vision.describe_path(tenant_id, name))
        except ValueError as exc:
            log.warning("screenshot resolve failed tenant=%s name=%s: %s", tenant_id, name, exc)
            described.append(f"[screenshot {name}: could not read ({exc})]")
    header_lines = [
        "[Attached screenshot context: the owner uploaded the following "
        "image(s). Use these descriptions to give accurate next-step guidance "
        "grounded in the actual current UI, not training-data memory.]",
    ]
    for i, desc in enumerate(described, start=1):
        header_lines.append(f"Screenshot {i} of {len(described)}:")
        header_lines.append(desc.strip())
        header_lines.append("")
    header_lines.append("Owner's message follows:")
    return "\n".join(header_lines) + "\n\n" + message


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

    message = body.message.strip()
    if body.screenshots:
        audit_log.record(
            tenant_id=tenant_id,
            event="chat_with_screenshots",
            ok=True,
            count=len(body.screenshots),
        )
        message = _prepend_screenshot_context(tenant_id, message, body.screenshots)

    turn = activation_agent.run_turn(tenant_id, message)

    # Re-read tenant state so the UI reflects any ring advances the turn
    # triggered (via activate_pipeline tool calls inside run_turn).
    slugs = roster.role_slugs()
    rings = activation_state.ring_view(tenant_id, slugs)
    google_cred = credentials.load(tenant_id, "google")
    probe = validation_probe.load_result(tenant_id, "google")

    # v0.6.0: surface any panel payloads the agent rendered this turn.
    # We inspect the tool events; if propose_voice_card or propose_crm_mapping
    # fired successfully, load the latest stored payload and ship it to the UI.
    panels: list[dict[str, Any]] = []
    for event in turn["events"]:
        if event.get("role") != "tool" or not event.get("ok"):
            continue
        if event.get("name") == "propose_voice_card":
            card = voice_card.load(tenant_id)
            if card and not card.get("accepted"):
                panels.append({"type": "voice_card", "payload": card})
        elif event.get("name") == "propose_crm_mapping":
            mapping = crm_mapping.load(tenant_id)
            if mapping and not mapping.get("accepted"):
                panels.append({"type": "crm_mapping", "payload": mapping})

    return {
        "events": turn["events"],
        "reached_idle": turn["reached_idle"],
        "usage": turn["usage"],
        "rings": rings,
        "google_connected": google_cred is not None,
        "google_validation_status": (google_cred or {}).get("validation_status", ""),
        "probe_summary": probe,
        "panels": panels,
    }
