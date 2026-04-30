"""
Hero stats: real math when the telemetry is there, honest placeholders when it isn't.

Weeks Saved
    Derived from successful heartbeat runs across all pipelines multiplied
    by the minutes-per-run savings estimate, divided by 40-hour weeks. The
    minutes table is intentionally editable in TIME_SAVED_MINUTES so we can
    tune post-hackathon without a code change. When there are zero heartbeats
    we return "--" with a verified-tip that says "Baseline capture populates
    this after the first week of runs."

Revenue Influenced
    Honest "connects after the first deal attribution" until we wire the
    Airtable Deals table in Track 2. DO NOT fabricate.

Goal Progress
    "Set a goal" until goals.json exists and has a pinned goal. Once pinned,
    we compute percent-to-target from a simple current/target ratio.
"""

from __future__ import annotations

import json
from typing import Any

from . import heartbeat_store, tenant_automations


# Per-successful-run savings minutes. Intentionally conservative. Tunable via
# env var per tenant later; hardcoded today so Weeks Saved is deterministic.
TIME_SAVED_MINUTES = {
    "patrol": 10,
    "morning_reports": 10,
    "seo": 90,
    "blog": 120,
    "sales_pipeline": 4,   # per touch
    "reviews": 3,          # per reply
    "social": 30,          # per post
    "ads": 15,             # per optimization cycle
    "chat_widget": 2,      # per conversation
    "gbp": 12,
    "client_reports": 20,
    "supervisor_reports": 25,
    "incident_alerts": 5,
    "watchdog": 0,         # infra heartbeat, no owner time saved
}
DEFAULT_MINUTES_PER_RUN = 15


# Cold-start projection: how many minutes per week each pipeline is expected
# to save once running. Different from TIME_SAVED_MINUTES (per-run) because
# high-frequency pipelines like chat_widget heartbeat shouldn't multiply
# minute-savings by tick count. These are conservative weekly estimates that
# render as the "expected" Weeks Saved value before the first real heartbeat.
EXPECTED_WEEKLY_MINUTES = {
    "patrol": 50,
    "morning_reports": 50,
    "seo": 90,
    "blog": 120,
    "sales_pipeline": 30,
    "reviews": 15,
    "social": 60,
    "ads": 30,
    "chat_widget": 10,
    "gbp": 20,
    "email_assistant": 60,
    "voice_ai": 90,
    "seo_recs": 30,
    "review_engine": 20,
    "win_back": 25,
}
DEFAULT_EXPECTED_WEEKLY_MINUTES = 30


def _minutes_for(pid: str) -> int:
    return TIME_SAVED_MINUTES.get(pid, DEFAULT_MINUTES_PER_RUN)


_SPARK_UP = "M0,22 L15,18 L30,20 L45,14 L60,16 L75,10 L90,12 L105,7 L120,9 L135,5 L150,7 L165,3 L180,5 L200,2"
_SPARK_FLAT = "M0,14 L25,13 L50,14 L75,13 L100,14 L125,13 L150,14 L175,13 L200,14"


def _expected_weekly_minutes(tenant_id: str) -> tuple[float, list[str]]:
    """Project minutes-saved-per-week from enabled automations alone (no
    heartbeats required). Returns (minutes, enabled_ids). Returns (0, [])
    when no automations are enabled, which preserves the "--" placeholder
    for tenants who haven't seeded yet."""
    try:
        enabled = tenant_automations.enabled_ids(tenant_id)
    except Exception:  # noqa: BLE001 - never crash the home render
        return 0.0, []
    if not enabled:
        return 0.0, []
    total = 0.0
    for pid in enabled:
        total += float(EXPECTED_WEEKLY_MINUTES.get(pid, DEFAULT_EXPECTED_WEEKLY_MINUTES))
    return total, list(enabled)


