"""Tests for the W7 tier-default seeder wired into mark_activation_complete."""

from __future__ import annotations

import os
from typing import Any

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import pytest

from dashboard_app.services import (
    activation_tools,
    automation_catalog as cat,
    handoff,
    tenant_automations,
    tenant_schedule,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def tenant_root(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def _no_email(monkeypatch):
    """Default: every test stubs send_handoff so SMTP is never reached."""
    monkeypatch.setattr(handoff, "send_handoff", lambda **_kw: True)


# ---------------------------------------------------------------------------
# happy paths
# ---------------------------------------------------------------------------


def test_starter_tier_seeds_starter_defaults(tenant_root):
    ok, payload = activation_tools.dispatch(
        "acme",
        "mark_activation_complete",
        {"tier": "starter"},
    )
    assert ok is True
    assert payload["status"] == "activated"
    assert payload["tier"] == "starter"
    assert payload["tier_default_count"] > 0
    enabled = set(tenant_automations.enabled_ids("acme"))
    expected = {a.id for a in cat.for_tier("starter")}
    assert enabled == expected


def test_pro_tier_includes_seo_recs(tenant_root):
    """Garcia signed for Pro - their automations.json must include seo_recs."""
    ok, payload = activation_tools.dispatch(
        "garcia_folklorico",
        "mark_activation_complete",
        {"tier": "pro"},
    )
    assert ok is True
    assert "seo_recs" in tenant_automations.enabled_ids("garcia_folklorico")
    assert tenant_automations.get_tier("garcia_folklorico") == "pro"


def test_ultra_tier_records_ultra(tenant_root):
    activation_tools.dispatch(
        "fancy_co",
        "mark_activation_complete",
        {"tier": "ultra"},
    )
    assert tenant_automations.get_tier("fancy_co") == "ultra"


def test_no_tier_supplied_skips_seeding(tenant_root):
    ok, payload = activation_tools.dispatch(
        "acme",
        "mark_activation_complete",
        {},
    )
    assert ok is True
    assert payload["tier"] is None
    assert payload["tier_default_count"] == 0
    assert tenant_automations.enabled_ids("acme") == []


# ---------------------------------------------------------------------------
# AP-only items don't leak into non-AP tenants
# ---------------------------------------------------------------------------


def test_seed_excludes_ap_only_automations(tenant_root):
    activation_tools.dispatch(
        "garcia_folklorico",
        "mark_activation_complete",
        {"tier": "ultra"},
    )
    enabled = tenant_automations.enabled_ids("garcia_folklorico")
    assert "daily_reports" not in enabled
    assert "guard_compliance" not in enabled
    assert "incident_trends" not in enabled


# ---------------------------------------------------------------------------
# idempotency
# ---------------------------------------------------------------------------


def test_repeated_completion_does_not_duplicate_entries(tenant_root):
    activation_tools.dispatch("acme", "mark_activation_complete", {"tier": "starter"})
    enabled1 = tenant_automations.enabled_ids("acme")
    activation_tools.dispatch("acme", "mark_activation_complete", {"tier": "starter"})
    enabled2 = tenant_automations.enabled_ids("acme")
    assert enabled1 == enabled2


def test_admin_added_entries_preserved_across_repeated_completion(tenant_root):
    activation_tools.dispatch("acme", "mark_activation_complete", {"tier": "starter"})
    tenant_automations.enable("acme", "google_ads_manager", source="admin_added")
    activation_tools.dispatch("acme", "mark_activation_complete", {"tier": "starter"})
    assert "google_ads_manager" in tenant_automations.enabled_ids("acme")


# ---------------------------------------------------------------------------
# error handling
# ---------------------------------------------------------------------------


def test_unknown_tier_is_reported_but_does_not_block_activation(tenant_root):
    ok, payload = activation_tools.dispatch(
        "acme",
        "mark_activation_complete",
        {"tier": "enterprise"},
    )
    assert ok is True
    assert payload["status"] == "activated"
    assert payload["tier_seed_error"] is not None
    assert "enterprise" in payload["tier_seed_error"]
    assert payload["tier_default_count"] == 0


# ---------------------------------------------------------------------------
# handoff letter sees the seeded tier
# ---------------------------------------------------------------------------


def test_schedule_seeded_alongside_automations(tenant_root):
    """When tier is supplied, BOTH automations.json and schedule.json get
    seeded. The dispatcher will need both."""
    ok, payload = activation_tools.dispatch(
        "garcia_folklorico",
        "mark_activation_complete",
        {"tier": "pro"},
    )
    assert ok is True
    assert payload["schedule_default_count"] > 0
    schedule_entries = tenant_schedule.list_entries("garcia_folklorico")
    assert any(e["pipeline_id"] == "seo_recs" for e in schedule_entries)
    # Cron strings must be valid for every seeded entry.
    for e in schedule_entries:
        assert tenant_schedule.is_valid_cron(e["cron"])


def test_schedule_seed_idempotent_across_completions(tenant_root):
    activation_tools.dispatch("acme", "mark_activation_complete", {"tier": "starter"})
    tenant_schedule.set_entry("acme", "reviews", "0 7 * * *", source="owner_change")
    activation_tools.dispatch("acme", "mark_activation_complete", {"tier": "starter"})
    reviews = tenant_schedule.get_entry("acme", "reviews")
    # Owner's cron change survives re-completion.
    assert reviews["cron"] == "0 7 * * *"


def test_handoff_sees_seeded_tier_when_completing(tenant_root, monkeypatch):
    """The letter must list the actual tier_default automations - so seeding
    has to happen BEFORE the handoff render. This pins that ordering."""
    captured: dict[str, Any] = {}

    def fake_send(*, tenant_id: str, **kwargs):
        # The letter is rendered inside send_handoff via handoff.render(),
        # which reads tenant_automations. Snapshot what's enabled at the
        # moment of send to prove the seeder ran first.
        captured["enabled_at_send"] = list(tenant_automations.enabled_ids(tenant_id))
        return True

    monkeypatch.setattr(handoff, "send_handoff", fake_send)
    ok, payload = activation_tools.dispatch(
        "garcia_folklorico",
        "mark_activation_complete",
        {
            "tier": "pro",
            "owner_name": "Itzel Garcia",
            "owner_email": "itzel@example.com",
        },
    )
    assert ok is True
    assert payload["handoff_sent"] is True
    assert "seo_recs" in captured["enabled_at_send"]
