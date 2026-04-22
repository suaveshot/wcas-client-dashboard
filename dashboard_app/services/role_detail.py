"""
Role detail page composer.

One pipeline at a time, rendered from its most-recent heartbeat snapshot
plus the same display-name lookup table used by the home grid.
"""

from __future__ import annotations

from typing import Any

from . import heartbeat_store, home_context, log_timeline


def _find_snapshot(tenant_id: str, role_slug: str) -> dict | None:
    """Match either the slug (dash form) or the underlying pipeline id (underscore form)."""
    underlying = role_slug.replace("-", "_")
    for row in heartbeat_store.read_all(tenant_id):
        pid = row.get("pipeline_id", "")
        if pid == role_slug or pid == underlying:
            return row
    return None


def build(tenant_id: str, role_slug: str) -> dict[str, Any]:
    snap = _find_snapshot(tenant_id, role_slug)
    underlying = role_slug.replace("-", "_")
    display_name = home_context._role_display(underlying)

    if snap is None:
        return {
            "role_slug": role_slug,
            "role_name": display_name,
            "has_snapshot": False,
            "status": "waiting",
            "status_text": "queued",
            "last_run": "waiting on first heartbeat",
            "summary": "",
            "received_at": "",
            "state_rows": [],
            "timeline": [],
            "log_tail": "",
        }

    payload = snap.get("payload") or {}
    last_run_text, _age = home_context._humanize_ago(payload.get("last_run") or snap.get("received_at", ""))
    status_raw = (payload.get("status") or "unknown").lower()
    _state, state_text, _grade, _spark = home_context._state_from_status(status_raw, _age)

    state_summary = payload.get("state_summary") or {}
    state_rows: list[dict[str, Any]] = []
    if isinstance(state_summary, dict):
        for k, v in state_summary.items():
            if isinstance(v, (str, int, float, bool)):
                state_rows.append({"label": k.replace("_", " ").title(), "value": str(v)})

    raw_tail = (payload.get("log_tail") or "")[:4000]
    timeline = log_timeline.parse(raw_tail, max_events=12)

    return {
        "role_slug": role_slug,
        "role_name": display_name,
        "has_snapshot": True,
        "status": status_raw,
        "status_text": state_text,
        "last_run": last_run_text,
        "summary": payload.get("summary") or "",
        "received_at": snap.get("received_at", ""),
        "state_rows": state_rows[:16],  # cap to avoid blowing up the card
        "timeline": [{"time": e.time_human, "level": e.level, "message": e.message} for e in timeline],
        "log_tail": raw_tail,
    }
