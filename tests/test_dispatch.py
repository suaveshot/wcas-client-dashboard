"""Tests for services/dispatch.py - the shared registry that wires
/approvals, /recommendations, /goals, and /settings to real action.

Covers four entry points:
- send(): pipeline-side. Honors pause + require_approval gates.
- deliver_approved(): post-/approvals-click. Honors pause only.
- execute_rec(): /recommendations Apply path.
- handle_heartbeat_events(): bumps goals on lead.created / review.posted.

Plus the supporting outgoing_queue.mark_send_failed helper that flips
an archived entry's status when the dispatcher's send fails.
"""

from __future__ import annotations

import json
import os

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import pytest

from dashboard_app.services import (
    dispatch,
    goals,
    heartbeat_store,
    outgoing_queue,
    recs_store,
    tenant_prefs,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _set_tenant_status(tenant_id: str, status: str) -> None:
    path = heartbeat_store.tenant_root(tenant_id) / "tenant_config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"status": status}), encoding="utf-8")


def _seed_rec(tenant_id: str, rec: dict) -> None:
    """Drop one rec into today's recs file so dispatch.execute_rec can find it."""
    recs_store.write_today(
        tenant_id,
        recs=[rec],
        model="claude-test",
        usd=0.0,
    )


# ---------------------------------------------------------------------------
# is_paused / requires_approval gates
# ---------------------------------------------------------------------------


def test_is_paused_reads_tenant_config(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    assert dispatch.is_paused("acme") is False  # no config file = not paused
    _set_tenant_status("acme", "paused")
    assert dispatch.is_paused("acme") is True
    _set_tenant_status("acme", "active")
    assert dispatch.is_paused("acme") is False


def test_requires_approval_reads_prefs(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    assert dispatch.requires_approval("acme", "reviews") is False
    tenant_prefs.set_require_approval("acme", "reviews", True)
    assert dispatch.requires_approval("acme", "reviews") is True
    assert dispatch.requires_approval("acme", "blog") is False


# ---------------------------------------------------------------------------
# send() - pipeline-side entry point
# ---------------------------------------------------------------------------


def test_send_skips_when_paused(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _set_tenant_status("acme", "paused")
    result = dispatch.send(
        tenant_id="acme",
        pipeline_id="reviews",
        channel="gbp_review_reply",
        recipient_hint="Maria",
        subject="(no subject)",
        body="Thank you Maria.",
    )
    assert result["action"] == "skipped"
    assert result["reason"] == "tenant_paused"
    # And nothing got queued or archived
    assert outgoing_queue.list_pending("acme") == []


def test_send_queues_when_require_approval_set(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    tenant_prefs.set_require_approval("acme", "reviews", True)
    result = dispatch.send(
        tenant_id="acme",
        pipeline_id="reviews",
        channel="gbp_review_reply",
        recipient_hint="Maria",
        subject="Reply to 5-star review",
        body="Thank you Maria.",
    )
    assert result["action"] == "queued"
    assert result["draft_id"]
    pending = outgoing_queue.list_pending("acme")
    assert len(pending) == 1
    assert pending[0]["pipeline_id"] == "reviews"


def test_send_delivers_when_handler_exists_and_no_gates(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    monkeypatch.setenv("DISPATCH_DRY_RUN", "true")  # don't hit real GBP
    result = dispatch.send(
        tenant_id="acme",
        pipeline_id="reviews",
        channel="gbp_review_reply",
        recipient_hint="Maria",
        subject="(no subject)",
        body="Thank you Maria.",
    )
    assert result["action"] == "delivered"
    assert result["handler"] == "reviews"


def test_send_returns_no_dispatcher_for_unknown_pipeline(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    result = dispatch.send(
        tenant_id="acme",
        pipeline_id="not_a_real_pipeline",
        channel="email",
        recipient_hint="x@y.com",
        subject="x",
        body="y",
    )
    assert result["action"] == "no_dispatcher"
    assert result["pipeline_id"] == "not_a_real_pipeline"


# ---------------------------------------------------------------------------
# deliver_approved() - post-approval path
# ---------------------------------------------------------------------------


def test_deliver_approved_honors_pause(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _set_tenant_status("acme", "paused")
    entry = {
        "id": "draft-reviews-x",
        "pipeline_id": "reviews",
        "channel": "gbp_review_reply",
        "recipient_hint": "Maria",
        "subject": "(no subject)",
        "body": "Thank you Maria.",
    }
    result = dispatch.deliver_approved("acme", entry)
    assert result["ok"] is False
    assert result["reason"] == "tenant_paused"


def test_deliver_approved_runs_handler_when_active(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    monkeypatch.setenv("DISPATCH_DRY_RUN", "true")
    entry = {
        "id": "draft-reviews-1",
        "pipeline_id": "reviews",
        "channel": "gbp_review_reply",
        "recipient_hint": "Maria",
        "subject": "(no subject)",
        "body": "Thank you Maria.",
    }
    result = dispatch.deliver_approved("acme", entry)
    assert result["ok"] is True
    assert result["status"] == "delivered"


def test_deliver_approved_marks_failed_on_dispatch_error(tmp_path, monkeypatch):
    """When the outgoing handler raises DispatchError, the archived.jsonl entry
    flips to status=approved_send_failed and the error is recorded."""
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    # Enqueue + approve so an archived entry exists
    enqueued = outgoing_queue.enqueue(
        tenant_id="acme",
        pipeline_id="reviews",
        channel="gbp_review_reply",
        recipient_hint="Maria",
        subject="(no subject)",
        body="Thank you Maria.",
    )
    approved = outgoing_queue.approve("acme", enqueued["id"])

    # Force the reviews handler to raise
    def _boom(tenant_id, payload):
        raise dispatch.DispatchError("simulated GBP outage")

    monkeypatch.setitem(dispatch.OUTGOING_HANDLERS, "reviews", _boom)

    result = dispatch.deliver_approved("acme", approved)
    assert result["ok"] is False
    assert "simulated GBP outage" in result["reason"]

    # Archived entry should now be flagged as failed
    archive_path = heartbeat_store.tenant_root("acme") / "outgoing" / "archived.jsonl"
    rows = [json.loads(line) for line in archive_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    target = next(r for r in rows if r["id"] == approved["id"])
    assert target["status"] == "approved_send_failed"
    assert "simulated GBP outage" in target["dispatch_error"]


def test_deliver_approved_returns_no_dispatcher_for_unknown_pipeline(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    entry = {
        "id": "draft-mystery-x",
        "pipeline_id": "mystery_pipeline",
        "channel": "email",
        "body": "y",
    }
    result = dispatch.deliver_approved("acme", entry)
    assert result["ok"] is False
    assert result["reason"] == "no_dispatcher"


# ---------------------------------------------------------------------------
# execute_rec() - /recommendations Apply path
# ---------------------------------------------------------------------------


def test_execute_rec_skips_when_paused(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _set_tenant_status("acme", "paused")
    _seed_rec("acme", {"id": "rec1", "proposed_tool": "review_reply_draft", "title": "x", "review": {"reviewer": "Maria", "stars": 5}})
    result = dispatch.execute_rec("acme", "rec1")
    assert result["ok"] is False
    assert result["reason"] == "tenant_paused"


def test_execute_rec_returns_not_found_for_unknown_rec(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    result = dispatch.execute_rec("acme", "rec_does_not_exist")
    assert result["ok"] is False
    assert result["reason"] == "rec_not_found"


def test_execute_rec_review_reply_draft_creates_outgoing_draft(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _seed_rec("acme", {
        "id": "rec_review_1",
        "proposed_tool": "review_reply_draft",
        "title": "Reply to Maria's 5-star review",
        "review": {"reviewer": "Maria Sanchez", "stars": 5, "text": "Best class ever!"},
        "draft_body": "Thank you Maria! See you next class.",
    })
    result = dispatch.execute_rec("acme", "rec_review_1")
    assert result["ok"] is True
    assert result["outcome"]["draft_id"]
    pending = outgoing_queue.list_pending("acme")
    assert len(pending) == 1
    assert pending[0]["pipeline_id"] == "reviews"
    assert "Maria" in pending[0]["body"]


def test_execute_rec_unknown_proposed_tool_returns_queued_for_review(tmp_path, monkeypatch):
    """Honest stub: rec types without a registered handler land in queued_for_review
    so Sam can hand-execute them. Per the audit's recommended behavior."""
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _seed_rec("acme", {
        "id": "rec_x",
        "proposed_tool": "schedule_change",  # not yet implemented
        "title": "Move blog day to Wednesday",
    })
    result = dispatch.execute_rec("acme", "rec_x")
    assert result["ok"] is True
    assert result["outcome"]["queued_for_review"] is True
    assert "schedule_change" in result["outcome"]["reason"]


# ---------------------------------------------------------------------------
# handle_heartbeat_events() - goals F1
# ---------------------------------------------------------------------------


def test_handle_heartbeat_events_bumps_leads_goal(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    g = goals.add("acme", title="Get 20 leads", metric="leads", target=20, timeframe="90d")
    dispatch.handle_heartbeat_events("acme", [{"kind": "lead.created", "count": 1}])
    again = goals.read("acme")
    assert again["goals"][0]["current"] == 1.0


def test_handle_heartbeat_events_bumps_reviews_goal_5_star_only(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    goals.add("acme", title="Get 10 5-star reviews", metric="reviews", target=10, timeframe="90d")
    # 4-star ignored
    dispatch.handle_heartbeat_events("acme", [{"kind": "review.posted", "stars": 4}])
    assert goals.read("acme")["goals"][0]["current"] == 0
    # 5-star bumps
    dispatch.handle_heartbeat_events("acme", [{"kind": "review.posted", "stars": 5}])
    assert goals.read("acme")["goals"][0]["current"] == 1.0


def test_handle_heartbeat_events_handles_no_goals_without_error(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    # No goals pinned → quiet no-op, no exception
    dispatch.handle_heartbeat_events("acme", [{"kind": "lead.created", "count": 1}])
    assert goals.read("acme")["goals"] == []


def test_handle_heartbeat_events_empty_events_is_no_op(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    g = goals.add("acme", title="Get 20 leads", metric="leads", target=20, timeframe="90d")
    dispatch.handle_heartbeat_events("acme", [])
    dispatch.handle_heartbeat_events("acme", None)  # type: ignore[arg-type]
    assert goals.read("acme")["goals"][0]["current"] == 0


def test_handle_heartbeat_events_count_field_respected(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    goals.add("acme", title="Get 100 leads", metric="leads", target=100, timeframe="90d")
    dispatch.handle_heartbeat_events("acme", [{"kind": "lead.created", "count": 5}])
    assert goals.read("acme")["goals"][0]["current"] == 5.0


# ---------------------------------------------------------------------------
# outgoing_queue.mark_send_failed - supporting helper
# ---------------------------------------------------------------------------


def test_mark_send_failed_flips_archived_entry_status(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    enqueued = outgoing_queue.enqueue(
        tenant_id="acme",
        pipeline_id="reviews",
        channel="gbp_review_reply",
        recipient_hint="Maria",
        subject="x",
        body="Thank you Maria.",
    )
    approved = outgoing_queue.approve("acme", enqueued["id"])
    assert approved["status"] == "approved"

    ok = outgoing_queue.mark_send_failed("acme", approved["id"], "GBP 503")
    assert ok is True

    archive = heartbeat_store.tenant_root("acme") / "outgoing" / "archived.jsonl"
    rows = [json.loads(line) for line in archive.read_text(encoding="utf-8").splitlines() if line.strip()]
    target = next(r for r in rows if r["id"] == approved["id"])
    assert target["status"] == "approved_send_failed"
    assert target["dispatch_error"] == "GBP 503"
    assert "dispatch_failed_at" in target


def test_mark_send_failed_returns_false_for_unknown_id(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    # No archive file yet -> False, no error
    assert outgoing_queue.mark_send_failed("acme", "nonexistent", "x") is False


# ---------------------------------------------------------------------------
# /api/heartbeat integration: events array bumps goals end-to-end
# (closes audits/phase0_goals.md::F1 at the API surface)
# ---------------------------------------------------------------------------


def test_heartbeat_endpoint_bumps_goal_on_events(tmp_path, monkeypatch):
    """A pipeline POSTing a heartbeat with events=[{kind: lead.created, count: 2}]
    bumps the matching pinned goal end-to-end through /api/heartbeat."""
    import os as _os
    from fastapi.testclient import TestClient
    from dashboard_app.main import app

    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    monkeypatch.setenv("HEARTBEAT_SHARED_SECRET", "test-hb-secret-w3")

    goals.add("acme", title="Get 50 leads this quarter", metric="leads", target=50, timeframe="90d")

    client = TestClient(app)
    resp = client.post(
        "/api/heartbeat",
        json={
            "pipeline_id": "sales_pipeline",
            "status": "ok",
            "last_run": "2026-04-29T10:00:00Z",
            "events": [{"kind": "lead.created", "count": 2}],
        },
        headers={
            "X-Heartbeat-Secret": _os.environ["HEARTBEAT_SHARED_SECRET"],
            "X-Tenant-Id": "acme",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["stored"] is True
    assert goals.read("acme")["goals"][0]["current"] == 2.0


def test_heartbeat_endpoint_ignores_missing_events_key(tmp_path, monkeypatch):
    """Existing AP heartbeats (no events key) keep working unchanged - the
    goal stays at 0 and no errors surface."""
    import os as _os
    from fastapi.testclient import TestClient
    from dashboard_app.main import app

    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    monkeypatch.setenv("HEARTBEAT_SHARED_SECRET", "test-hb-secret-w3")

    goals.add("acme", title="Get 50 leads", metric="leads", target=50, timeframe="90d")

    client = TestClient(app)
    resp = client.post(
        "/api/heartbeat",
        json={
            "pipeline_id": "patrol_automation",
            "status": "ok",
            "last_run": "2026-04-29T10:00:00Z",
            "summary": "3 DARs drafted",
        },
        headers={
            "X-Heartbeat-Secret": _os.environ["HEARTBEAT_SHARED_SECRET"],
            "X-Tenant-Id": "acme",
        },
    )
    assert resp.status_code == 200
    assert goals.read("acme")["goals"][0]["current"] == 0  # unchanged


# ---------------------------------------------------------------------------
# /settings UX integration: F1 Resume button, F5 banner, F8 default 7 roles
# ---------------------------------------------------------------------------


def _settings_signed_cookie(tenant_id="acme"):
    from dashboard_app.services import sessions
    return sessions.issue(tenant_id=tenant_id, email="owner@acme.com", role="client")


def test_settings_renders_canonical_seven_roles_for_new_tenant(tmp_path, monkeypatch):
    """F8: a tenant with zero heartbeats still sees all 7 onboarding roles
    so they can pre-flight Approve-Before-Send safety before first send."""
    from fastapi.testclient import TestClient
    from dashboard_app.main import app
    from dashboard_app.services import roster

    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    client = TestClient(app)
    cookie = _settings_signed_cookie("brand_new_tenant")
    resp = client.get("/settings", cookies={"wcas_session": cookie})
    assert resp.status_code == 200
    body = resp.text
    for role in roster.ACTIVATION_ROSTER:
        assert f'data-pipeline-id="{role["slug"]}"' in body, f"missing toggle for {role['slug']}"
        assert role["name"] in body
    # And the "pending first run" tag should appear since no heartbeats exist
    assert "pending first run" in body


def test_settings_active_renders_pause_button_no_banner(tmp_path, monkeypatch):
    """F1+F5: an active tenant sees the Pause button and no paused banner."""
    from fastapi.testclient import TestClient
    from dashboard_app.main import app

    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    client = TestClient(app)
    cookie = _settings_signed_cookie("acme")
    resp = client.get("/settings", cookies={"wcas_session": cookie})
    assert resp.status_code == 200
    body = resp.text
    assert 'id="ap-pause-all"' in body
    assert 'id="ap-resume-all"' not in body
    assert 'id="ap-paused-banner"' not in body


def test_settings_paused_renders_resume_button_with_banner(tmp_path, monkeypatch):
    """F1+F5: when tenant_config.json:status=paused, Resume button replaces
    Pause and a status banner shows above the fieldsets."""
    import json as _json
    from fastapi.testclient import TestClient
    from dashboard_app.main import app
    from dashboard_app.services import heartbeat_store

    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    config_path = heartbeat_store.tenant_root("acme") / "tenant_config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        _json.dumps({"status": "paused", "status_updated_at": "2026-04-29T10:00:00Z"}),
        encoding="utf-8",
    )

    client = TestClient(app)
    cookie = _settings_signed_cookie("acme")
    resp = client.get("/settings", cookies={"wcas_session": cookie})
    assert resp.status_code == 200
    body = resp.text
    assert 'id="ap-resume-all"' in body
    assert 'id="ap-pause-all"' not in body
    assert 'id="ap-paused-banner"' in body
    assert "All roles are paused." in body
    assert "2026-04-29T10:00:00Z" in body
