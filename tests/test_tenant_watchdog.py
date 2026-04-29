"""Tests for dashboard_app.services.tenant_watchdog."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import pytest

from dashboard_app.services import (
    heartbeat_store,
    tenant_automations,
    tenant_schedule,
    tenant_watchdog as tw,
)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tenant_root(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    return tmp_path


def _heartbeat(tenant_id: str, pipeline_id: str, *,
               status: str = "ok",
               last_run: str = "2026-04-29T07:00:00+00:00",
               summary: str = "ok") -> None:
    heartbeat_store.write_snapshot(tenant_id, pipeline_id, {
        "status": status,
        "last_run": last_run,
        "summary": summary,
    })


def _NOW(year=2026, month=4, day=29, hour=12, minute=0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# expected_period_hours / overdue_threshold_hours
# ---------------------------------------------------------------------------


def test_expected_period_every_15_minutes():
    p = tw.expected_period_hours("*/15 * * * *")
    assert 0.2 < p < 0.3  # 0.25h


def test_expected_period_hourly_business_hours():
    # 9 fires per day -> ~2.7h expected period.
    p = tw.expected_period_hours("0 9-17 * * *")
    assert 2.5 < p < 3.0


def test_expected_period_daily():
    p = tw.expected_period_hours("0 8 * * *")
    assert 23.5 < p < 24.5


def test_expected_period_weekly_one_day():
    # Tuesday 10am -> roughly 7 days.
    p = tw.expected_period_hours("0 10 * * 2")
    assert 24 * 6.5 < p < 24 * 7.5


def test_expected_period_invalid_falls_back_to_daily():
    p = tw.expected_period_hours("not a cron")
    assert p == 24.0


def test_overdue_threshold_floor():
    # */15 -> 0.25h * 4 = 1h, above the 0.5h floor.
    assert tw.overdue_threshold_hours("*/15 * * * *") == pytest.approx(1.0, rel=0.01)


def test_overdue_threshold_for_hourly_business():
    t = tw.overdue_threshold_hours("0 9-17 * * *")
    assert t > 8  # ~10.7h


def test_overdue_threshold_for_weekly():
    t = tw.overdue_threshold_hours("0 10 * * 2")
    assert t > 24 * 7  # ~28 days * 4


# ---------------------------------------------------------------------------
# Issue + summarize
# ---------------------------------------------------------------------------


def test_issue_to_dict_round_trip():
    i = tw.Issue(
        tenant_id="acme",
        pipeline_id="reviews",
        kind=tw.ISSUE_ERRORED,
        age_hours=1.5,
        severity="error",
        message="boom",
    )
    d = i.to_dict()
    assert d["tenant_id"] == "acme"
    assert d["kind"] == "errored"


def test_summarize_counts_by_kind(tenant_root):
    results = {
        "acme": [
            tw.Issue("acme", "reviews", tw.ISSUE_ERRORED, 1.0, "x", "error"),
            tw.Issue("acme", "gbp", tw.ISSUE_OVERDUE, 30.0, "x", "warn"),
        ],
        "garcia": [],
        "bobs": [
            tw.Issue("bobs", "seo", tw.ISSUE_MISSING_FIRST_RUN, 50.0, "x", "warn"),
        ],
    }
    counts = tw.summarize(results)
    assert counts["errored"] == 1
    assert counts["overdue"] == 1
    assert counts["missing_first_run"] == 1
    assert counts["tenants_with_issues"] == 2
    assert counts["total"] == 3


# ---------------------------------------------------------------------------
# evaluate_tenant - errored heartbeats
# ---------------------------------------------------------------------------


def test_errored_heartbeat_surfaces(tenant_root):
    tenant_automations.seed_for_tier("acme", "starter")
    _heartbeat("acme", "reviews", status="error", summary="token expired")
    issues = tw.evaluate_tenant("acme", now=_NOW())
    errored = [i for i in issues if i.kind == tw.ISSUE_ERRORED]
    assert len(errored) == 1
    assert errored[0].pipeline_id == "reviews"
    assert errored[0].severity == "error"
    assert "token expired" in errored[0].message


def test_errored_heartbeat_surfaces_even_without_schedule(tenant_root):
    """AP-style: heartbeat-only, no schedule.json - errors must still surface."""
    _heartbeat("americal_patrol", "patrol", status="error", summary="bad data")
    issues = tw.evaluate_tenant("americal_patrol", now=_NOW())
    assert any(i.pipeline_id == "patrol" and i.kind == tw.ISSUE_ERRORED
               for i in issues)


def test_no_errored_when_status_is_ok(tenant_root):
    _heartbeat("acme", "reviews", status="ok")
    issues = tw.evaluate_tenant("acme", now=_NOW())
    assert all(i.kind != tw.ISSUE_ERRORED for i in issues)


# ---------------------------------------------------------------------------
# evaluate_tenant - overdue
# ---------------------------------------------------------------------------


def test_overdue_when_heartbeat_too_old(tenant_root):
    tenant_automations.seed_for_tier("acme", "starter")
    tenant_schedule.seed_for_tier("acme", "starter")
    # Reviews scheduled hourly business hours -> overdue threshold ~10h.
    # Set heartbeat 30 hours old.
    old = (_NOW() - timedelta(hours=30)).isoformat()
    _heartbeat("acme", "reviews", status="ok", last_run=old)
    issues = tw.evaluate_tenant("acme", now=_NOW())
    overdue = [i for i in issues if i.kind == tw.ISSUE_OVERDUE]
    assert any(i.pipeline_id == "reviews" for i in overdue)


def test_not_overdue_when_within_window(tenant_root):
    tenant_schedule.seed_for_tier("acme", "starter")
    fresh = (_NOW() - timedelta(hours=1)).isoformat()
    _heartbeat("acme", "reviews", status="ok", last_run=fresh)
    issues = tw.evaluate_tenant("acme", now=_NOW())
    assert all(not (i.pipeline_id == "reviews" and i.kind == tw.ISSUE_OVERDUE)
               for i in issues)


def test_errored_takes_precedence_over_overdue(tenant_root):
    """An errored pipeline with a stale heartbeat is reported once, as errored."""
    tenant_schedule.seed_for_tier("acme", "starter")
    old = (_NOW() - timedelta(hours=30)).isoformat()
    _heartbeat("acme", "reviews", status="error", last_run=old)
    issues = tw.evaluate_tenant("acme", now=_NOW())
    reviews_issues = [i for i in issues if i.pipeline_id == "reviews"]
    assert len(reviews_issues) == 1
    assert reviews_issues[0].kind == tw.ISSUE_ERRORED


def test_naive_iso_heartbeat_does_not_crash(tenant_root):
    """AP pipelines on the PC write heartbeats without a tz suffix.
    The watchdog must treat them as UTC instead of crashing on
    'can't subtract offset-naive and offset-aware datetimes'.
    """
    tenant_schedule.seed_for_tier("acme", "starter")
    naive = (_NOW() - timedelta(hours=30)).replace(tzinfo=None).isoformat()
    assert "+" not in naive and "Z" not in naive  # confirm naive format
    _heartbeat("acme", "reviews", status="ok", last_run=naive)
    # Must not raise; must still detect the staleness as overdue.
    issues = tw.evaluate_tenant("acme", now=_NOW())
    overdue = [i for i in issues if i.kind == tw.ISSUE_OVERDUE]
    assert any(i.pipeline_id == "reviews" for i in overdue)


# ---------------------------------------------------------------------------
# evaluate_tenant - missing first run
# ---------------------------------------------------------------------------


def test_missing_first_run_after_grace(tenant_root):
    """Enabled + scheduled, no heartbeat, scheduled longer ago than grace."""
    tenant_automations.seed_for_tier("acme", "starter")
    # Manually backdate the schedule entry to before the grace window.
    long_ago = (_NOW() - timedelta(hours=tw.FIRST_RUN_GRACE_HOURS + 5)).isoformat()
    tenant_schedule.set_entry("acme", "reviews", "0 8 * * *", source="tier_default")
    # set_entry stamps a fresh timestamp; backdate it on disk for the test.
    import json
    sched_path = tenant_root / "acme" / "config" / "schedule.json"
    doc = json.loads(sched_path.read_text(encoding="utf-8"))
    for e in doc["entries"]:
        if e["pipeline_id"] == "reviews":
            e["last_modified_at"] = long_ago
    sched_path.write_text(json.dumps(doc), encoding="utf-8")

    issues = tw.evaluate_tenant("acme", now=_NOW())
    missing = [i for i in issues if i.kind == tw.ISSUE_MISSING_FIRST_RUN]
    assert any(i.pipeline_id == "reviews" for i in missing)


def test_within_grace_does_not_flag_missing(tenant_root):
    """Activation-time grace prevents false alarm 10 minutes after activation."""
    tenant_automations.seed_for_tier("acme", "starter")
    tenant_schedule.set_entry("acme", "reviews", "0 8 * * *", source="tier_default")
    issues = tw.evaluate_tenant("acme", now=_NOW())
    assert all(i.kind != tw.ISSUE_MISSING_FIRST_RUN for i in issues)


def test_scheduled_but_not_enabled_is_skipped(tenant_root):
    """Admin scheduled ahead of enable - shouldn't trigger missing_first_run."""
    long_ago = (_NOW() - timedelta(hours=tw.FIRST_RUN_GRACE_HOURS + 5)).isoformat()
    tenant_schedule.set_entry("acme", "reviews", "0 8 * * *", source="admin_added")
    import json
    sched_path = tenant_root / "acme" / "config" / "schedule.json"
    doc = json.loads(sched_path.read_text(encoding="utf-8"))
    for e in doc["entries"]:
        e["last_modified_at"] = long_ago
    sched_path.write_text(json.dumps(doc), encoding="utf-8")

    # Note: no tenant_automations.enable for reviews.
    issues = tw.evaluate_tenant("acme", now=_NOW())
    assert all(i.kind != tw.ISSUE_MISSING_FIRST_RUN for i in issues)


