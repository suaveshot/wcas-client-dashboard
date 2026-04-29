"""Tests for dashboard_app.services.tenant_automations."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import pytest

from dashboard_app.services import automation_catalog as cat, tenant_automations as ta


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def tenant_root(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    return tmp_path


def _read_raw(tenant_root, tenant_id: str) -> dict:
    path = tenant_root / tenant_id / "config" / "automations.json"
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# read API on empty tenant
# ---------------------------------------------------------------------------


def test_list_enabled_empty_when_no_file(tenant_root):
    assert ta.list_enabled("acme") == []


def test_enabled_ids_empty_when_no_file(tenant_root):
    assert ta.enabled_ids("acme") == []


def test_is_enabled_false_when_no_file(tenant_root):
    assert ta.is_enabled("acme", "reviews") is False


def test_get_tier_none_when_no_file(tenant_root):
    assert ta.get_tier("acme") is None


def test_list_enabled_invalid_tenant_returns_empty(tenant_root):
    assert ta.list_enabled("../bad-slug") == []


# ---------------------------------------------------------------------------
# enable
# ---------------------------------------------------------------------------


def test_enable_persists_entry(tenant_root):
    entry = ta.enable("acme", "reviews", source="admin_added", note="Friends/family")
    assert entry["id"] == "reviews"
    assert entry["source"] == "admin_added"
    assert entry["note"] == "Friends/family"
    assert "enabled_at" in entry
    raw = _read_raw(tenant_root, "acme")
    assert raw["enabled"][0]["id"] == "reviews"


def test_enable_rejects_unknown_id(tenant_root):
    with pytest.raises(ta.TenantAutomationsError):
        ta.enable("acme", "made_up_thing")


def test_enable_rejects_invalid_source(tenant_root):
    with pytest.raises(ta.TenantAutomationsError):
        ta.enable("acme", "reviews", source="weird")


def test_enable_rejects_expires_at_for_non_promo(tenant_root):
    with pytest.raises(ta.TenantAutomationsError):
        ta.enable("acme", "reviews", source="admin_added", expires_at="2099-01-01T00:00:00+00:00")


def test_enable_promo_with_expires_at(tenant_root):
    iso = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    entry = ta.enable("acme", "voice_ai", source="promo_optin", expires_at=iso)
    assert entry["expires_at"] == iso


def test_enable_idempotent_updates_existing(tenant_root):
    ta.enable("acme", "reviews", source="tier_default")
    first = ta.list_enabled("acme")[0]
    ta.enable("acme", "reviews", source="admin_added", note="upgraded")
    after = ta.list_enabled("acme")
    assert len(after) == 1
    assert after[0]["source"] == "admin_added"
    assert after[0]["note"] == "upgraded"
    # enabled_at preserved
    assert after[0]["enabled_at"] == first["enabled_at"]


# ---------------------------------------------------------------------------
# disable
# ---------------------------------------------------------------------------


def test_disable_removes_entry(tenant_root):
    ta.enable("acme", "reviews")
    assert ta.disable("acme", "reviews") is True
    assert ta.enabled_ids("acme") == []


def test_disable_returns_false_when_absent(tenant_root):
    assert ta.disable("acme", "reviews") is False


def test_disable_only_removes_target(tenant_root):
    ta.enable("acme", "reviews")
    ta.enable("acme", "gbp")
    ta.disable("acme", "reviews")
    assert ta.enabled_ids("acme") == ["gbp"]


# ---------------------------------------------------------------------------
# seed_for_tier
# ---------------------------------------------------------------------------


def test_seed_for_tier_starter_seeds_starter_defaults(tenant_root):
    ta.seed_for_tier("acme", "starter")
    enabled = set(ta.enabled_ids("acme"))
    expected = {a.id for a in cat.for_tier("starter")}
    assert enabled == expected


def test_seed_for_tier_pro_includes_seo_recs(tenant_root):
    ta.seed_for_tier("acme", "pro")
    assert "seo_recs" in ta.enabled_ids("acme")


def test_seed_for_tier_records_tier(tenant_root):
    ta.seed_for_tier("acme", "ultra")
    assert ta.get_tier("acme") == "ultra"


def test_seed_for_tier_idempotent_by_default(tenant_root):
    """Re-seeding same tier doesn't duplicate or wipe admin_added entries."""
    ta.seed_for_tier("acme", "starter")
    ta.enable("acme", "google_ads_manager", source="admin_added")
    ta.seed_for_tier("acme", "starter")  # should be no-op for tier_defaults
    enabled = ta.enabled_ids("acme")
    assert "google_ads_manager" in enabled
    # And no duplicates
    assert len(enabled) == len(set(enabled))


