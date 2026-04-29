"""Home-surface composer tests."""

import os
from pathlib import Path

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import json

from dashboard_app.services import heartbeat_store, home_context


def test_empty_tenant_returns_placeholder(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    ctx = home_context.build(tenant_id="brand_new", owner_name="Jane")
    assert ctx["tenant_name"]
    assert ctx["owner_initials"] == "JA"
    assert ctx["roles"]  # placeholder row, not empty
    assert ctx["roles"][0]["slug"] == "first-run"
    assert ctx["attention"] is None
    assert ctx["narrative"].startswith("Your roles are connected")


def test_roles_reflect_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    heartbeat_store.write_snapshot("acme", "patrol", {
        "status": "ok",
        "last_run": "2026-04-22T07:00:00+00:00",
        "summary": "3 DARs sent",
    })
    heartbeat_store.write_snapshot("acme", "seo", {
        "status": "error",
        "last_run": "2026-04-22T08:00:00+00:00",
        "summary": "token expired",
    })
    ctx = home_context.build(tenant_id="acme", owner_name="Sam A")
    slugs = {r["slug"] for r in ctx["roles"]}
    assert "patrol" in slugs
    assert "seo" in slugs
    # Errored role -> attention banner surfaces with error kind.
    assert ctx["attention"] is not None
    assert ctx["attention"]["kind"] == "error"


def test_hero_stats_render_honest_placeholders(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    ctx = home_context.build(tenant_id="acme", owner_name="Sam A")
    labels = [s["label"] for s in ctx["hero_stats"]]
    assert labels == ["Weeks saved", "Revenue influenced", "Goal progress"]
    for stat in ctx["hero_stats"]:
        assert stat["value"] == "--"
        assert stat["verified_tip"]


# ---------------------------------------------------------------------------
# W6: catalog-driven ring rendering
# ---------------------------------------------------------------------------


def test_seeded_starter_tenant_renders_pending_rings(tmp_path, monkeypatch):
    """A tenant freshly seeded with the Starter tier should see one ring
    per starter-default automation, all in 'pending first run' state."""
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    from dashboard_app.services import automation_catalog as cat, tenant_automations
    tenant_automations.seed_for_tier("acme", "starter")
    ctx = home_context.build(tenant_id="acme", owner_name="Sam")
    starter_ids = {a.id for a in cat.for_tier("starter")}
    rendered_ids = {r["slug"].replace("-", "_") for r in ctx["roles"]}
    assert starter_ids.issubset(rendered_ids), f"missing rings: {starter_ids - rendered_ids}"
    # All rings are pending (no heartbeats yet)
    for r in ctx["roles"]:
        if r["slug"].replace("-", "_") in starter_ids:
            assert r["state"] == "pending"
            assert r["state_text"] == "pending first run"


def test_seeded_pro_tenant_includes_seo_recs_ring(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    from dashboard_app.services import tenant_automations
    tenant_automations.seed_for_tier("acme", "pro")
    ctx = home_context.build(tenant_id="acme", owner_name="Sam")
    rendered_ids = {r["slug"].replace("-", "_") for r in ctx["roles"]}
    assert "seo_recs" in rendered_ids


def test_heartbeat_promotes_pending_to_active(tmp_path, monkeypatch):
    """Once a heartbeat lands for an enabled automation, the ring flips
    from 'pending' to 'active' (or 'error', etc., per the heartbeat)."""
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    from dashboard_app.services import tenant_automations
    tenant_automations.seed_for_tier("acme", "starter")
    heartbeat_store.write_snapshot("acme", "reviews", {
        "status": "ok",
        "last_run": "2026-04-29T07:00:00+00:00",
        "summary": "ok",
    })
    ctx = home_context.build(tenant_id="acme", owner_name="Sam")
    reviews_ring = next((r for r in ctx["roles"] if r["slug"] == "reviews"), None)
    assert reviews_ring is not None
    assert reviews_ring["state"] != "pending"


def test_heartbeat_for_non_enabled_pipeline_still_renders(tmp_path, monkeypatch):
    """Backward-compat: AP runs pipelines that aren't (yet) in the
    catalog. Their heartbeats must still produce a ring."""
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    # No tenant_automations.json exists for AP; just heartbeats
    heartbeat_store.write_snapshot("americal_patrol", "patrol", {
        "status": "ok",
        "last_run": "2026-04-29T07:00:00+00:00",
        "summary": "3 DARs",
    })
    ctx = home_context.build(tenant_id="americal_patrol", owner_name="Sam")
    slugs = {r["slug"] for r in ctx["roles"]}
    assert "patrol" in slugs


def test_disabling_automation_removes_its_ring(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    from dashboard_app.services import tenant_automations
    tenant_automations.seed_for_tier("acme", "starter")
    tenant_automations.disable("acme", "reviews")
    ctx = home_context.build(tenant_id="acme", owner_name="Sam")
    rendered_ids = {r["slug"].replace("-", "_") for r in ctx["roles"]}
    assert "reviews" not in rendered_ids


def test_promo_optin_adds_ring(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    from datetime import datetime, timedelta, timezone
    from dashboard_app.services import tenant_automations
    tenant_automations.seed_for_tier("acme", "starter")
    future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    tenant_automations.enable("acme", "voice_ai", source="promo_optin", expires_at=future)
    ctx = home_context.build(tenant_id="acme", owner_name="Sam")
    rendered_ids = {r["slug"].replace("-", "_") for r in ctx["roles"]}
    assert "voice_ai" in rendered_ids
