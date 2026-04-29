"""Per-tenant watchdog evaluator.

Reads each tenant's schedule.json + heartbeat snapshots and returns a
flat list of issues - overdue pipelines, errored pipelines, and
pipelines that haven't run yet despite being scheduled. The dashboard
admin surface and Sam's daily digest both consume this.

Why this exists: AP's existing watchdog only watches AP pipelines on
Sam's PC + on AP's VPS instance. Multi-tenant WCAS pipelines aren't
monitored anywhere - if Garcia's reviews automation silently dies,
nobody notices until a client complains. The 2026-04-23 Kyle
Vestermark regression was the wake-up call: a broken pipeline ran
silently for 10 days because the digest counted drafts, not health.

Design:
  * Pure read-only - never mutates schedule, automations, or heartbeats.
  * Stateless - re-running on the same inputs always produces the same
    output, so Sam can run it on demand AND on a cron without
    coordinating state between runs.
  * Cron-aware staleness: a pipeline scheduled every 15 min is overdue
    after 60 minutes of silence; a weekly one is overdue at 9 days.
  * No alerting in this layer. The runner that calls evaluate_*
    decides how to surface (email, dashboard banner, etc.).
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import (
    automation_catalog,
    heartbeat_store,
    tenant_automations,
    tenant_schedule,
    telemetry,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Issue model
# ---------------------------------------------------------------------------


# Watchdog issue kinds. Stable strings so the dashboard + digest can
# render different copy per kind without re-parsing free-form messages.
ISSUE_OVERDUE = "overdue"
ISSUE_ERRORED = "errored"
ISSUE_MISSING_FIRST_RUN = "missing_first_run"

VALID_ISSUE_KINDS = frozenset({ISSUE_OVERDUE, ISSUE_ERRORED, ISSUE_MISSING_FIRST_RUN})


@dataclass(frozen=True)
class Issue:
    tenant_id: str
    pipeline_id: str
    kind: str
    age_hours: float | None
    message: str
    severity: str  # "warn" | "error" | "info"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# cron -> expected staleness window
# ---------------------------------------------------------------------------


# A pipeline is overdue if no heartbeat has landed within
# expected_period * OVERDUE_MULTIPLIER. 4x gives Cron skew + retry
# headroom without firing on every minor late-by-one delay.
OVERDUE_MULTIPLIER = 4

# Floor on every cadence so a once-per-15-minutes pipeline isn't
# flagged after 16 minutes of silence (random network jitter).
MIN_OVERDUE_HOURS = 0.5

# Newly-scheduled pipelines get this much grace before "missing first
# run" fires. Avoids false alarms on a tenant 10 minutes after activation.
FIRST_RUN_GRACE_HOURS = 24


def _parse_int_set(field: str, lo: int, hi: int) -> set[int]:
    """Expand a single cron field into the concrete set of integers it
    matches. Raises ValueError on malformed input - callers are expected
    to have validated via tenant_schedule.is_valid_cron first."""
    values: set[int] = set()
    for piece in field.split(","):
        head, _, step_s = piece.partition("/")
        step = int(step_s) if step_s else 1
        if head == "*":
            values.update(range(lo, hi + 1, step))
            continue
        if "-" in head:
            a_s, b_s = head.split("-", 1)
            a, b = int(a_s), int(b_s)
            values.update(range(a, b + 1, step))
        else:
            values.add(int(head))
    return values


def expected_period_hours(cron_expr: str) -> float:
    """Estimate how many hours typically pass between firings of `cron_expr`.

    This is a lightweight heuristic, not a full cron simulator: we count
    how many distinct minute-buckets per day the schedule fires, then
    derive the average gap. Good enough to bucket "every 5 min" vs
    "every Tuesday" vs "Mon at 9am only", which is all the watchdog
    needs to choose a staleness threshold.
    """
    if not tenant_schedule.is_valid_cron(cron_expr):
        # Fall back to "daily" so we at least flag if something stops
        # entirely.  Better than refusing to evaluate.
        return 24.0
    minute, hour, dom, month, dow = cron_expr.strip().split()
    minutes = _parse_int_set(minute, 0, 59)
    hours = _parse_int_set(hour, 0, 23)
    if not minutes or not hours:
        return 24.0
    fires_per_day = len(minutes) * len(hours)
    # day-of-week filter cuts daily fires by week_factor.
    # day-of-month "*" means every day; specific dom set scales by len/30.
    if dow.strip() != "*":
        dows = _parse_int_set(dow, 0, 6)
        if not dows:
            return 24.0
        week_factor = len(dows) / 7
    else:
        week_factor = 1.0
    if dom.strip() != "*":
        doms = _parse_int_set(dom, 1, 31)
        if not doms:
            return 24.0
        month_factor = len(doms) / 30
    else:
        month_factor = 1.0
    if month.strip() != "*":
        months = _parse_int_set(month, 1, 12)
        if not months:
            return 24.0
        year_factor = len(months) / 12
    else:
        year_factor = 1.0
    fires_per_day_avg = fires_per_day * week_factor * month_factor * year_factor
    if fires_per_day_avg <= 0:
        return 24.0 * 7  # ridiculously sparse; treat as weekly
    return 24.0 / fires_per_day_avg


def overdue_threshold_hours(cron_expr: str) -> float:
    return max(MIN_OVERDUE_HOURS, expected_period_hours(cron_expr) * OVERDUE_MULTIPLIER)


# ---------------------------------------------------------------------------
# tenant discovery
# ---------------------------------------------------------------------------


def _tenant_root_dir() -> Path:
    return Path(os.environ.get("TENANT_ROOT") or "/opt/wc-solns")


def list_tenants() -> list[str]:
    """Every tenant directory present under TENANT_ROOT (excluding the
    `_platform` sibling reserved for Pattern C credentials)."""
    root = _tenant_root_dir()
    if not root.exists():
        return []
    out: list[str] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        name = entry.name
        if name.startswith("_") or name.startswith("."):
            continue
        out.append(name)
    return out


# ---------------------------------------------------------------------------
# evaluation
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        # Tolerate both "...Z" and "...+00:00" suffixes.
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    # AP pipelines on the PC write heartbeats without a tz suffix; treat
    # any naive datetime as UTC so _hours_since does not raise
    # "can't subtract offset-naive and offset-aware datetimes" downstream.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _hours_since(when: datetime, now: datetime) -> float:
    return max(0.0, (now - when).total_seconds() / 3600.0)


def _heartbeats_by_pid(tenant_id: str) -> dict[str, dict[str, Any]]:
    return {h["pipeline_id"]: h for h in telemetry.pipelines_for(tenant_id)}


def evaluate_tenant(
    tenant_id: str,
    *,
    now: datetime | None = None,
) -> list[Issue]:
    """Return every health issue currently visible for one tenant.

    Sources:
      * tenant_schedule.list_entries (enabled scheduled pipelines)
      * tenant_automations.enabled_ids (enabled but maybe unscheduled)
      * telemetry.pipelines_for (most recent heartbeat per pipeline)

    Order of issues:
      1. errored heartbeats (immediate)
      2. overdue (scheduled pipeline that hasn't pinged in N * cadence)
      3. missing_first_run (enabled, never pinged, past activation grace)
    """
    now_dt = now or _now()
    issues: list[Issue] = []

    schedules = {
        e["pipeline_id"]: e
        for e in tenant_schedule.list_entries(tenant_id, enabled_only=True)
    }
    heartbeats = _heartbeats_by_pid(tenant_id)
    enabled_ids = set(tenant_automations.enabled_ids(tenant_id))

    # 1. errored heartbeats. We surface these even if the pipeline isn't
    # in the schedule (admin debugging, AP-style heartbeat-only pipelines).
    for pid, hb in heartbeats.items():
        status = (hb.get("status") or "").lower()
        if status != "error":
            continue
        last_run = _parse_iso(hb.get("last_run", "") or hb.get("received_at", ""))
        age = _hours_since(last_run, now_dt) if last_run else None
        issues.append(Issue(
            tenant_id=tenant_id,
            pipeline_id=pid,
            kind=ISSUE_ERRORED,
            age_hours=age,
            severity="error",
            message=(hb.get("summary") or "pipeline reported error").strip()[:200],
        ))

    # 2. overdue. A scheduled pipeline that DID run before but hasn't pinged
    # within the cadence-derived window.
    for pid, entry in schedules.items():
        hb = heartbeats.get(pid)
        if hb is None:
            continue  # caught by case 3 below
        if (hb.get("status") or "").lower() == "error":
            continue  # already flagged in case 1
        last_run = _parse_iso(hb.get("last_run", "") or hb.get("received_at", ""))
        if last_run is None:
            continue
        age = _hours_since(last_run, now_dt)
        threshold = overdue_threshold_hours(entry.get("cron", ""))
        if age > threshold:
            issues.append(Issue(
                tenant_id=tenant_id,
                pipeline_id=pid,
                kind=ISSUE_OVERDUE,
                age_hours=age,
                severity="warn",
                message=(
                    f"no heartbeat in {age:.1f}h "
                    f"(expected within {threshold:.1f}h for cron '{entry['cron']}')"
                ),
            ))

    # 3. missing first run. Enabled in the catalog AND scheduled, no
    # heartbeat ever, past the activation grace period. We use the
    # schedule entry's last_modified_at as the activation marker - a
    # tier_default seed stamps that to the activation time.
    for pid, entry in schedules.items():
        if pid in heartbeats:
            continue
        if pid not in enabled_ids:
            # Scheduled but not in the enabled list - admin probably
            # scheduled it ahead of enabling. Skip to avoid noise.
            continue
        scheduled_at = _parse_iso(entry.get("last_modified_at", ""))
        if scheduled_at is None:
            continue
        age = _hours_since(scheduled_at, now_dt)
        if age < FIRST_RUN_GRACE_HOURS:
            continue
        issues.append(Issue(
            tenant_id=tenant_id,
            pipeline_id=pid,
            kind=ISSUE_MISSING_FIRST_RUN,
            age_hours=age,
            severity="warn",
            message=(
                f"scheduled {age:.1f}h ago, never pinged. "
                f"Pipeline likely failed to deploy or activate."
            ),
        ))

    return issues


def evaluate_all_tenants(
    *,
    now: datetime | None = None,
    tenants: Iterable[str] | None = None,
) -> dict[str, list[Issue]]:
    """Run evaluate_tenant across every tenant. Skips tenants whose
    discovery raises (e.g. missing config dir) so a single bad tenant
    can't kill the digest."""
    target = list(tenants) if tenants is not None else list_tenants()
    out: dict[str, list[Issue]] = {}
    for tid in target:
        try:
            out[tid] = evaluate_tenant(tid, now=now)
        except Exception as exc:
            log.warning("watchdog: evaluate_tenant(%s) crashed: %s", tid, exc)
            out[tid] = []
    return out


def summarize(results: dict[str, list[Issue]]) -> dict[str, int]:
    """Roll a results map into a flat counts-by-kind dict for the digest."""
    counts = {k: 0 for k in VALID_ISSUE_KINDS}
    counts["tenants_with_issues"] = 0
    counts["total"] = 0
    for issues in results.values():
        if issues:
            counts["tenants_with_issues"] += 1
        for i in issues:
            counts["total"] += 1
            counts[i.kind] = counts.get(i.kind, 0) + 1
    return counts


__all__ = [
    "FIRST_RUN_GRACE_HOURS",
    "ISSUE_ERRORED",
    "ISSUE_MISSING_FIRST_RUN",
    "ISSUE_OVERDUE",
    "Issue",
    "MIN_OVERDUE_HOURS",
    "OVERDUE_MULTIPLIER",
    "VALID_ISSUE_KINDS",
    "evaluate_all_tenants",
    "evaluate_tenant",
    "expected_period_hours",
    "list_tenants",
    "overdue_threshold_hours",
    "summarize",
]