def test_seed_for_tier_overwrite_replaces_tier_defaults(tenant_root):
    ta.seed_for_tier("acme", "starter")
    ta.enable("acme", "google_ads_manager", source="admin_added")
    ta.seed_for_tier("acme", "ultra", overwrite=True)
    enabled = set(ta.enabled_ids("acme"))
    expected = {a.id for a in cat.for_tier("ultra")} | {"google_ads_manager"}
    assert enabled == expected


def test_seed_for_tier_records_tier_change_even_when_no_overwrite(tenant_root):
    """Tier upgrade without overwrite still updates the stored tier."""
    ta.seed_for_tier("acme", "starter")
    ta.seed_for_tier("acme", "pro")  # no overwrite
    assert ta.get_tier("acme") == "pro"


def test_seed_for_tier_rejects_unknown_tier(tenant_root):
    with pytest.raises(ta.TenantAutomationsError):
        ta.seed_for_tier("acme", "enterprise")


def test_seed_for_tier_excludes_ap_only_from_non_ap_tenants(tenant_root):
    ta.seed_for_tier("acme", "ultra")
    enabled = ta.enabled_ids("acme")
    assert "daily_reports" not in enabled
    assert "guard_compliance" not in enabled
    assert "incident_trends" not in enabled


# ---------------------------------------------------------------------------
# prune_expired + expired-filtering on read
# ---------------------------------------------------------------------------


def test_list_enabled_filters_expired_promos(tenant_root):
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    ta.enable("acme", "reviews")
    ta.enable("acme", "voice_ai", source="promo_optin", expires_at=past)
    ta.enable("acme", "google_ads_manager", source="promo_optin", expires_at=future)
    enabled = set(ta.enabled_ids("acme"))
    assert "reviews" in enabled
    assert "voice_ai" not in enabled  # expired
    assert "google_ads_manager" in enabled


def test_list_enabled_include_expired_returns_everything(tenant_root):
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    ta.enable("acme", "voice_ai", source="promo_optin", expires_at=past)
    ids = [e["id"] for e in ta.list_enabled("acme", include_expired=True)]
    assert "voice_ai" in ids


def test_prune_expired_removes_only_expired_promos(tenant_root):
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    ta.enable("acme", "reviews")
    ta.enable("acme", "voice_ai", source="promo_optin", expires_at=past)
    ta.enable("acme", "google_ads_manager", source="promo_optin", expires_at=future)
    pruned = ta.prune_expired("acme")
    assert pruned == 1
    raw = _read_raw(tenant_root, "acme")
    ids_in_file = [e["id"] for e in raw["enabled"]]
    assert "voice_ai" not in ids_in_file
    assert "reviews" in ids_in_file
    assert "google_ads_manager" in ids_in_file


def test_prune_expired_no_op_when_none_expired(tenant_root):
    future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    ta.enable("acme", "voice_ai", source="promo_optin", expires_at=future)
    assert ta.prune_expired("acme") == 0


# ---------------------------------------------------------------------------
# malformed file recovery
# ---------------------------------------------------------------------------


def test_malformed_json_treated_as_empty(tenant_root):
    path = tenant_root / "acme" / "config" / "automations.json"
    path.parent.mkdir(parents=True)
    path.write_text("not json", encoding="utf-8")
    assert ta.list_enabled("acme") == []


def test_array_at_top_level_treated_as_empty(tenant_root):
    path = tenant_root / "acme" / "config" / "automations.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    assert ta.list_enabled("acme") == []


def test_enabled_entries_without_id_string_dropped(tenant_root):
    path = tenant_root / "acme" / "config" / "automations.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"enabled": [
            {"id": "reviews", "source": "admin_added"},
            {"source": "admin_added"},  # missing id
            "garbage",
            {"id": 42},  # non-string id
        ]}),
        encoding="utf-8",
    )
    out = ta.list_enabled("acme")
    assert len(out) == 1
    assert out[0]["id"] == "reviews"