# ---------------------------------------------------------------------------
# tenant discovery
# ---------------------------------------------------------------------------


def test_list_tenants_skips_underscore_directories(tenant_root):
    (tenant_root / "acme").mkdir()
    (tenant_root / "garcia_folklorico").mkdir()
    (tenant_root / "_platform").mkdir()
    (tenant_root / ".cache").mkdir()
    out = tw.list_tenants()
    assert "acme" in out
    assert "garcia_folklorico" in out
    assert "_platform" not in out
    assert ".cache" not in out


def test_list_tenants_empty_when_root_missing(tenant_root, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tenant_root / "no-such-dir"))
    assert tw.list_tenants() == []


# ---------------------------------------------------------------------------
# evaluate_all_tenants
# ---------------------------------------------------------------------------


def test_evaluate_all_tenants_aggregates(tenant_root):
    tenant_automations.seed_for_tier("acme", "starter")
    tenant_automations.seed_for_tier("garcia_folklorico", "pro")
    _heartbeat("acme", "reviews", status="error", summary="x")

    results = tw.evaluate_all_tenants(now=_NOW())
    assert "acme" in results
    assert "garcia_folklorico" in results
    assert any(i.kind == tw.ISSUE_ERRORED for i in results["acme"])


def test_evaluate_all_tenants_continues_when_one_crashes(tenant_root, monkeypatch):
    """A bad tenant must not poison the whole digest."""
    tenant_automations.seed_for_tier("acme", "starter")
    _heartbeat("acme", "reviews", status="ok")

    real_evaluate = tw.evaluate_tenant

    def maybe_boom(tenant_id, **kwargs):
        if tenant_id == "broken":
            raise RuntimeError("boom")
        return real_evaluate(tenant_id, **kwargs)

    monkeypatch.setattr(tw, "evaluate_tenant", maybe_boom)
    results = tw.evaluate_all_tenants(now=_NOW(), tenants=["acme", "broken"])
    assert "acme" in results
    assert results["broken"] == []
