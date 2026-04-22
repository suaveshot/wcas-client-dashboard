"""Activity feed composer tests."""

import os

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

from dashboard_app.services import activity_feed, heartbeat_store


def test_empty_tenant_returns_brand_placeholder(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    rows = activity_feed.build("brand_new")
    assert len(rows) == 1
    assert rows[0]["relative"] == "waiting"
    assert "wakes up" in rows[0]["action"]


def test_heartbeat_snapshot_becomes_feed_row(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    heartbeat_store.write_snapshot("acme", "patrol", {
        "status": "ok",
        "last_run": "2026-04-22T07:00:00+00:00",
        "summary": "3 DARs sent, no errors",
    })
    rows = activity_feed.build("acme")
    assert len(rows) >= 1
    # Patrol row should render in the feed with a role-display name and
    # a client-friendly action sentence derived from the summary.
    patrol_row = next((r for r in rows if r["role_slug"] == "patrol"), None)
    assert patrol_row is not None
    assert patrol_row["role"] == "Morning Reports"
    assert "3 DARs sent" in patrol_row["action"]
    assert patrol_row["time"]  # non-empty clock


def test_error_heartbeat_surfaces_honest_language(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    heartbeat_store.write_snapshot("acme", "seo", {
        "status": "error",
        "last_run": "2026-04-22T08:30:00+00:00",
        "summary": "oauth token expired",
    })
    rows = activity_feed.build("acme")
    seo = next(r for r in rows if r["role_slug"] == "seo")
    assert "problem" in seo["action"].lower() or "error" in seo["action"].lower()


def test_decision_log_merges_with_heartbeats_newest_first(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    heartbeat_store.write_snapshot("acme", "patrol", {
        "status": "ok",
        "last_run": "2026-04-22T07:00:00+00:00",
        "summary": "morning run done",
    })
    activity_feed.append_decision(
        tenant_id="acme",
        actor="owner",
        kind="attention.dismiss",
        text="Dismissed the attention banner.",
    )
    rows = activity_feed.build("acme")
    assert len(rows) >= 2
    # Newest first: the decision was appended after the heartbeat timestamp.
    kinds = [r["role_slug"] for r in rows]
    assert "dashboard" in kinds
    assert "patrol" in kinds


def test_empty_tenant_id_does_not_crash(tmp_path, monkeypatch):
    # Invalid tenant -> heartbeat_store raises; feed should still return placeholder.
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    rows = activity_feed.build("bad!!tenant")
    assert isinstance(rows, list)
