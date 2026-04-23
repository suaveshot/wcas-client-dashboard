"""
/api/ask_global - "ask your business" 1M-context Opus 4.7 query.

Flagship Track 0A surface. Composes all heartbeats + decisions + goals +
brand + KB + receipts summary into one structured prompt and sends a
single Opus call. The system prompt is cache-flagged so repeat questions
within ~5 minutes hit the prompt cache.

Guard rails:
  - require_tenant enforces session-scoped tenant
  - ask_global_limiter caps at 2/min/tenant (expensive calls)
  - cost_tracker (inside opus.chat) enforces daily caps
  - guardrails.review_outbound strips em dashes and vendor leaks
  - recent_asks.append logs successful asks for the sidebar pills

Never cites the model by name in the answer. Never invents numbers.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..services import global_ask, guardrails, opus, rate_limit, recent_asks
from ..services.tenant_ctx import require_tenant

log = logging.getLogger("dashboard.ask_global")

router = APIRouter(tags=["ask"])


class AskGlobalRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=500)


@router.post("/api/ask_global")
async def api_ask_global(body: AskGlobalRequest, tenant_id: str = Depends(require_tenant)) -> JSONResponse:
    if not rate_limit.ask_global_limiter.allow(tenant_id):
        raise HTTPException(
            status_code=429,
            detail="Take a breath. Global asks are capped at 2 per minute.",
        )

    context = global_ask.compose_context(tenant_id)
    user_content = (
        f"Here is the current state of this business:\n\n"
        f"{context['prompt']}\n\n"
        f"---\n\nQuestion: {body.question}"
    )

    try:
        result = opus.chat(
            tenant_id=tenant_id,
            system=global_ask.system_prompt(),
            messages=[{"role": "user", "content": user_content}],
            max_tokens=600,
            temperature=0.3,
            kind="ask_global",
            note=f"q={body.question[:80]}",
            cache_system=True,
        )
    except opus.OpusBudgetExceeded as exc:
        raise HTTPException(status_code=429, detail=f"budget reached today: {exc}")
    except opus.OpusUnavailable:
        return JSONResponse({
            "answer": "The assistant is offline right now. Refresh in a minute or ask Sam.",
            "sources": [],
        })

    reviewed = guardrails.review_outbound("ask_global", result.text)
    if reviewed.decision == "reject":
        log.warning("ask_global rejected by guardrail reasons=%s", reviewed.reasons)
        return JSONResponse({
            "answer": "Couldn't produce a safe answer this time. Try a more specific question or ask Sam.",
            "sources": [],
        })

    try:
        recent_asks.append(tenant_id, body.question, result.usd)
    except Exception:
        log.exception("recent_asks append failed; not blocking answer")

    return JSONResponse({
        "answer": reviewed.content,
        "sources": context["sources"][:6],  # cap displayed chips
        "cost_usd": result.usd,
        "model": result.model,
        "question": body.question,
    })
