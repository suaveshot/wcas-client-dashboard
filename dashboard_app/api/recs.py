"""
/api/recommendations/refresh  -  trigger a real Opus pass against the
tenant's full state, persist the result, and surface cost back to the
caller for the demo's "Updated. $0.04 spent." toast.

Guard rails:
  - require_tenant enforces session-scoped tenant_id
  - recs_refresh_limiter caps at 5/day/tenant (cost cap is the hard floor)
  - cost_tracker (inside opus.chat) enforces per-tenant + dev daily caps
  - guardrails (inside recommendations.finalize) re-vet every rec the
    model produces; refused candidates flow to drafts, not to clients

Error mapping for the front-end toast:
  429  rate limit OR daily budget exceeded
  502  RecsGenerationError (model output unparseable)
  503  OpusUnavailable (no SDK / no API key)
  500  unexpected
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import JSONResponse

from ..services import audit_log, dispatch, heartbeat_store, opus, rate_limit, recs_generator, recs_store
from ..services.tenant_ctx import require_tenant

log = logging.getLogger("dashboard.api.recs")

router = APIRouter(tags=["recommendations"])

_SAFE_REC_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$", re.IGNORECASE)
_ALLOWED_ACTIONS = {"apply", "dismiss"}


def _actions_path(tenant_id: str):
    return heartbeat_store.tenant_root(tenant_id) / "rec_actions.jsonl"


@router.post("/api/recommendations/{rec_id}/act")
async def api_recs_act(
    rec_id: str,
    body: dict = Body(default_factory=dict),
    tenant_id: str = Depends(require_tenant),
) -> JSONResponse:
    """Record an Apply or Dismiss action against a recommendation.

    Persists one JSONL row to rec_actions.jsonl plus an audit log entry.
    Returns the recorded entry so the UI can display the chosen action
    in its undo toast.
    """
    if not _SAFE_REC_ID.match(rec_id or ""):
        raise HTTPException(status_code=400, detail="invalid rec_id")
    action = (body.get("action") or "").strip().lower()
    if action not in _ALLOWED_ACTIONS:
        raise HTTPException(status_code=400, detail="action must be apply or dismiss")

    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "rec_id": rec_id,
        "action": action,
    }

    try:
        path = _actions_path(tenant_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, separators=(",", ":")) + "\n")
    except OSError:
        log.exception("rec_actions write failed for tenant=%s rec=%s", tenant_id, rec_id)
        raise HTTPException(status_code=500, detail="Could not save action")

    audit_log.record(
        tenant_id=tenant_id,
        event=f"recommendation_{action}",
        ok=True,
        rec_id=rec_id,
    )

    # W3: on Apply, hand off to dispatch.execute_rec for real per-rec-type
    # execution (closes audits/phase0_recommendations.md::F1). Dismiss is
    # intent-only and stays as the existing record-and-respond.
    response: dict = {"ok": True, "rec_id": rec_id, "action": action, "ts": entry["ts"]}
    if action == "apply":
        response["dispatch"] = dispatch.execute_rec(tenant_id, rec_id)
    return JSONResponse(response)


@router.post("/api/recommendations/refresh")
async def api_recs_refresh(tenant_id: str = Depends(require_tenant)) -> JSONResponse:
    if not rate_limit.recs_refresh_limiter.allow(tenant_id):
        raise HTTPException(
            status_code=429,
            detail="Daily refresh limit reached. Recommendations refresh up to 5 times per day.",
        )

    try:
        result = recs_generator.generate(tenant_id)
    except opus.OpusBudgetExceeded as exc:
        raise HTTPException(status_code=429, detail=f"Daily budget reached: {exc}")
    except opus.OpusUnavailable:
        raise HTTPException(status_code=503, detail="The assistant is offline. Try again in a minute.")
    except recs_generator.RecsGenerationError as exc:
        log.warning("recs generation parse failed for tenant=%s: %s", tenant_id, exc)
        raise HTTPException(
            status_code=502,
            detail="Couldn't read the model's response. Try again.",
        )
    except Exception as exc:  # noqa: BLE001 - last-resort safety net before the framework returns 500
        log.exception("recs refresh failed for tenant=%s", tenant_id)
        raise HTTPException(status_code=500, detail="Refresh failed. Try again.") from exc

    path = recs_store.write_today(
        tenant_id,
        recs=result["recs"],
        model=result["model"],
        usd=result["usd"],
        input_tokens=result["input_tokens"],
        output_tokens=result["output_tokens"],
    )

    live_count = sum(1 for r in result["recs"] if not r.get("draft"))
    draft_count = sum(1 for r in result["recs"] if r.get("draft"))

    return JSONResponse({
        "ok": True,
        "count": len(result["recs"]),
        "live_count": live_count,
        "draft_count": draft_count,
        "model": result["model"],
        "usd": round(float(result["usd"]), 6),
        "path": str(path.name),  # only the leaf, not the absolute path
    })
