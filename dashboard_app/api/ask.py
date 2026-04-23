"""
/api/ask - first real Opus call, grounded in the caller's own data.

Powers the drill-down modal's "Ask" footer. Given a natural-language question
about a pipeline (role), returns a short plain-English answer that cites the
pipeline's own heartbeat snapshot. Short and synchronous; not a chat session.

Guard rails:
  - require_tenant enforces session-scoped tenant (no cross-tenant reads)
  - cost_tracker + guardrails.review_outbound wrap the call
  - model defaults to Haiku for dev; demo override swaps to Opus
  - response length capped at 512 tokens to keep per-call cost bounded
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..services import guardrails, opus, rate_limit, telemetry
from ..services.tenant_ctx import require_tenant

log = logging.getLogger("dashboard.ask")

router = APIRouter(tags=["ask"])


class AskRequest(BaseModel):
    role_slug: str = Field(..., min_length=1, max_length=64)
    question: str = Field(..., min_length=3, max_length=500)


_SYSTEM = """You are a senior automation analyst embedded in a small-shop owner-operator's dashboard. You answer questions about ONE specific automation pipeline ("role") using only the evidence given. Rules:

- One to three sentences. Plain English. No em dashes. Never mention the name of any AI vendor.
- If the evidence does not clearly answer the question, say so and offer one specific follow-up the owner could check.
- Cite specific numbers or timestamps from the evidence. Never invent numbers.
- Do not recommend any change here. This surface is read-only; recommendations have their own surface with a review gate.
"""


def _pipeline_snapshot(tenant_id: str, role_slug: str) -> dict | None:
    for row in telemetry.pipelines_for(tenant_id):
        if row.get("pipeline_id") == role_slug:
            return row
    return None


@router.post("/api/ask")
async def api_ask(body: AskRequest, tenant_id: str = Depends(require_tenant)) -> JSONResponse:
    if not rate_limit.ask_limiter.allow(tenant_id):
        raise HTTPException(status_code=429, detail="Slow down a moment; try again in a minute.")

    snapshot = _pipeline_snapshot(tenant_id, body.role_slug)
    if snapshot is None:
        return JSONResponse(
            {"answer": "No recent telemetry for this role yet. Try again after its next run.",
             "sources": []},
        )

    evidence = (
        f"Pipeline: {body.role_slug}\n"
        f"Status: {snapshot.get('status')}\n"
        f"Last run: {snapshot.get('last_run')}\n"
        f"Summary: {snapshot.get('summary')}\n"
    )

    try:
        result = opus.chat(
            tenant_id=tenant_id,
            system=_SYSTEM,
            messages=[{"role": "user", "content": f"Evidence:\n{evidence}\n\nQuestion: {body.question}"}],
            max_tokens=512,
            temperature=0.3,
            kind="ask_pipeline",
            note=f"role={body.role_slug}",
            cache_system=True,
        )
    except opus.OpusBudgetExceeded as exc:
        raise HTTPException(status_code=429, detail=f"budget reached today: {exc}")
    except opus.OpusUnavailable:
        # Graceful fallback during local dev without an API key. Don't 500 the client.
        return JSONResponse(
            {"answer": "The assistant is offline right now. Refresh in a minute or ask Sam.",
             "sources": []},
        )

    reviewed = guardrails.review_outbound("ask", result.text)
    if reviewed.decision == "reject":
        log.warning("ask response rejected by guardrail role=%s reasons=%s", body.role_slug, reviewed.reasons)
        return JSONResponse(
            {"answer": "Couldn't produce a safe answer this time. Ask Sam if it's urgent.",
             "sources": []},
        )

    return JSONResponse({
        "answer": reviewed.content,
        "sources": [{"source": "heartbeat", "pipeline_id": body.role_slug, "last_run": snapshot.get("last_run")}],
        "cost_usd": result.usd,
        "model": result.model,
    })
