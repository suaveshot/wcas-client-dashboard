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

from . import (
    activity_feed,
    automation_catalog,
    hero_stats,
    notifications,
    rec_actions,
    recent_asks,
    recs_store,
    sample_outputs,
    seeded_recs,
    telemetry,
    tenant_automations,
    tenant_kb,
    tenant_schedule,
    voice_card,
)


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

    enabled_ids = tenant_automations.enabled_ids(tenant_id)
    snapshots_by_pid: dict[str, dict[str, Any]] = {s["pipeline_id"]: s for s in snapshots}
    age_by_pid: dict[str, float] = {}

    roles: list[dict[str, Any]] = []
    rendered: set[str] = set()

    if enabled_ids:
        # New path: catalog drives the ring set. Heartbeats fill in state.
        for aid in enabled_ids:
            entry = automation_catalog.get(aid)
            if entry is None:
                continue
            snap = snapshots_by_pid.get(aid)
            if snap is not None:
                last_run_text, age_hours = _humanize_ago(snap.get("last_run", ""))
                age_by_pid[aid] = age_hours
                state, state_text, grade, spark = _state_from_status(
                    snap.get("status", ""), age_hours
                )
            else:
                age_by_pid[aid] = 0.0
                next_run_label = _next_run_label_for(tenant_id, aid)
                last_run_text = next_run_label or "queued"
                state = "pending"
                state_text = (
                    f"first run {next_run_label}"
                    if next_run_label
                    else "pending first run"
                )
                grade = None
                spark = _SPARK_FLAT
            roles.append({
                "slug": aid.replace("_", "-"),
                "name": entry.name,
                "state": state,
                "state_text": state_text,
                "actions": 0,
                "influenced": "0",
                "last_run": last_run_text,
                "grade": grade,
                "spark_path": spark,
            })
            rendered.add(aid)

    # Backward-compat path for tenants without an automations.json
    # (e.g. AP today): render every heartbeat we got, even if it's not
    # in the catalog yet. Also catches any heartbeats from pipelines
    # that aren't in the enabled list (so admin debugging stays visible).
    for snap in snapshots:
        pid = snap["pipeline_id"]
        if pid in rendered:
            continue
        last_run_text, age_hours = _humanize_ago(snap.get("last_run", ""))
        age_by_pid[pid] = age_hours
        state, state_text, grade, spark = _state_from_status(snap.get("status", ""), age_hours)
        catalog_entry = automation_catalog.get(pid)
        roles.append({
            "slug": pid.replace("_", "-"),
            "name": catalog_entry.name if catalog_entry else _role_display(pid),
            "state": state,
            "state_text": state_text,
            "actions": 0,
            "influenced": "0",
            "last_run": last_run_text,
            "grade": grade,
            "spark_path": spark,
        })

    snapshots_by_pid_age = age_by_pid  # rename for downstream code that expected the older variable

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
        # Cold-start narrative. Lead with "Your roles are connected" so the
        # roles-pending placeholder pin test stays valid, then add a relief
        # frame (per feedback_wcas_relief_framing). We extend the owner, we
        # do not replace them (per feedback_wcas_extension_not_replacement).
        pending_count = len(rendered) or len(roles)
        if pending_count:
            narrative = (
                "Your roles are connected and queued for their first run. The "
                f"work piling up across {pending_count} role"
                f"{'s' if pending_count != 1 else ''} is ready to land in your "
                "voice, with your last word on every send."
            )
        else:
            narrative = (
                "Your roles are connected and queued for their first run. The "
                "first heartbeat will arrive after the next scheduled execution; "
                "this page will wake up as soon as data flows."
            )

    # Prefer the most recent Opus refresh when fresh; degrade to seeded recs
    # so the surface is never blank for cold-start tenants.
    fresh = recs_store.read_latest(tenant_id)
    if recs_store.is_fresh(fresh):
        live_recs = [r for r in (fresh.get("recs") or []) if not r.get("draft")]
    else:
        live_recs = seeded_recs.build(tenant_id, limit=12)
    live_recs = rec_actions.filter_unacted(tenant_id, live_recs)[:3]

    is_cold_start = not has_live

    ctx = {
        "tenant_name": display_name,
        "owner_name": owner_name or "there",
        "owner_initials": initials,
        "today_date": datetime.now().strftime("%Y-%m-%d"),
        "refresh_ago": "just now" if has_live else "waiting",
        "next_refresh": "on the next pipeline run",
        "pinned_roles": _pinned_from_roles(roles, snapshots_by_pid_age),
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
        # Cold-start surfaces. Each is empty/None when its source isn't
        # populated, so the template can use {% if x %} to decide whether
        # to render the section without crashing on missing data.
        "is_cold_start": is_cold_start,
        "activation_samples": _activation_samples_summary(tenant_id),
        "this_week_timeline": _this_week_timeline(tenant_id, enabled_ids),
        "voice_teaser": _voice_teaser(tenant_id),
        "kb_summary": _kb_summary(tenant_id),
    }
    return _maybe_demo_mode(ctx)


