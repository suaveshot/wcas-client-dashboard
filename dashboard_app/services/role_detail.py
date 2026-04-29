"""
Role detail page composer.

One pipeline at a time, rendered from its most-recent heartbeat snapshot
plus the same display-name lookup table used by the home grid.

For role_slug == "seo", the SEO Recommendations Engine output (W5.5)
is also surfaced as a side panel; tenants without a recommender cache
just see the standard heartbeat view.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import brightlocal_master, heartbeat_store, home_context, log_timeline, seo_recommender


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

    seo_panel = _build_seo_panel(tenant_id) if underlying == "seo" else None

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
            "seo_panel": seo_panel,
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
        "seo_panel": seo_panel,
    }


def _build_seo_panel(tenant_id: str) -> dict[str, Any]:
    """Build the SEO Recommendations side panel for /roles/seo.

    Returns a dict with:
      recommendations  list of top 5 recs ranked by urgency
      generated_at_ago humanized "X days ago" or "" if no cache
      brightlocal_status label string for the "Local rank tracking" footer
      has_data         True if at least one rec OR brightlocal is provisioned
    """
    recs = seo_recommender.get_cached(tenant_id)
    meta = seo_recommender.get_cache_meta(tenant_id)

    generated_at = meta.get("generated_at")
    generated_at_ago = ""
    if isinstance(generated_at, (int, float)) and generated_at > 0:
        try:
            iso = datetime.fromtimestamp(generated_at, tz=timezone.utc).isoformat()
            generated_at_ago, _ = home_context._humanize_ago(iso)
        except (OverflowError, ValueError, OSError):
            generated_at_ago = ""

    bl_provisioned = brightlocal_master.is_provisioned()
    bl_location = brightlocal_master.get_tenant_location_id(tenant_id)
    if bl_provisioned and bl_location:
        bl_status_label = "Local rank tracking provided by WCAS - Active."
        bl_status_state = "active"
    elif bl_provisioned:
        bl_status_label = "Local rank tracking provided by WCAS - Provisioning needed."
        bl_status_state = "pending_location"
    else:
        bl_status_label = "Local rank tracking not yet provisioned."
        bl_status_state = "not_provisioned"

    return {
        "recommendations": recs[:seo_recommender.DEFAULT_TOP_N],
        "more_count": max(0, len(recs) - seo_recommender.DEFAULT_TOP_N),
        "generated_at_ago": generated_at_ago,
        "source_summary": meta.get("source_summary") or {},
        "brightlocal_status_label": bl_status_label,
        "brightlocal_status_state": bl_status_state,
        "has_data": bool(recs) or bl_provisioned,
    }