def _weeks_saved(tenant_id: str) -> tuple[str, str, dict[str, Any]]:
    """Return (value_str, delta_text, meta) for the Weeks Saved card.

    Branches:
      - Has successful heartbeats: show real cumulative weeks-saved.
      - No heartbeats but enabled automations: project from
        EXPECTED_WEEKLY_MINUTES so the card surfaces a confident number
        instead of "--" / "calculating" before the first run lands.
      - Neither: honest "--" placeholder.
    """
    try:
        snaps = heartbeat_store.read_all(tenant_id)
    except heartbeat_store.HeartbeatError:
        snaps = []

    total_runs = 0
    total_minutes = 0.0
    contributors: list[str] = []
    for snap in snaps:
        pid = snap.get("pipeline_id", "")
        payload = snap.get("payload") or {}
        status = (payload.get("status") or "").lower()
        if status != "ok":
            continue
        run_count = int(payload.get("run_count") or 1)
        mins = _minutes_for(pid) * run_count
        if mins <= 0:
            continue
        total_runs += run_count
        total_minutes += mins
        contributors.append(pid)

    if total_minutes > 0:
        weeks = total_minutes / 60.0 / 40.0
        if weeks < 0.1:
            value = f"{total_minutes/60:.1f}h"
        elif weeks < 1:
            value = f"{weeks:.1f}w"
        else:
            value = f"{weeks:.1f}"
        delta = f"across {total_runs} automated actions"
        return value, delta, {
            "run_count": total_runs,
            "contributors": sorted(set(contributors)),
            "projected": False,
        }

    expected_min, enabled_ids = _expected_weekly_minutes(tenant_id)
    if expected_min > 0:
        hours = expected_min / 60.0
        value = f"~{hours:.1f}h"
        delta = (
            f"projected once your {len(enabled_ids)} role"
            f"{'s' if len(enabled_ids) != 1 else ''} run weekly"
        )
        return value, delta, {
            "run_count": 0,
            "contributors": [],
            "projected": True,
            "enabled_count": len(enabled_ids),
            "weekly_minutes": expected_min,
        }

    return "--", "calculating", {"run_count": 0, "projected": False}


def _goal_progress(tenant_id: str) -> tuple[str, str, str]:
    """Return (value_str, delta_text, status_text) from goals.json."""
    try:
        root = heartbeat_store.tenant_root(tenant_id)
    except heartbeat_store.HeartbeatError:
        return "--", "set a goal", "no goals yet"
    goals_path = root / "goals.json"
    if not goals_path.exists():
        return "--", "set a goal", "no goals yet"
    try:
        data = json.loads(goals_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "--", "set a goal", "no goals yet"
    goals = data.get("goals") or []
    if not goals:
        return "--", "set a goal", "no goals yet"

    first = goals[0]
    current = float(first.get("current") or 0)
    target = float(first.get("target") or 0)
    if target <= 0:
        return "--", "waiting on target", "learning"
    pct = max(0.0, min(100.0, (current / target) * 100.0))
    if pct >= 75:
        status = "on track"
    elif pct >= 40:
        status = "trending up"
    else:
        status = "behind"
    return f"{int(pct)}%", f"{int(current)} of {int(target)}", status


def build(tenant_id: str) -> list[dict[str, Any]]:
    """Return the three hero-stat cards. Stable shape for the home template."""
    weeks_val, weeks_delta, weeks_meta = _weeks_saved(tenant_id)
    goal_val, goal_delta, goal_status = _goal_progress(tenant_id)

    if weeks_meta.get("projected"):
        weeks_status = "projected"
        weeks_tip = (
            f"Projection from {weeks_meta.get('enabled_count', 0)} enabled "
            "roles. Real number lands after your first heartbeats arrive."
        )
        weeks_spark = _SPARK_UP
    elif weeks_val != "--":
        weeks_status = "on track"
        weeks_tip = (
            f"Derived from {weeks_meta['run_count']} automated actions across your roles"
        )
        weeks_spark = _SPARK_UP
    else:
        weeks_status = "learning"
        weeks_tip = "Populates after your first automated run lands."
        weeks_spark = _SPARK_FLAT

    cards: list[dict[str, Any]] = [
        {
            "label": "Weeks saved",
            "value": weeks_val,
            "direction": "up",
            "delta_text": weeks_delta,
            "trajectory": "ok",
            "status_text": weeks_status,
            "verified_tip": weeks_tip,
            "spark_path": weeks_spark,
        },
        {
            "label": "Revenue influenced",
            "value": "--",
            "direction": "up",
            "delta_text": "first-touch attribution wiring next",
            "trajectory": "ok",
            "status_text": "learning",
            "verified_tip": "Traced from Airtable Deals once first-touch attribution is enabled.",
            "spark_path": _SPARK_FLAT,
        },
        {
            "label": "Goal progress",
            "value": goal_val,
            "direction": "up",
            "delta_text": goal_delta,
            "trajectory": "ok",
            "status_text": goal_status,
            "verified_tip": (
                "Measured against the first pinned goal in your goals file."
                if goal_val != "--"
                else "Pin one to three goals and this wakes up."
            ),
            "spark_path": _SPARK_UP if goal_val != "--" else _SPARK_FLAT,
        },
    ]
    return cards
