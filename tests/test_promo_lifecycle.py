"""Tests for dashboard_app.services.promo_lifecycle."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import pytest

from dashboard_app.services import (
    promo_lifecycle as pl,
    tenant_automations as ta,
)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tenant_root(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    return tmp_path


def _seed_tenant_dir(tenant_root, tenant_id: str) -> None:
    """Create an empty tenant directory so iterators can see it."""
    (tenant_root / tenant_id).mkdir(parents=True, exist_ok=True)


def _read_raw(tenant_root, tenant_id: str) -> dict:
    path = tenant_root / tenant_id / "config" / "automations.json"
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# grant_promo
# ---------------------------------------------------------------------------


def test_grant_promo_writes_promo_optin_row(tenant_root):
    now = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    entry = pl.grant_promo("acme", "voice_ai", days=30, now=now)
    assert entry["id"] == "voice_ai"
    assert entry["source"] == "promo_optin"
    expected_expiry = (now + timedelta(days=30)).isoformat()
    assert entry["expires_at"] == expected_expiry
    raw = _read_raw(tenant_root, "acme")
    assert any(
        e["id"] == "voice_ai" and e["source"] == "promo_optin"
        for e in raw["enabled"]
    )


def test_grant_promo_works_for_any_catalog_id(tenant_root):
    """Generic by design: not voice_ai-specific."""
    now = datetime(2026, 5, 1, tzinfo=timezone.utc)
    for aid in ("gbp", "seo", "reviews", "email_assistant", "blog"):
        entry = pl.grant_promo("acme", aid, days=14, now=now)
        assert entry["id"] == aid
        assert entry["source"] == "promo_optin"


def test_grant_promo_rejects_unknown_automation(tenant_root):
    now = datetime(2026, 5, 1, tzinfo=timezone.utc)
    with pytest.raises(pl.PromoError) as exc_info:
        pl.grant_promo("acme", "made_up_thing", days=30, now=now)
    assert "not in catalog" in str(exc_info.value)


def test_grant_promo_rejects_bad_tenant_slug(tenant_root):
    now = datetime(2026, 5, 1, tzinfo=timezone.utc)
    with pytest.raises(pl.PromoError):
        pl.grant_promo("../bad-slug", "reviews", days=30, now=now)


def test_grant_promo_rejects_zero_days(tenant_root):
    now = datetime(2026, 5, 1, tzinfo=timezone.utc)
    with pytest.raises(pl.PromoError):
        pl.grant_promo("acme", "reviews", days=0, now=now)


def test_grant_promo_rejects_negative_days(tenant_root):
    now = datetime(2026, 5, 1, tzinfo=timezone.utc)
    with pytest.raises(pl.PromoError):
        pl.grant_promo("acme", "reviews", days=-7, now=now)


def test_grant_promo_rejects_non_int_days(tenant_root):
    now = datetime(2026, 5, 1, tzinfo=timezone.utc)
    with pytest.raises(pl.PromoError):
        pl.grant_promo("acme", "reviews", days=3.5, now=now)  # type: ignore[arg-type]


def test_grant_promo_defaults_now_to_real_clock(tenant_root):
    """If now is omitted, the function still writes a sensible row."""
    before = datetime.now(timezone.utc)
    entry = pl.grant_promo("acme", "reviews", days=10)
    after = datetime.now(timezone.utc)
    expires = datetime.fromisoformat(entry["expires_at"])
    assert before + timedelta(days=10) - timedelta(seconds=5) <= expires
    assert expires <= after + timedelta(days=10) + timedelta(seconds=5)


# ---------------------------------------------------------------------------
# revoke_promo
# ---------------------------------------------------------------------------


def test_revoke_promo_removes_promo_row(tenant_root):
    now = datetime(2026, 5, 1, tzinfo=timezone.utc)
    pl.grant_promo("acme", "voice_ai", days=30, now=now)
    assert pl.revoke_promo("acme", "voice_ai") is True
    assert ta.is_enabled("acme", "voice_ai") is False


def test_revoke_promo_refuses_tier_default(tenant_root):
    ta.enable("acme", "reviews", source="tier_default")
    with pytest.raises(pl.PromoError) as exc_info:
        pl.revoke_promo("acme", "reviews")
    assert "tier_default" in str(exc_info.value)
    # And the row is still there.
    assert ta.is_enabled("acme", "reviews") is True


def test_revoke_promo_refuses_admin_added(tenant_root):
    ta.enable("acme", "google_ads_manager", source="admin_added")
    with pytest.raises(pl.PromoError) as exc_info:
        pl.revoke_promo("acme", "google_ads_manager")
    assert "admin_added" in str(exc_info.value)
    assert ta.is_enabled("acme", "google_ads_manager") is True


def test_revoke_promo_returns_false_when_never_enrolled(tenant_root):
    assert pl.revoke_promo("acme", "voice_ai") is False


def test_revoke_promo_rejects_unknown_automation(tenant_root):
    with pytest.raises(pl.PromoError):
        pl.revoke_promo("acme", "made_up_thing")


def test_revoke_promo_rejects_bad_tenant(tenant_root):
    with pytest.raises(pl.PromoError):
        pl.revoke_promo("../bad", "reviews")


# ---------------------------------------------------------------------------
# find_expiring_soon
# ---------------------------------------------------------------------------


def test_find_expiring_soon_includes_within_threshold(tenant_root):
    now = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    # Expires in 1 day -> in result with default threshold of 3 days.
    pl.grant_promo("acme", "voice_ai", days=1, now=now)
    rows = pl.find_expiring_soon("acme", now=now)
    ids = [r["id"] for r in rows]
    assert "voice_ai" in ids


def test_find_expiring_soon_excludes_beyond_threshold(tenant_root):
    now = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    # Expires in 5 days -> NOT in result with default threshold of 3 days.
    pl.grant_promo("acme", "voice_ai", days=5, now=now)
    rows = pl.find_expiring_soon("acme", now=now)
    assert rows == []


def test_find_expiring_soon_respects_custom_threshold(tenant_root):
    now = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    pl.grant_promo("acme", "voice_ai", days=5, now=now)
    rows = pl.find_expiring_soon("acme", now=now, threshold_days=7)
    assert any(r["id"] == "voice_ai" for r in rows)


def test_find_expiring_soon_excludes_tier_default(tenant_root):
    """tier_default never has expires_at; must not surface."""
    now = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    ta.enable("acme", "reviews", source="tier_default")
    pl.grant_promo("acme", "voice_ai", days=1, now=now)
    rows = pl.find_expiring_soon("acme", now=now)
    ids = [r["id"] for r in rows]
    assert "reviews" not in ids
    assert "voice_ai" in ids


def test_find_expiring_soon_excludes_admin_added(tenant_root):
    now = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    ta.enable("acme", "google_ads_manager", source="admin_added")
    pl.grant_promo("acme", "voice_ai", days=1, now=now)
    rows = pl.find_expiring_soon("acme", now=now)
    ids = [r["id"] for r in rows]
    assert "google_ads_manager" not in ids
    assert "voice_ai" in ids


def test_find_expiring_soon_excludes_already_expired(tenant_root):
    """Already-expired promos are filtered by list_enabled, so they
    must not appear in the expiring-soon window either."""
    now = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    past = (now - timedelta(days=1)).isoformat()
    ta.enable("acme", "voice_ai", source="promo_optin", expires_at=past)
    rows = pl.find_expiring_soon("acme", now=now)
    assert rows == []


# ---------------------------------------------------------------------------
# find_expiring_soon_all_tenants
# ---------------------------------------------------------------------------


def test_find_expiring_soon_all_tenants_aggregates(tenant_root):
    now = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    pl.grant_promo("acme", "voice_ai", days=1, now=now)
    pl.grant_promo("garcia_folklorico", "blog", days=2, now=now)
    pl.grant_promo("foo", "social", days=10, now=now)  # outside threshold
    result = pl.find_expiring_soon_all_tenants(now=now)
    assert "acme" in result
    assert "garcia_folklorico" in result
    assert "foo" not in result  # nothing expiring soon
    assert [r["id"] for r in result["acme"]] == ["voice_ai"]


def test_find_expiring_soon_all_tenants_skips_underscore_dirs(tenant_root):
    now = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    # Real tenant
    pl.grant_promo("acme", "voice_ai", days=1, now=now)
    # Reserved platform dir; even if it had a config we should skip it.
    (tenant_root / "_platform").mkdir()
    (tenant_root / "_platform" / "config").mkdir()
    (tenant_root / "_platform" / "config" / "automations.json").write_text(
        json.dumps({
            "tier": None,
            "enabled": [{
                "id": "voice_ai",
                "source": "promo_optin",
                "enabled_at": now.isoformat(),
                "expires_at": (now + timedelta(days=1)).isoformat(),
            }],
        }),
        encoding="utf-8",
    )
    result = pl.find_expiring_soon_all_tenants(now=now)
    assert "_platform" not in result
    assert "acme" in result


def test_find_expiring_soon_all_tenants_returns_empty_when_no_tenants(tenant_root):
    now = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    assert pl.find_expiring_soon_all_tenants(now=now) == {}


# ---------------------------------------------------------------------------
# sweep_expired_all_tenants
# ---------------------------------------------------------------------------


def test_sweep_expired_all_tenants_aggregates_counts(tenant_root):
    # prune_expired uses the real clock to decide "expired". Anchor to
    # real now so the past/future ISO strings line up with that clock.
    real_now = datetime.now(timezone.utc)
    past = (real_now - timedelta(days=2)).isoformat()
    future = (real_now + timedelta(days=30)).isoformat()
    # acme: 2 expired, 1 active
    ta.enable("acme", "voice_ai", source="promo_optin", expires_at=past)
    ta.enable("acme", "blog", source="promo_optin", expires_at=past)
    ta.enable("acme", "social", source="promo_optin", expires_at=future)
    # foo: 1 expired
    ta.enable("foo", "voice_ai", source="promo_optin", expires_at=past)
    # bar: nothing expired
    ta.enable("bar", "voice_ai", source="promo_optin", expires_at=future)

    # `now` here is the swept_at marker the dispatcher will log, not the
    # comparison clock for prune_expired itself.
    sweep_now = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    result = pl.sweep_expired_all_tenants(now=sweep_now)
    assert result["swept_at"] == sweep_now.isoformat()
    assert result["tenants_swept"] == 3
    assert result["rows_pruned"] == 3
    assert result["by_tenant"]["acme"] == 2
    assert result["by_tenant"]["foo"] == 1
    assert result["by_tenant"]["bar"] == 0


def test_sweep_expired_all_tenants_skips_underscore_dirs(tenant_root):
    now = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    pl.grant_promo("acme", "voice_ai", days=1, now=now)
    (tenant_root / "_platform").mkdir()
    result = pl.sweep_expired_all_tenants(now=now)
    assert "_platform" not in result["by_tenant"]
    assert "acme" in result["by_tenant"]
    assert result["tenants_swept"] == 1


def test_sweep_expired_all_tenants_handles_no_tenants(tenant_root):
    now = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    result = pl.sweep_expired_all_tenants(now=now)
    assert result["tenants_swept"] == 0
    assert result["rows_pruned"] == 0
    assert result["by_tenant"] == {}
