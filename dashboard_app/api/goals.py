"""
/api/goals - goal CRUD.

POST   /api/goals            -> add a goal
DELETE /api/goals/{goal_id}  -> remove a goal

All session-gated.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..services import activity_feed, goals
from ..services.tenant_ctx import require_tenant

log = logging.getLogger("dashboard.goals")

router = APIRouter(tags=["goals"])


class GoalBody(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)
    metric: str = Field(..., min_length=1, max_length=24)
    target: float = Field(..., gt=0)
    timeframe: str = Field(..., min_length=1, max_length=8)


@router.get("/api/goals")
async def api_goals_get(tenant_id: str = Depends(require_tenant)) -> JSONResponse:
    return JSONResponse({"tenant_id": tenant_id, "goals": goals.read(tenant_id)})


@router.post("/api/goals")
async def api_goals_add(body: GoalBody, tenant_id: str = Depends(require_tenant)) -> JSONResponse:
    try:
        entry = goals.add(
            tenant_id=tenant_id,
            title=body.title,
            metric=body.metric,
            target=body.target,
            timeframe=body.timeframe,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        activity_feed.append_decision(
            tenant_id=tenant_id,
            actor="owner",
            kind="goals.add",
            text=f"Pinned a new goal: {entry['title']}",
        )
    except OSError:
        pass
    return JSONResponse({"ok": True, "goal": entry})


@router.delete("/api/goals/{goal_id}")
async def api_goals_delete(goal_id: str, tenant_id: str = Depends(require_tenant)) -> JSONResponse:
    ok = goals.remove(tenant_id, goal_id)
    if not ok:
        raise HTTPException(status_code=404, detail="goal not found")
    try:
        activity_feed.append_decision(
            tenant_id=tenant_id,
            actor="owner",
            kind="goals.remove",
            text=f"Removed goal {goal_id}",
        )
    except OSError:
        pass
    return JSONResponse({"ok": True})
