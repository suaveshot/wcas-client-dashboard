"""
Rule-based recommendation seeder.

Generates deterministic recommendations from current tenant state without
calling Opus. Every rec flows through the same guardrails + schema the
Day-4 Managed-Agent generator will use; this layer simply guarantees the
"What should we fix?" surface has SOMETHING real to show before the
Managed-Agent generator comes online.

Three rule families:
    1. Stale-error rule - any pipeline that's been erroring for >7 days
    2. Overdue rule - any pipeline not run in >3x typical cadence
    3. Needs-attention rule - pipeline payload sets `needs_attention=true`

Each rec that passes `guardrails.review_recommendation` surfaces to the
client. Anything failing the schema gets stamped as a draft via
`recommendations.finalize()` and flows to Sam's admin inbox.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import heartbeat_store, recommendations


# Typical cadence per pipeline in hours. Used to detect "overdue" state.
TYPICAL_CADENCE_HOURS = {
    "patrol": 24,
    "morning_reports": 24,
    "sales_pipeline": 24,
    "reviews": 48,
    "seo": 168,
    "blog": 168,
    "social": 24,
    "ads": 24,
    "gbp": 24,
    "chat_widget": 1,
    "incident_alerts": 6,
    "watchdog": 1,
}
DEFAULT_CADENCE = 48.0


def _role_display(pid: str) -> str:
    overrides = {
        "patrol": "Morning Reports",
        "morning_reports": "Morning Reports",
        "sales_pipeline": "Sales Pipeline",
        "seo": "SEO",
        "blog": "Blog Posts",
        "gbp": "Google Business",
        "social": "Social Posts",
        "reviews": "Reviews",
        "chat_widget": "Chat Widget",
        "ads": "Ads",
    }
    return overrides.get(pid, pid.replace("_", " ").title())


def _age_hours(iso_ts: str) -> float:
    if not iso_ts:
        return 9999.0
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return 9999.0
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600


def _stale_error_rec(snap: dict[str, Any]) -> dict[str, Any] | None:
    payload = snap.get("payload") or {}
    if (payload.get("status") or "").lower() != "error":
        return None
    last_run = payload.get("last_run") or snap.get("received_at", "")
    age_h = _age_hours(last_run)
    if age_h < 24 * 7:
        return None
    role_name = _role_display(snap.get("pipeline_id", ""))
    reason_text = (payload.get("summary") or "last known error").strip()
    days = int(age_h // 24)
    return {
        "headline": f"{role_name} has been erroring for {days} days.",
        "reason": (
            f"The last run failed with '{reason_text[:120]}'. Re-running the pipeline "
            f"after a reconnect usually clears this and restores weekly coverage. "
            f"Reversible if it doesn't help."
        ),
        "proposed_tool": "queue_pipeline_run",
        "proposed_args": {"pipeline_id": snap.get("pipeline_id")},
        "impact": {
            "metric": "health_restoration",
            "estimate": 1,
            "unit": "pipeline restored",
            "calculation": f"{days} days of missed runs × this role's cadence = opportunity lost to resume.",
        },
        "confidence": 9,
        "reversibility": "instant",
        "evidence": [{
            "source": "heartbeat",
            "datapoint": "status",
            "value": "error",
            "observed_at": last_run,
        }],
        "role_slug": (snap.get("pipeline_id") or "").replace("_", "-"),
        "goal": "HEALTH",
    }


def _overdue_rec(snap: dict[str, Any]) -> dict[str, Any] | None:
    payload = snap.get("payload") or {}
    pid = snap.get("pipeline_id", "")
    cadence = TYPICAL_CADENCE_HOURS.get(pid, DEFAULT_CADENCE)
    last_run = payload.get("last_run") or snap.get("received_at", "")
    age_h = _age_hours(last_run)
    if age_h < cadence * 3:
        return None
    if (payload.get("status") or "").lower() == "error":
        return None  # covered by stale-error rule
    role_name = _role_display(pid)
    days = max(1, int(age_h // 24))
    return {
        "headline": f"{role_name} hasn't run in {days} day{'s' if days != 1 else ''}.",
        "reason": (
            f"Typical cadence for this role is every {int(cadence)} hours but the last "
            f"heartbeat was {int(age_h)} hours ago. Scheduling a fresh run restores "
            f"predictable output."
        ),
        "proposed_tool": "schedule_followup",
        "proposed_args": {"pipeline_id": pid, "when": "now"},
        "impact": {
            "metric": "cadence_restoration",
            "estimate": 1,
            "unit": "run",
            "calculation": f"{int(age_h)}h since last run vs {int(cadence)}h expected cadence.",
        },
        "confidence": 7,
        "reversibility": "session",
        "evidence": [{
            "source": "heartbeat",
            "datapoint": "hours_since_last_run",
            "value": int(age_h),
            "observed_at": last_run,
        }],
        "role_slug": pid.replace("_", "-"),
        "goal": "HEALTH",
    }


def _needs_attention_rec(snap: dict[str, Any]) -> dict[str, Any] | None:
    payload = snap.get("payload") or {}
    if not payload.get("needs_attention"):
        return None
    pid = snap.get("pipeline_id", "")
    role_name = _role_display(pid)
    note = (payload.get("attention_note") or "").strip() or (payload.get("summary") or "").strip() or "review this role"
    return {
        "headline": f"{role_name} flagged something for your review.",
        "reason": note[:200],
        "proposed_tool": "noop",
        "proposed_args": {},
        "impact": {
            "metric": "owner_review",
            "estimate": 1,
            "unit": "decision",
            "calculation": "Pipeline self-reports it needs human eyes.",
        },
        "confidence": 8,
        "reversibility": "instant",
        "evidence": [{
            "source": "heartbeat",
            "datapoint": "needs_attention",
            "value": "true",
            "observed_at": payload.get("last_run") or snap.get("received_at", ""),
        }],
        "role_slug": pid.replace("_", "-"),
        "goal": "HEALTH",
    }


def build(tenant_id: str, limit: int = 3) -> list[dict[str, Any]]:
    """Return up to `limit` live recs. Drafts are written to admin inbox in
    `recommendations.finalize()` and are not returned here."""
    try:
        snaps = heartbeat_store.read_all(tenant_id)
    except heartbeat_store.HeartbeatError:
        return []

    candidates: list[dict[str, Any]] = []
    for snap in snaps:
        for rule in (_stale_error_rec, _overdue_rec, _needs_attention_rec):
            rec = rule(snap)
            if rec:
                candidates.append(rec)
                break  # one rec per pipeline max, highest-priority rule wins

    live: list[dict[str, Any]] = []
    for rec in candidates:
        finalized = recommendations.finalize(tenant_id, rec)
        if not finalized.get("draft"):
            live.append(finalized)
            if len(live) >= limit:
                break
    return live


def build_with_drafts(tenant_id: str, limit: int = 8) -> list[dict[str, Any]]:
    """Return every candidate with the draft flag set. Admin + /recommendations
    tab page consumes this; clients only see build() (live only)."""
    try:
        snaps = heartbeat_store.read_all(tenant_id)
    except heartbeat_store.HeartbeatError:
        return []

    out: list[dict[str, Any]] = []
    for snap in snaps:
        for rule in (_stale_error_rec, _overdue_rec, _needs_attention_rec):
            rec = rule(snap)
            if rec:
                out.append(recommendations.finalize(tenant_id, rec))
                break
    return out[:limit]
