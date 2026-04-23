"""
Home-surface context composer.

Builds the Jinja context the `home.html` template renders. Every field
the template references has a source of truth; when a source is missing,
we return a brand-aware empty placeholder rather than a scary blank.

Data sources (in priority order):
  1. Heartbeat snapshots written by services.heartbeat_store
     (live telemetry from the tenant's PC-side pipelines)
  2. Airtable Clients row (owner name, tenant display name, goals)
     - hackathon scope: reads owner_name + tenant display only
  3. Fallback mocks (Americal Patrol demo data) when neither source has data

Design rules honored:
  - Every sensitive number wears .ap-priv so privacy-mode blur works
  - Attention banner is singular (one banner or none)
  - Narrative + hero stats + roles grid render even when feed/recs are empty
  - Role card state derives from heartbeat status:
      ok   -> "active"
      error -> "error" (plus grade "F" if errored > 24h)
      paused -> "paused"
      overdue -> "attention"
      unknown -> "active" with grade=None
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import activity_feed, hero_stats, notifications, recent_asks, seeded_recs, telemetry


_SPARK_UP = "M0,22 L15,18 L30,20 L45,14 L60,16 L75,10 L90,12 L105,7 L120,9 L135,5 L150,7 L165,3 L180,5 L200,2"
_SPARK_DOWN = "M0,6 L20,8 L40,7 L60,11 L80,9 L100,14 L120,13 L140,17 L160,16 L180,20 L200,22"
_SPARK_FLAT = "M0,14 L25,13 L50,14 L75,13 L100,14 L125,13 L150,14 L175,13 L200,14"


_STATUS_ICON_PATHS = {
    "seo": "M3 3h18v18H3zM16 11l-4 4-4-4M12 15V3",
    "reviews": "M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z",
    "sales_pipeline": "M4 4h16v4H4zM4 12h16v4H4zM4 20h16",
    "ads": "M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83",
    "chat_widget": "M21 11.5a8.38 8.38 0 0 1-9 8.5 8.5 8.5 0 0 1-7.6-4.5L3 21l1.5-3.4A8.38 8.38 0 0 1 3 11.5 8.5 8.5 0 0 1 11.5 3 8.38 8.38 0 0 1 21 11.5z",
    "morning_reports": "M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
    "patrol": "M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
}


def _state_from_status(status: str, age_hours: float) -> tuple[str, str, str | None, str]:
    """(state, state_text, grade, spark_path) from a heartbeat status."""
    s = (status or "").lower()
    if s == "paused":
        return "paused", "paused", None, _SPARK_FLAT
    if s == "error":
        return "error", "error", "F" if age_hours > 24 else "C", _SPARK_DOWN
    if s in ("unknown", ""):
        return "active", "active", None, _SPARK_FLAT
    if age_hours > 48:
        return "attention", "needs attention", "C", _SPARK_FLAT
    return "active", "active", "A", _SPARK_UP


def _humanize_ago(iso_ts: str) -> tuple[str, float]:
    """(human string, age_hours)."""
    if not iso_ts:
        return "never", 9999.0
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return iso_ts, 9999.0
    delta = datetime.now(timezone.utc) - dt
    hours = delta.total_seconds() / 3600
    if hours < 1:
        return f"{int(delta.total_seconds() // 60)} min ago", hours
    if hours < 24:
        return f"{int(hours)}h ago", hours
    days = int(hours // 24)
    return f"{days} day{'s' if days != 1 else ''} ago", hours


def _role_display(pipeline_id: str) -> str:
    overrides = {
        "patrol": "Morning Reports",
        "sales_pipeline": "Sales Pipeline",
        "seo": "SEO",
        "blog": "Blog Posts",
        "gbp": "Google Business",
        "social": "Social Posts",
        "reviews": "Reviews",
        "qbr": "QBR Generator",
        "guard_compliance": "Guard Compliance",
        "harbor_lights": "Harbor Lights Parking",
        "weekly_update": "Weekly Update",
    }
    return overrides.get(pipeline_id, pipeline_id.replace("_", " ").title())


def build(tenant_id: str, owner_name: str = "", tenant_display: str = "") -> dict[str, Any]:
    """
    Compose the full home context. Live fields come from telemetry;
    scaffold rows (narrative, hero stats math, recs) are still TBD in
    Day 3+ and use confident placeholders for now.
    """
    snapshots = telemetry.pipelines_for(tenant_id)
    has_live = bool(snapshots)

    display_name = tenant_display or _display_from_slug(tenant_id)
    initials = _initials(owner_name) if owner_name else _initials(display_name)

    roles = []
    snapshots_by_pid: dict[str, float] = {}
    for snap in snapshots:
        pid = snap["pipeline_id"]
        last_run_text, age_hours = _humanize_ago(snap.get("last_run", ""))
        snapshots_by_pid[pid] = age_hours
        state, state_text, grade, spark = _state_from_status(snap.get("status", ""), age_hours)
        roles.append({
            "slug": pid.replace("_", "-"),
            "name": _role_display(pid),
            "state": state,
            "state_text": state_text,
            "actions": 0,  # Day 3: summarized per-pipeline action count
            "influenced": "0",
            "last_run": last_run_text,
            "grade": grade,
            "spark_path": spark,
        })

    # Attention banner: pick the most urgent errored or overdue role, if any.
    attention = None
    errored = [r for r in roles if r["state"] == "error"]
    if errored:
        attention = {
            "kind": "error",
            "text": f"{errored[0]['name']} is erroring. Open it to see the last log and the fix.",
        }
    else:
        overdue = [r for r in roles if r["state"] == "attention"]
        if overdue:
            attention = {
                "kind": "behind",
                "text": f"{overdue[0]['name']} hasn't run in over 2 days.",
            }

    # Narrative: honest-placeholder until Day 3 wires the Opus-written version.
    if has_live:
        active_count = sum(1 for r in roles if r["state"] == "active")
        narrative = (
            f"Live telemetry from {len(roles)} role{'s' if len(roles) != 1 else ''} "
            f"across your account. {active_count} are running on schedule. "
            "Your weekly recap lands Sunday."
        )
    else:
        narrative = (
            "Your roles are connected and queued for their first run. The "
            "first heartbeat will arrive after the next scheduled execution; "
            "this page will wake up as soon as data flows."
        )

    live_recs = seeded_recs.build(tenant_id, limit=3)

    return {
        "tenant_name": display_name,
        "owner_name": owner_name or "there",
        "owner_initials": initials,
        "today_date": datetime.now().strftime("%Y-%m-%d"),
        "refresh_ago": "just now" if has_live else "waiting",
        "next_refresh": "on the next pipeline run",
        "pinned_roles": _pinned_from_roles(roles, snapshots_by_pid),
        "rail_health": _rail_health(roles),
        "recent_asks": recent_asks.recent(tenant_id, n=3),
        "notifications_count": notifications.count(tenant_id),
        "attention": attention,
        "narrative": narrative,
        "hero_stats": hero_stats.build(tenant_id),
        "roles": roles or _fallback_roles_when_empty(),
        "feed": activity_feed.build(tenant_id),
        "recommendations": live_recs,
        "total_recs": len(live_recs),
    }


def _display_from_slug(slug: str) -> str:
    return slug.replace("_", " ").replace("-", " ").title()


def _initials(name: str) -> str:
    parts = [p for p in (name or "").split() if p]
    if not parts:
        return "WC"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _pinned_from_roles(roles: list[dict[str, Any]], snapshots_by_pid: dict[str, float]) -> list[dict[str, Any]]:
    """Pinned role shortcut list for the sidebar. Each includes its state
    and a `pulse` flag (true if the role ran within the last 60 seconds).

    Day 3+ this becomes user-configurable. Today: pin the first three active.
    snapshots_by_pid maps pipeline_id -> age_hours so we can compute pulse.
    """
    pinned = []
    for r in roles:
        if r["state"] != "active":
            continue
        underlying = r["slug"].replace("-", "_")
        age_h = snapshots_by_pid.get(underlying, 999.0)
        pulse = age_h < (1.0 / 60.0)  # fired within the last 60 seconds
        pinned.append({
            "slug": r["slug"],
            "name": r["name"],
            "active": True,
            "auto": True,
            "state": r["state"],
            "pulse": pulse,
        })
        if len(pinned) >= 3:
            break
    return pinned


def _rail_health(roles: list[dict[str, Any]]) -> dict[str, int]:
    """One-line status-at-a-glance counts for the rail health strip."""
    counts = {"total": 0, "running": 0, "attention": 0, "error": 0, "paused": 0}
    for r in roles:
        counts["total"] += 1
        state = r.get("state", "")
        if state == "active":
            counts["running"] += 1
        elif state == "attention":
            counts["attention"] += 1
        elif state == "error":
            counts["error"] += 1
        elif state == "paused":
            counts["paused"] += 1
    return counts


def _hero_stats_placeholder(n_roles: int) -> list[dict[str, Any]]:
    # Honest placeholders until we have the Day 3 math. Verified tips
    # communicate that the number is not yet computed, not that it is
    # real-but-zero.
    return [
        {"label": "Weeks saved", "value": "--",
         "direction": "up", "delta_text": "calculating",
         "trajectory": "ok", "status_text": "learning",
         "verified_tip": "Baseline capture populates this after the first week of runs",
         "spark_path": _SPARK_FLAT},
        {"label": "Revenue influenced", "value": "--",
         "direction": "up", "delta_text": "calculating",
         "trajectory": "ok", "status_text": "learning",
         "verified_tip": "Traced from Airtable Deals once first-touch attribution data is in",
         "spark_path": _SPARK_FLAT},
        {"label": "Goal progress", "value": "--",
         "direction": "up", "delta_text": "set a goal",
         "trajectory": "ok", "status_text": "no goals yet",
         "verified_tip": "Pin one to three goals during activation and this wakes up",
         "spark_path": _SPARK_FLAT},
    ]


def _fallback_roles_when_empty() -> list[dict[str, Any]]:
    # Brand-voiced empty state: show a single placeholder card that
    # tells the owner exactly what comes next, rather than an empty grid.
    return [{
        "slug": "first-run",
        "name": "First run pending",
        "state": "active",
        "state_text": "queued",
        "actions": 0,
        "influenced": "0",
        "last_run": "waiting on first heartbeat",
        "grade": None,
        "spark_path": _SPARK_FLAT,
    }]
