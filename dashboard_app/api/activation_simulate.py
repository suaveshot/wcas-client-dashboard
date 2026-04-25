"""POST /api/activation/simulate-customer

The demo finale. After the wizard is complete, the samples grid shows
a hero card: "Want to see what your re-engagement email would say to
Maria Sanchez (37 days inactive)?" The owner clicks the button. We:

  1. Read the saved CRM mapping for this tenant.
  2. Pick the deterministic FIRST inactive student (so the demo is
     repeatable and the seed script can guarantee a clean name there).
  3. Generate one personalized re-engagement email via the
     sample_outputs `live_simulation` template with the student's name
     + days_inactive substituted in.
  4. Return the draft + the citations badge list so the UI can render
     both side by side.

This is the moment the entire pitch ('AI learns your voice and your
data') collapses into one provable artifact: a real, named, voice-
matched email that did not exist 3 seconds ago.

Cost guard: 1 call per tenant per 60 seconds. Cap at the per-tenant
daily Opus budget like every other generation.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ..services import crm_mapping, rate_limit, sample_outputs
from ..services.tenant_ctx import require_tenant

log = logging.getLogger("dashboard.activation_simulate")


# 1 call per tenant per 60s. Demo recording rarely needs more; if a
# judge clicks twice we want the second click to be cached.
simulate_limiter = rate_limit.SlidingWindowLimiter(
    max_events=1, window_seconds=60
)


router = APIRouter(tags=["activation_simulate"])


@router.post("/api/activation/simulate-customer")
def simulate_customer(
    _request: Request,
    tenant_id: str = Depends(require_tenant),
) -> dict[str, Any]:
    if not simulate_limiter.allow(tenant_id):
        raise HTTPException(
            status_code=429,
            detail="Just generated one. Wait a minute before regenerating.",
        )

    target = crm_mapping.first_inactive_for_simulation(tenant_id)
    if target is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "No CRM mapping with an inactive segment is available for "
                "this tenant. Complete the activation wizard first."
            ),
        )

    name = str(target.get("name") or "").strip()
    days_inactive = int(target.get("days_inactive", 30) or 30)
    if not name:
        raise HTTPException(status_code=409, detail="Inactive segment has no usable name.")

    try:
        # persist=False so the simulation doesn't pollute the samples/ dir
        # (it's a transient hero card, not one of the 7 saved samples).
        result = sample_outputs.generate_for_pipeline(
            tenant_id,
            "live_simulation",
            template_vars={"name": name, "days_inactive": days_inactive},
            persist=False,
        )
    except ValueError as exc:
        log.warning("simulate template error tenant=%s: %s", tenant_id, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        log.exception("simulate failed tenant=%s", tenant_id)
        raise HTTPException(status_code=502, detail=f"Generation failed: {exc.__class__.__name__}") from exc

    return {
        "name": name,
        "days_inactive": days_inactive,
        "title": result.get("title", ""),
        "body_markdown": result.get("body_markdown", ""),
        "preview": result.get("preview", ""),
        "citations": result.get("citations", []),
        "status": result.get("status", "ok"),
        "usd": result.get("usd", 0.0),
    }
