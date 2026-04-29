"""Tests for dashboard_app.services.automation_catalog."""

from __future__ import annotations

import os

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import pytest

from dashboard_app.services import automation_catalog as cat


# ---------------------------------------------------------------------------
# registry shape
# ---------------------------------------------------------------------------


def test_catalog_has_all_22_audit_systems_plus_seo_recs():
    """The plan's per-system audit lists 22 systems; W5.5 added seo_recs
    as a distinct entry from the broader 'SEO' automation."""
    assert len(cat.all()) == 23


def test_catalog_ids_unique():
    ids = [a.id for a in cat.all()]
    assert len(ids) == len(set(ids))


def test_catalog_every_id_resolvable():
    for a in cat.all():
        assert cat.get(a.id) is a


def test_unknown_id_returns_none():
    assert cat.get("totally_made_up") is None


def test_exists_helper():
    assert cat.exists("reviews") is True
    assert cat.exists("nope") is False


# ---------------------------------------------------------------------------
# the canonical 7 + add-ons + AP-only
# ---------------------------------------------------------------------------


def test_canonical_seven_present():
    """The 7 generic automations every onboarding tenant gets must all
    appear in the catalog as starter+ defaults (except seo_recs which
    is pro+ and blog/social which are pro+)."""
    starter = {a.id for a in cat.for_tier("starter")}
    assert {"gbp", "seo", "reviews", "email_assistant", "chat_widget"}.issubset(starter)


def test_ap_only_systems_excluded_from_tiers():
    for tier in cat.VALID_TIERS:
        for a in cat.for_tier(tier):
            assert a.tenant_scope != "ap_only", f"{a.id} is ap_only but appears in tier {tier}"


def test_ap_only_systems_present():
    ap_only = [a for a in cat.all() if a.tenant_scope == "ap_only"]
    assert {a.id for a in ap_only} == {"daily_reports", "guard_compliance", "incident_trends"}


# ---------------------------------------------------------------------------
# tier defaults
# ---------------------------------------------------------------------------


def test_for_tier_starter_subset_of_pro():
    starter = {a.id for a in cat.for_tier("starter")}
    pro = {a.id for a in cat.for_tier("pro")}
    assert starter.issubset(pro), f"starter must be a subset of pro: {starter - pro}"


def test_for_tier_pro_subset_of_ultra():
    pro = {a.id for a in cat.for_tier("pro")}
    ultra = {a.id for a in cat.for_tier("ultra")}
    assert pro.issubset(ultra)


def test_for_tier_unknown_returns_empty():
    assert cat.for_tier("nonexistent") == ()


def test_tier_default_ids_returns_only_ids():
    out = cat.tier_default_ids("pro")
    assert isinstance(out, tuple)
    for entry in out:
        assert isinstance(entry, str)


# ---------------------------------------------------------------------------
# tenant_kind visibility
# ---------------------------------------------------------------------------


def test_visible_to_default_excludes_ap_only_and_internal():
    visible = {a.id for a in cat.visible_to("any")}
    assert "daily_reports" not in visible
    assert "system_watchdog" not in visible
    assert "guard_compliance" not in visible
    # but standard ones are present
    assert "reviews" in visible
    assert "gbp" in visible


def test_visible_to_ap_includes_ap_only():
    visible = {a.id for a in cat.visible_to("ap")}
    assert "daily_reports" in visible
    assert "guard_compliance" in visible
    assert "incident_trends" in visible
    # but wcas_internal still excluded
    assert "system_watchdog" not in visible


def test_visible_to_wcas_includes_everything():
    visible = {a.id for a in cat.visible_to("wcas")}
    assert "daily_reports" in visible
    assert "system_watchdog" in visible


# ---------------------------------------------------------------------------
# by_status / by_category
# ---------------------------------------------------------------------------


def test_by_status_shipped_includes_w5_pipelines():
    shipped = {a.id for a in cat.by_status("shipped")}
    # These shipped during W3-W5
    assert "reviews" in shipped
    assert "gbp" in shipped
    assert "seo" in shipped
    assert "email_assistant" in shipped


def test_by_status_planned_includes_blog():
    planned = {a.id for a in cat.by_status("planned")}
    assert "blog" in planned


def test_by_status_unknown_returns_empty():
    assert cat.by_status("invented_status") == ()


def test_by_category_core_includes_seven():
    core = {a.id for a in cat.by_category("core")}
    assert {"gbp", "seo", "reviews", "email_assistant", "chat_widget", "blog", "social"}.issubset(core)


def test_by_category_add_on_includes_voice_ai():
    add_on = {a.id for a in cat.by_category("add_on")}
    assert "voice_ai" in add_on
    assert "qbo_sync" in add_on


def test_by_category_unknown_returns_empty():
    assert cat.by_category("garbage") == ()


# ---------------------------------------------------------------------------
# names_for helper
# ---------------------------------------------------------------------------


def test_names_for_returns_id_to_name_dict():
    names = cat.names_for(["reviews", "gbp"])
    assert names == {"reviews": "Review Engine", "gbp": "Google Business Profile"}


def test_names_for_skips_unknown_ids():
    names = cat.names_for(["reviews", "made_up"])
    assert names == {"reviews": "Review Engine"}


# ---------------------------------------------------------------------------
# dataclass guard rails
# ---------------------------------------------------------------------------


def test_automation_rejects_invalid_status():
    with pytest.raises(ValueError):
        cat.Automation(
            id="x",
            name="X",
            status="not_a_status",
            default_tiers=(),
            category="core",
            description="x",
        )


def test_automation_rejects_invalid_category():
    with pytest.raises(ValueError):
        cat.Automation(
            id="x",
            name="X",
            status="shipped",
            default_tiers=(),
            category="garbage",
            description="x",
        )


def test_automation_rejects_invalid_tenant_scope():
    with pytest.raises(ValueError):
        cat.Automation(
            id="x",
            name="X",
            status="shipped",
            default_tiers=(),
            category="core",
            tenant_scope="bogus",
            description="x",
        )


def test_automation_rejects_invalid_tier():
    with pytest.raises(ValueError):
        cat.Automation(
            id="x",
            name="X",
            status="shipped",
            default_tiers=("enterprise",),
            category="core",
            description="x",
        )


def test_automation_is_frozen():
    a = cat.get("reviews")
    with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
        a.name = "new name"


# ---------------------------------------------------------------------------
# voice_ai per_minute_billing flag
# ---------------------------------------------------------------------------


def test_voice_ai_marked_per_minute_billing():
    voice = cat.get("voice_ai")
    assert voice is not None
    assert voice.per_minute_billing is True


def test_default_per_minute_billing_false():
    reviews = cat.get("reviews")
    assert reviews.per_minute_billing is False
