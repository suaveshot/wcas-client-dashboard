"""POST /api/activation/panel-accept

Owners click 'This is us' on the voice card or 'Looks right' on the
CRM mapping panel. The UI POSTs here with the card/mapping id and any
edits. We:

  1. Persist the accepted state (voice_card.mark_accepted /
     crm_mapping.mark_accepted).
  2. Mirror any voice edits back into kb/voice.md so downstream
     surfaces (sample generator, recs, ask) read the corrected version.
  3. Trigger one follow-up agent turn so the conversation moves to
     the next step without the owner having to type anything.

Returns the agent's follow-up events + any new panels the next turn
produced (e.g. accepting the voice card frequently leads into the
CRM-mapping turn).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..agents import activation_agent
from ..services import (
    activation_state,
    credentials,
    crm_mapping,
    rate_limit,
    roster,
    tenant_kb,
    validation_probe,
    voice_card,
)
from ..services.tenant_ctx import require_tenant

log = logging.getLogger("dashboard.activation_panel")


# 10 acceptances per 5 minutes is plenty - each happy path uses 2
# (voice card + CRM mapping). Anything past 10 is misbehavior.
panel_accept_limiter = rate_limit.SlidingWindowLimiter(
    max_events=10, window_seconds=300
)


router = APIRouter(tags=["activation_panel"])


class PanelAcceptIn(BaseModel):
    type: str = Field(pattern=r"^(voice_card|crm_mapping)$")
    card_id: str = Field(min_length=4, max_length=64)
    edits: dict[str, Any] = Field(default_factory=dict)
    follow_up_message: str = Field(default="", max_length=400)


def _voice_followup_message(card: dict[str, Any]) -> str:
    """Default user-message we send into the agent loop after voice acceptance."""
    return (
        "I just accepted the voice card. Now ask me what CRM or booking "
        "system I use to track customers."
    )


def _crm_followup_message(mapping: dict[str, Any]) -> str:
    """Default user-message we send into the agent loop after CRM acceptance."""
    seg_count = len(mapping.get("segments", []))
    return (
        f"I just accepted the CRM mapping ({seg_count} segments). Tell me to "
        "click the Connect Google button above and walk me through what gets "
        "activated next."
    )


@router.post("/api/activation/panel-accept")
def panel_accept(
    body: PanelAcceptIn,
    _request: Request,
    tenant_id: str = Depends(require_tenant),
) -> dict[str, Any]:
    if not panel_accept_limiter.allow(tenant_id):
        raise HTTPException(
            status_code=429,
            detail="Too many panel acceptances. Slow down.",
        )

    if body.type == "voice_card":
        updated = voice_card.mark_accepted(
            tenant_id, card_id=body.card_id, edits=body.edits
        )
        if updated is None:
            raise HTTPException(
                status_code=404,
                detail="No matching voice card to accept (maybe it was regenerated).",
            )
        # If the owner edited the voice sample, mirror the edit into voice.md
        # so downstream Opus surfaces use the owner-approved version.
        if "voice_sample" in body.edits or "traits" in body.edits:
            md_lines = [
                "## Voice traits",
                "",
                *(f"- {t}" for t in updated["traits"]),
                "",
                "## Sample message in this voice",
                "",
                f"_Context: {updated['sample_context'] or 'general greeting'}_",
                "",
                updated["voice_sample"],
                "",
                "## For comparison: generic AI version",
                "",
                updated["generic_sample"],
            ]
            try:
                tenant_kb.write_section(tenant_id, "voice", "\n".join(md_lines))
            except tenant_kb.KbError:
                log.warning("voice KB mirror failed tenant=%s", tenant_id)
        followup = body.follow_up_message.strip() or _voice_followup_message(updated)

    else:  # crm_mapping
        updated = crm_mapping.mark_accepted(
            tenant_id, mapping_id=body.card_id, edits=body.edits
        )
        if updated is None:
            raise HTTPException(
                status_code=404,
                detail="No matching CRM mapping to accept.",
            )
        followup = body.follow_up_message.strip() or _crm_followup_message(updated)

    # Run one more agent turn so the conversation flows naturally.
    # If the agent loop fails (Opus down, budget, etc.), we still return
    # the acceptance success; the UI shows a retry prompt.
    turn_events: list[dict[str, Any]] = []
    next_panels: list[dict[str, Any]] = []
    reached_idle = False
    try:
        turn = activation_agent.run_turn(tenant_id, followup)
        turn_events = turn["events"]
        reached_idle = turn["reached_idle"]
        for event in turn_events:
            if event.get("role") != "tool" or not event.get("ok"):
                continue
            if event.get("name") == "propose_voice_card":
                card = voice_card.load(tenant_id)
                if card and not card.get("accepted"):
                    next_panels.append({"type": "voice_card", "payload": card})
            elif event.get("name") == "propose_crm_mapping":
                mapping = crm_mapping.load(tenant_id)
                if mapping and not mapping.get("accepted"):
                    next_panels.append({"type": "crm_mapping", "payload": mapping})
    except Exception as exc:
        log.exception("follow-up turn failed tenant=%s", tenant_id)
        turn_events = [
            {"role": "system", "text": "Saved. Send me a message when you want to keep going."}
        ]

    slugs = roster.role_slugs()
    return {
        "accepted": True,
        "type": body.type,
        "events": turn_events,
        "reached_idle": reached_idle,
        "panels": next_panels,
        "rings": activation_state.ring_view(tenant_id, slugs),
        "google_connected": credentials.load(tenant_id, "google") is not None,
        "google_validation_status": (
            (credentials.load(tenant_id, "google") or {}).get("validation_status", "")
        ),
        "probe_summary": validation_probe.load_result(tenant_id, "google"),
    }
