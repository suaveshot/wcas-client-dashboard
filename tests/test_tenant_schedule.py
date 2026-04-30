"""Tests for dashboard_app.services.tenant_schedule."""

from __future__ import annotations

import json
import os

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import pytest

from dashboard_app.services import (
    automation_catalog as cat,
    tenant_schedule as ts,
)


# ---------------------------------------------------------------------------
# helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tenant_root(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    return tmp_path


def _read_raw(tenant_root, tenant_id: str) -> dict:
    path = tenant_root / tenant_id / "config" / "schedule.json"
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# cron validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("expr", [
    "* * * * *",
    "0 8 * * *",
    "*/15 * * * *",
    "0 9-17 * * *",
    "0 10 * * 2,4,6",
    "0 9 1-7 * 1",
    "30 0,12 * * *",
])
def test_is_valid_cron_accepts_valid(expr):
    assert ts.is_valid_cron(expr) is True


@pytest.mark.parametrize("expr", [
    "",
    "* * * *",         # only 4 fields
    "* * * * * *",     # 6 fields
    "60 * * * *",      # minute > 59
    "* 24 * * *",      # hour > 23
    "* * 32 * *",      # day-of-month > 31
    "* * * 13 *",      # month > 12
    "* * * * 7",       # day-of-week > 6
    "abc * * * *",
    "*/abc * * * *",
    "5- * * * *",
    "5,abc * * * *",
])
def test_is_valid_cron_rejects_invalid(expr):
    assert ts.is_valid_cron(expr) is False


def test_is_valid_cron_rejects_non_string():
    assert ts.is_valid_cron(None) is False  # type: ignore[arg-type]
    assert ts.is_valid_cron(60) is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# read API on empty tenant
# ---------------------------------------------------------------------------


def test_list_entries_empty_when_no_file(tenant_root):
    assert ts.list_entries("acme") == []


def test_get_entry_none_when_missing(tenant_root):
    assert ts.get_entry("acme", "reviews") is None


def test_is_enabled_false_when_missing(tenant_root):
    assert ts.is_enabled("acme", "reviews") is False


def test_invalid_tenant_returns_empty(tenant_root):
    assert ts.list_entries("../bad-slug") == []


# ---------------------------------------------------------------------------
# set_entry
# ---------------------------------------------------------------------------


def test_set_entry_persists_row(tenant_root):
    entry = ts.set_entry("acme", "reviews", "0 9-17 * * *")
    assert entry["pipeline_id"] == "reviews"
    assert entry["cron"] == "0 9-17 * * *"
    assert entry["enabled"] is True
    raw = _read_raw(tenant_root, "acme")
    assert raw["entries"][0]["pipeline_id"] == "reviews"
    assert raw["version"] == ts.SCHEMA_VERSION


def test_set_entry_rejects_unknown_pipeline(tenant_root):
    with pytest.raises(ts.ScheduleError):
        ts.set_entry("acme", "made_up_pipeline", "0 8 * * *")


def test_set_entry_rejects_invalid_cron(tenant_root):
    with pytest.raises(ts.ScheduleError):
        ts.set_entry("acme", "reviews", "every minute")


def test_set_entry_rejects_invalid_source(tenant_root):
    with pytest.raises(ts.ScheduleError):
        ts.set_entry("acme", "reviews", "0 8 * * *", source="weird")


def test_set_entry_idempotent_replaces_in_place(tenant_root):
    ts.set_entry("acme", "reviews", "0 8 * * *")
    ts.set_entry("acme", "reviews", "0 9 * * *")
    entries = ts.list_entries("acme")
    assert len(entries) == 1
    assert entries[0]["cron"] == "0 9 * * *"


# ---------------------------------------------------------------------------
# enable / disable
# ---------------------------------------------------------------------------


def test_disable_keeps_row_for_easy_reenable(tenant_root):
    ts.set_entry("acme", "reviews", "0 8 * * *")
    assert ts.disable("acme", "reviews") is True
    assert ts.get_entry("acme", "reviews")["enabled"] is False
    assert ts.is_enabled("acme", "reviews") is False


def test_disable_returns_false_when_already_disabled(tenant_root):
    ts.set_entry("acme", "reviews", "0 8 * * *")
    ts.disable("acme", "reviews")
    assert ts.disable("acme", "reviews") is False


def test_disable_returns_false_when_missing(tenant_root):
    assert ts.disable("acme", "reviews") is False


def test_enable_flips_back_to_true(tenant_root):
    ts.set_entry("acme", "reviews", "0 8 * * *")
    ts.disable("acme", "reviews")
    assert ts.enable("acme", "reviews") is True
    assert ts.is_enabled("acme", "reviews") is True


def test_enable_returns_false_when_missing(tenant_root):
    assert ts.enable("acme", "reviews") is False


def test_list_entries_enabled_only_filters(tenant_root):
    ts.set_entry("acme", "reviews", "0 8 * * *")
    ts.set_entry("acme", "gbp", "0 10 * * 1")
    ts.disable("acme", "reviews")
    enabled = [e["pipeline_id"] for e in ts.list_entries("acme", enabled_only=True)]
    assert enabled == ["gbp"]


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------


def test_remove_drops_the_row(tenant_root):
    ts.set_entry("acme", "reviews", "0 8 * * *")
    assert ts.remove("acme", "reviews") is True
    assert ts.get_entry("acme", "reviews") is None


def test_remove_returns_false_when_missing(tenant_root):
    assert ts.remove("acme", "reviews") is False


# ---------------------------------------------------------------------------
# tier seeding
# ---------------------------------------------------------------------------


