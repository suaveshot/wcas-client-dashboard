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