def _next_run_label_for(tenant_id: str, pipeline_id: str) -> str:
    """Look up the cron for a pending automation (preferring its actual
    schedule entry, falling back to the catalog default) and humanize it
    for the cold-start ring's state_text."""
    entry = tenant_schedule.get_entry(tenant_id, pipeline_id)
    cron = ""
    if entry and entry.get("enabled", True):
        c = entry.get("cron")
        if isinstance(c, str) and c.strip():
            cron = c
    if not cron:
        cron = tenant_schedule.default_cron_for(pipeline_id)
    return tenant_schedule.humanize_cron(cron)


def _activation_samples_summary(tenant_id: str) -> list[dict[str, Any]]:
    """Lightweight summary of cached activation samples for the cold-start
    carousel. We strip body_markdown so the home context stays small; the
    detail panel fetches the full sample on demand."""
    try:
        samples = sample_outputs.list_samples(tenant_id)
    except Exception:  # noqa: BLE001 - never break home render
        return []
    out: list[dict[str, Any]] = []
    for s in samples:
        if not isinstance(s, dict):
            continue
        out.append({
            "slug": s.get("slug"),
            "title": s.get("title") or "",
            "preview": s.get("preview") or "",
            "status": s.get("status") or "ok",
            "generated_at": s.get("generated_at"),
        })
    return out


def _this_week_timeline(
    tenant_id: str,
    enabled_ids: list[str],
) -> list[dict[str, Any]]:
    """Render a cadence summary of what will land this week, derived from
    schedule.json (or default crons when no entry exists yet). Returns at
    most one row per enabled automation."""
    if not enabled_ids:
        return []
    by_pid: dict[str, str] = {}
    for entry in tenant_schedule.list_entries(tenant_id, enabled_only=True):
        pid = entry.get("pipeline_id")
        cron = entry.get("cron")
        if isinstance(pid, str) and isinstance(cron, str):
            by_pid[pid] = cron
    rows: list[dict[str, Any]] = []
    for aid in enabled_ids:
        catalog_entry = automation_catalog.get(aid)
        if catalog_entry is None:
            continue
        cron = by_pid.get(aid) or tenant_schedule.default_cron_for(aid)
        when_label = tenant_schedule.humanize_cron(cron)
        if not when_label:
            continue
        rows.append({
            "pipeline_id": aid,
            "pipeline_name": catalog_entry.name,
            "when_label": when_label,
            "cron": cron,
        })
    return rows


def _voice_teaser(tenant_id: str) -> dict[str, Any] | None:
    """Voice card teaser for the cold-start narrative band. Returns None
    when no voice card has been captured during activation yet."""
    try:
        card = voice_card.load(tenant_id)
    except Exception:  # noqa: BLE001
        return None
    if not card:
        return None
    traits = card.get("traits") or []
    if not isinstance(traits, list):
        traits = []
    cleaned = [str(t).strip() for t in traits if t]
    voice_sample = str(card.get("voice_sample") or "").strip()
    if not cleaned and not voice_sample:
        return None
    return {
        "traits": cleaned[:3],
        "voice_sample": voice_sample[:240],
    }


def _kb_summary(tenant_id: str) -> str | None:
    """First two lines of kb/company.md as a cold-start callout. Returns
    None when no company section exists."""
    try:
        company = tenant_kb.read_section(tenant_id, "company")
    except Exception:  # noqa: BLE001
        return None
    if not company:
        return None
    lines = [line.strip() for line in company.splitlines() if line.strip()]
    # Skip a leading markdown heading if present.
    body = [line for line in lines if not line.startswith("#")]
    if not body:
        return None
    excerpt = " ".join(body[:2])
    return excerpt[:260]


def _maybe_demo_mode(ctx: dict[str, Any]) -> dict[str, Any]:
    """If DEMO_MODE=true, run the rendered context through the sanitizer.

    Kept local so home_context has no hard dependency on the scripts/ tree
    when the env flag is off.
    """
    import os
    if os.getenv("DEMO_MODE", "false").lower() != "true":
        return ctx
    try:
        from scripts import sanitize_for_demo  # type: ignore
    except ImportError:
        return ctx
    try:
        return sanitize_for_demo.apply_to_context(ctx)
    except Exception:
        return ctx


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