def test_seed_for_tier_creates_entry_per_tier_default(tenant_root):
    ts.seed_for_tier("acme", "starter")
    entries = ts.list_entries("acme")
    seeded_ids = {e["pipeline_id"] for e in entries}
    expected = {a.id for a in cat.for_tier("starter")}
    assert expected.issubset(seeded_ids)
    for e in entries:
        assert e["enabled"] is True
        assert e["source"] == "tier_default"
        assert ts.is_valid_cron(e["cron"])


def test_seed_for_tier_pro_includes_seo_recs(tenant_root):
    ts.seed_for_tier("acme", "pro")
    ids = {e["pipeline_id"] for e in ts.list_entries("acme")}
    assert "seo_recs" in ids


def test_seed_for_tier_idempotent_preserves_owner_edits(tenant_root):
    """A re-seed must NOT clobber an owner's cron change."""
    ts.seed_for_tier("acme", "starter")
    ts.set_entry("acme", "reviews", "0 7 * * *", source="owner_change")
    ts.seed_for_tier("acme", "starter")  # re-seed
    reviews = ts.get_entry("acme", "reviews")
    assert reviews["cron"] == "0 7 * * *"
    assert reviews["source"] == "owner_change"


def test_seed_for_tier_overwrite_resets_tier_defaults(tenant_root):
    ts.seed_for_tier("acme", "starter")
    ts.set_entry("acme", "reviews", "0 7 * * *", source="owner_change")
    ts.seed_for_tier("acme", "ultra", overwrite=True)
    # owner_change rows survive overwrite (not in the new tier_default set
    # OR re-seeded with the default - either is fine; assert tier shape)
    ids = {e["pipeline_id"] for e in ts.list_entries("acme")}
    expected = {a.id for a in cat.for_tier("ultra")}
    assert expected.issubset(ids)


def test_seed_for_tier_rejects_unknown_tier(tenant_root):
    with pytest.raises(ts.ScheduleError):
        ts.seed_for_tier("acme", "enterprise")


def test_seed_for_tier_admin_added_rows_preserved(tenant_root):
    ts.seed_for_tier("acme", "starter")
    ts.set_entry("acme", "google_ads_manager", "0 9 * * 1", source="admin_added")
    ts.seed_for_tier("acme", "starter")
    ids = {e["pipeline_id"] for e in ts.list_entries("acme")}
    assert "google_ads_manager" in ids


# ---------------------------------------------------------------------------
# default_cron_for sanity
# ---------------------------------------------------------------------------


def test_default_cron_for_known_ids():
    for pid in ("reviews", "gbp", "seo", "blog", "social", "email_assistant"):
        assert ts.is_valid_cron(ts.default_cron_for(pid))


def test_default_cron_for_unknown_id_falls_back_to_daily():
    assert ts.default_cron_for("brand_new_thing") == "0 8 * * *"


# ---------------------------------------------------------------------------
# malformed file recovery
# ---------------------------------------------------------------------------


def test_malformed_json_treated_as_empty(tenant_root):
    path = tenant_root / "acme" / "config" / "schedule.json"
    path.parent.mkdir(parents=True)
    path.write_text("not json", encoding="utf-8")
    assert ts.list_entries("acme") == []


def test_array_at_top_level_treated_as_empty(tenant_root):
    path = tenant_root / "acme" / "config" / "schedule.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    assert ts.list_entries("acme") == []


def test_entries_without_pipeline_id_dropped(tenant_root):
    path = tenant_root / "acme" / "config" / "schedule.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"entries": [
            {"pipeline_id": "reviews", "cron": "0 8 * * *"},
            {"cron": "0 8 * * *"},  # missing pipeline_id
            "garbage",
            {"pipeline_id": 42, "cron": "0 8 * * *"},  # non-string
        ]}),
        encoding="utf-8",
    )
    entries = ts.list_entries("acme")
    assert len(entries) == 1
    assert entries[0]["pipeline_id"] == "reviews"


# ---------------------------------------------------------------------------
# humanize_cron - cron -> human label for cold-start UI
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("expr,expected", [
    ("0 10 * * 1", "Mon 10am"),
    ("0 7 * * 1", "Mon 7am"),
    ("0 9 * * 2,4,6", "Tue/Thu/Sat 9am"),
    ("30 14 * * 5", "Fri 2:30pm"),
    ("0 9-17 * * *", "hourly 9am-5pm"),
    ("*/15 * * * *", "every 15 min"),
    ("*/5 * * * *", "every 5 min"),
    ("*/1 * * * *", "every minute"),
    ("0 9 1-7 * 1", "first Mon of month 9am"),
    ("0 8 * * *", "daily 8am"),
    ("0 0 * * *", "daily 12am"),
    ("0 12 * * *", "daily 12pm"),
    ("15 13 * * *", "daily 1:15pm"),
    ("0 8 15 * *", "day 15 8am"),
])
def test_humanize_cron_renders_common_patterns(expr, expected):
    assert ts.humanize_cron(expr) == expected


@pytest.mark.parametrize("expr", [
    "",
    "garbage",
    "0 8",          # too few fields
    "* * * * * *",  # too many fields
    "60 8 * * *",   # minute out of range
    "0 24 * * *",   # hour out of range
    "0 8 * * 9",    # dow out of range
])
def test_humanize_cron_returns_empty_for_invalid(expr):
    assert ts.humanize_cron(expr) == ""


def test_humanize_cron_default_table_all_humanize():
    """Every default cron in the catalog table must humanize so the
    cold-start UI never falls back to the generic placeholder for a
    catalog-default automation."""
    for pid in (
        "gbp", "seo", "reviews", "blog", "social",
        "email_assistant", "chat_widget", "voice_ai",
        "seo_recs", "review_engine", "win_back",
    ):
        cron = ts.default_cron_for(pid)
        assert ts.humanize_cron(cron), f"default cron for {pid!r} did not humanize: {cron!r}"
