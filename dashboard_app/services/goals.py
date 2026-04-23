"""
Per-tenant goals stored at /opt/wc-solns/<tenant>/goals.json.

Shape:
    {
        "updated_at": "<iso>",
        "goals": [
            {"id": "<hex>", "title": "...", "metric": "leads|reviews|calls|revenue|other",
             "target": 20, "current": 0, "timeframe": "90d", "created_at": "<iso>"}
        ]
    }

Max three pinned goals (UI enforces; service caps on write).
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from typing import Any

from . import heartbeat_store


MAX_GOALS = 3
ALLOWED_METRICS = {"leads", "reviews", "calls", "revenue", "other"}
ALLOWED_TIMEFRAMES = {"30d", "60d", "90d"}


def _path(tenant_id: str):
    return heartbeat_store.tenant_root(tenant_id) / "goals.json"


def read(tenant_id: str) -> dict[str, Any]:
    try:
        path = _path(tenant_id)
    except heartbeat_store.HeartbeatError:
        return {"goals": []}
    if not path.exists():
        return {"goals": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"goals": []}
    data.setdefault("goals", [])
    return data


def _write(tenant_id: str, data: dict[str, Any]) -> None:
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    path = _path(tenant_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def add(tenant_id: str, title: str, metric: str, target: float, timeframe: str) -> dict[str, Any]:
    if metric not in ALLOWED_METRICS:
        raise ValueError(f"metric must be one of {sorted(ALLOWED_METRICS)}")
    if timeframe not in ALLOWED_TIMEFRAMES:
        raise ValueError(f"timeframe must be one of {sorted(ALLOWED_TIMEFRAMES)}")
    title = (title or "").strip()
    if not title:
        raise ValueError("title required")
    try:
        target = float(target)
    except (TypeError, ValueError) as exc:
        raise ValueError("target must be a number") from exc
    if target <= 0:
        raise ValueError("target must be > 0")

    data = read(tenant_id)
    goals = list(data.get("goals") or [])
    if len(goals) >= MAX_GOALS:
        raise ValueError(f"max {MAX_GOALS} goals pinned; remove one to add another")
    entry = {
        "id": secrets.token_hex(4),
        "title": title[:120],
        "metric": metric,
        "target": target,
        "current": 0,
        "timeframe": timeframe,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    goals.append(entry)
    data["goals"] = goals
    _write(tenant_id, data)
    return entry


def remove(tenant_id: str, goal_id: str) -> bool:
    data = read(tenant_id)
    goals = list(data.get("goals") or [])
    new_goals = [g for g in goals if g.get("id") != goal_id]
    if len(new_goals) == len(goals):
        return False
    data["goals"] = new_goals
    _write(tenant_id, data)
    return True


def bump_current(tenant_id: str, goal_id: str, delta: float) -> bool:
    """Increment a goal's current value. Returns True if the goal was found."""
    data = read(tenant_id)
    goals = list(data.get("goals") or [])
    for g in goals:
        if g.get("id") == goal_id:
            g["current"] = float(g.get("current") or 0) + float(delta or 0)
            data["goals"] = goals
            _write(tenant_id, data)
            return True
    return False
