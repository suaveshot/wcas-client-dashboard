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


# ---------------------------------------------------------------------------
# OUTGOING_HANDLERS - live wire paths
#
# Each new handler gets one happy-path (DRY_RUN) test, one live-path test
# with a fake HTTP/SMTP injector, and one failure test that exercises the
# DispatchError conversion (closes the W6 partial-method-surface lesson at
# the dispatcher boundary).
# ---------------------------------------------------------------------------


from dashboard_app.services import (  # noqa: E402
    credentials as _credentials,
    crm_mapping as _crm_mapping,
    ghl_provider as _ghl_provider,
    hubspot_provider as _hubspot_provider,
    pipedrive_provider as _pipedrive_provider,
)


class _FakeHTTPResponse:
    def __init__(self, status_code: int = 200, text: str = "", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload

    def json(self):
        return self._payload


def _seed_crm_mapping(tenant_id: str, kind: str) -> None:
    """Drop a minimal crm_mapping.json with `kind` set so _resolve_sales_kind
    finds it. crm_mapping.save() doesn't accept kind; write the file directly."""
    path = _crm_mapping._path(tenant_id)
    path.write_text(json.dumps({"kind": kind, "base_id": "x", "table_name": "y"}),
                    encoding="utf-8")


# ---------------------------------------------------------------------------
# reviews - live GBP path
# ---------------------------------------------------------------------------


def test_reviews_live_path_posts_to_gbp(tmp_path, monkeypatch):
    """Live reviews handler builds the correct review-resource URL, sends a
    Bearer token, and returns posted=True on 200."""
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    monkeypatch.delenv("DISPATCH_DRY_RUN", raising=False)

    captured: dict = {}

    def fake_post(url, *, token, json_body):
        captured["url"] = url
        captured["token"] = token
        captured["body"] = json_body
        return _FakeHTTPResponse(status_code=200, text="{}", payload={})

    monkeypatch.setattr(dispatch, "_post_gbp", fake_post)
    monkeypatch.setattr(dispatch.credentials, "access_token",
                        lambda tid, prov: "fake-google-token")

    entry = {
        "id": "draft-reviews-live-1",
        "pipeline_id": "reviews",
        "channel": "gbp_review_reply",
        "recipient_hint": "Maria",
        "subject": "(no subject)",
        "body": "Thank you Maria.",
        "metadata": {
            "location_path": "accounts/123/locations/456",
            "review_id": "rev_abc",
        },
    }
    result = dispatch.deliver_approved("acme", entry)
    assert result["ok"] is True
    assert result["status"] == "delivered"
    assert captured["url"] == (
        "https://mybusiness.googleapis.com/v4/"
        "accounts/123/locations/456/reviews/rev_abc/reply"
    )
    assert captured["token"] == "fake-google-token"
    assert captured["body"] == {"comment": "Thank you Maria."}


def test_reviews_live_path_converts_4xx_to_dispatch_error(tmp_path, monkeypatch):
    """A 4xx response from GBP flips the archived entry to
    approved_send_failed with the HTTP status surfaced."""
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    monkeypatch.delenv("DISPATCH_DRY_RUN", raising=False)

    monkeypatch.setattr(dispatch, "_post_gbp",
                        lambda url, **kw: _FakeHTTPResponse(403, text="Permission denied"))
    monkeypatch.setattr(dispatch.credentials, "access_token",
                        lambda tid, prov: "fake-token")

    enqueued = outgoing_queue.enqueue(
        tenant_id="acme",
        pipeline_id="reviews",
        channel="gbp_review_reply",
        recipient_hint="Maria",
        subject="x",
        body="Thanks",
        metadata={"location_path": "accounts/1/locations/2", "review_id": "r1"},
    )
    approved = outgoing_queue.approve("acme", enqueued["id"])

    result = dispatch.deliver_approved("acme", approved)
    assert result["ok"] is False
    assert "GBP reply rejected: HTTP 403" in result["reason"]

    archive = heartbeat_store.tenant_root("acme") / "outgoing" / "archived.jsonl"
    rows = [json.loads(line) for line in archive.read_text(encoding="utf-8").splitlines() if line.strip()]
    target = next(r for r in rows if r["id"] == approved["id"])
    assert target["status"] == "approved_send_failed"


def test_reviews_live_path_missing_review_name_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    monkeypatch.delenv("DISPATCH_DRY_RUN", raising=False)

    entry = {
        "id": "draft-reviews-bad",
        "pipeline_id": "reviews",
        "channel": "gbp_review_reply",
        "body": "Thanks",
        "metadata": {},  # no review_name, no location_path/review_id
    }
    result = dispatch.deliver_approved("acme", entry)
    assert result["ok"] is False
    assert "missing review name" in result["reason"]


def test_reviews_live_path_credential_error_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    monkeypatch.delenv("DISPATCH_DRY_RUN", raising=False)

    def boom(*a, **kw):
        raise _credentials.CredentialError("no stored credential for google")

    monkeypatch.setattr(dispatch.credentials, "access_token", boom)

    entry = {
        "id": "draft-reviews-noauth",
        "pipeline_id": "reviews",
        "channel": "gbp_review_reply",
        "body": "Thanks",
        "metadata": {"location_path": "accounts/1/locations/2", "review_id": "r1"},
    }
    result = dispatch.deliver_approved("acme", entry)
    assert result["ok"] is False
    assert "google access_token unavailable" in result["reason"]


# ---------------------------------------------------------------------------
# gbp - live localPosts path
# ---------------------------------------------------------------------------


def test_gbp_live_path_posts_to_localposts(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    monkeypatch.delenv("DISPATCH_DRY_RUN", raising=False)

    captured: dict = {}

    def fake_post(url, *, token, json_body):
        captured["url"] = url
        captured["body"] = json_body
        return _FakeHTTPResponse(200, payload={"name": "post_xyz"})

    monkeypatch.setattr(dispatch, "_post_gbp", fake_post)
    monkeypatch.setattr(dispatch.credentials, "access_token",
                        lambda tid, prov: "google-tok")

    entry = {
        "id": "draft-gbp-1",
        "pipeline_id": "gbp",
        "channel": "gbp_post",
        "subject": "GBP post: New service",
        "body": "We just launched evening patrol coverage.",
        "metadata": {
            "location_path": "accounts/aaa/locations/bbb",
            "post_kind": "STANDARD",
        },
    }
    result = dispatch.deliver_approved("acme", entry)
    assert result["ok"] is True
    assert captured["url"] == (
        "https://mybusiness.googleapis.com/v4/"
        "accounts/aaa/locations/bbb/localPosts"
    )
    assert captured["body"]["summary"] == "We just launched evening patrol coverage."
    assert captured["body"]["topicType"] == "STANDARD"
    assert captured["body"]["languageCode"] == "en-US"


def test_gbp_live_path_missing_location_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    monkeypatch.delenv("DISPATCH_DRY_RUN", raising=False)

    entry = {
        "id": "draft-gbp-bad",
        "pipeline_id": "gbp",
        "body": "x",
        "metadata": {},
    }
    result = dispatch.deliver_approved("acme", entry)
    assert result["ok"] is False
    assert "missing metadata.location_path" in result["reason"]


def test_gbp_dry_run_short_circuits(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    monkeypatch.setenv("DISPATCH_DRY_RUN", "true")

    def explode(*a, **kw):
        raise AssertionError("network must not be called in DRY_RUN")

    monkeypatch.setattr(dispatch, "_post_gbp", explode)
    monkeypatch.setattr(dispatch.credentials, "access_token", explode)

    entry = {
        "id": "draft-gbp-dry",
        "pipeline_id": "gbp",
        "body": "x",
        "metadata": {"location_path": "accounts/1/locations/2"},
    }
    result = dispatch.deliver_approved("acme", entry)
    assert result["ok"] is True
    assert result["result"]["dry_run"] is True


# ---------------------------------------------------------------------------
# sales - CRMProvider routing
# ---------------------------------------------------------------------------


class _StubProvider:
    """Records send_email/send_sms calls for assertions. Raises on demand
    via the configured exception classes."""

    def __init__(self, *, raise_email=None, raise_sms=None,
                 message_id="msg_stub"):
        self.calls: list[tuple] = []
        self._raise_email = raise_email
        self._raise_sms = raise_sms
        self._message_id = message_id

    def send_email(self, contact_id, subject, html_body, **kw):
        self.calls.append(("email", contact_id, subject, html_body))
        if self._raise_email is not None:
            raise self._raise_email
        return self._message_id

    def send_sms(self, contact_id, message, **kw):
        self.calls.append(("sms", contact_id, message))
        if self._raise_sms is not None:
            raise self._raise_sms
        return self._message_id


def test_sales_routes_email_to_ghl_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    monkeypatch.delenv("DISPATCH_DRY_RUN", raising=False)
    _seed_crm_mapping("acme", "ghl")

    stub = _StubProvider(message_id="ghl_msg_1")
    monkeypatch.setattr(_ghl_provider, "for_tenant", lambda tid: stub)

    entry = {
        "id": "draft-sales-email",
        "pipeline_id": "sales",
        "channel": "sales_cold_email",
        "subject": "Quick hello",
        "body": "Hi Maria, want to chat?",
        "metadata": {"contact_id": "ghl_contact_42", "first_name": "Maria"},
    }
    result = dispatch.deliver_approved("acme", entry)
    assert result["ok"] is True
    assert result["result"]["kind"] == "ghl"
    assert result["result"]["message_id"] == "ghl_msg_1"
    assert stub.calls == [("email", "ghl_contact_42", "Quick hello",
                           "Hi Maria, want to chat?")]


def test_sales_routes_sms_to_ghl_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    monkeypatch.delenv("DISPATCH_DRY_RUN", raising=False)
    _seed_crm_mapping("acme", "ghl")

    stub = _StubProvider(message_id="ghl_sms_1")
    monkeypatch.setattr(_ghl_provider, "for_tenant", lambda tid: stub)

    entry = {
        "id": "draft-sales-sms",
        "pipeline_id": "sales",
        "channel": "sales_cold_sms",
        "subject": "",
        "body": "Quick hello from Acme",
        "metadata": {"contact_id": "ghl_contact_99"},
    }
    result = dispatch.deliver_approved("acme", entry)
    assert result["ok"] is True
    assert stub.calls == [("sms", "ghl_contact_99", "Quick hello from Acme")]


def test_sales_routes_email_to_hubspot_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    monkeypatch.delenv("DISPATCH_DRY_RUN", raising=False)
    _seed_crm_mapping("acme", "hubspot")

    stub = _StubProvider(message_id="hs_event_1")
    monkeypatch.setattr(_hubspot_provider, "for_tenant", lambda tid: stub)

    entry = {
        "id": "draft-sales-hs",
        "pipeline_id": "sales",
        "channel": "sales_cold_email",
        "subject": "Hello",
        "body": "Email content here",
        "metadata": {"contact_id": "hs_contact_1"},
    }
    result = dispatch.deliver_approved("acme", entry)
    assert result["ok"] is True
    assert result["result"]["kind"] == "hubspot"


def test_sales_hubspot_sms_raises_dispatch_error(tmp_path, monkeypatch):
    """W6 lesson: HubSpotProvider.send_sms raises by design (no native SMS).
    The dispatcher must catch it and flip to approved_send_failed, never
    silently no-op."""
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    monkeypatch.delenv("DISPATCH_DRY_RUN", raising=False)
    _seed_crm_mapping("acme", "hubspot")

    stub = _StubProvider(
        raise_sms=_hubspot_provider.HubSpotProviderError(
            0, "HubSpot does not natively support outbound SMS."
        ),
    )
    monkeypatch.setattr(_hubspot_provider, "for_tenant", lambda tid: stub)

    enqueued = outgoing_queue.enqueue(
        tenant_id="acme",
        pipeline_id="sales",
        channel="sales_cold_sms",
        recipient_hint="(lead)",
        subject="",
        body="Hello",
        metadata={"contact_id": "hs_contact_1"},
    )
    approved = outgoing_queue.approve("acme", enqueued["id"])

    result = dispatch.deliver_approved("acme", approved)
    assert result["ok"] is False
    assert "HubSpot does not natively support outbound SMS" in result["reason"]

    archive = heartbeat_store.tenant_root("acme") / "outgoing" / "archived.jsonl"
    rows = [json.loads(line) for line in archive.read_text(encoding="utf-8").splitlines() if line.strip()]
    target = next(r for r in rows if r["id"] == approved["id"])
    assert target["status"] == "approved_send_failed"


def test_sales_routes_to_pipedrive_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    monkeypatch.delenv("DISPATCH_DRY_RUN", raising=False)
    _seed_crm_mapping("acme", "pipedrive")

    # PipedriveProvider.send_email raises by design; the dispatcher must
    # convert that to DispatchError instead of silently swallowing.
    stub = _StubProvider(
        raise_email=_pipedrive_provider.PipedriveProviderError(
            0, "Pipedrive does not expose a programmatic outbound email API."
        ),
    )
    monkeypatch.setattr(_pipedrive_provider, "for_tenant", lambda tid: stub)

    entry = {
        "id": "draft-sales-pd",
        "pipeline_id": "sales",
        "channel": "sales_cold_email",
        "subject": "Hi",
        "body": "Hello",
        "metadata": {"contact_id": "pd_contact_1"},
    }
    result = dispatch.deliver_approved("acme", entry)
    assert result["ok"] is False
    assert "Pipedrive does not expose" in result["reason"]


def test_sales_ghl_from_email_missing_raises_dispatch_error(tmp_path, monkeypatch):
    """The W6 lesson trap from ghl_provider.py:202 - send_email without a
    configured from_email raises GHLProviderError. Dispatcher converts it."""
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    monkeypatch.delenv("DISPATCH_DRY_RUN", raising=False)
    _seed_crm_mapping("acme", "ghl")

    stub = _StubProvider(
        raise_email=_ghl_provider.GHLProviderError(
            0, "from_email not configured on GHLProvider; pass at __init__"
        ),
    )
    monkeypatch.setattr(_ghl_provider, "for_tenant", lambda tid: stub)

    entry = {
        "id": "draft-sales-noemail",
        "pipeline_id": "sales",
        "channel": "sales_cold_email",
        "subject": "Hi",
        "body": "Hello",
        "metadata": {"contact_id": "c_1"},
    }
    result = dispatch.deliver_approved("acme", entry)
    assert result["ok"] is False
    assert "from_email not configured" in result["reason"]


def test_sales_no_crm_mapping_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    monkeypatch.delenv("DISPATCH_DRY_RUN", raising=False)
    # Intentionally no _seed_crm_mapping call.

    entry = {
        "id": "draft-sales-nomap",
        "pipeline_id": "sales",
        "channel": "sales_cold_email",
        "body": "Hello",
        "metadata": {"contact_id": "c_1"},
    }
    result = dispatch.deliver_approved("acme", entry)
    assert result["ok"] is False
    assert "no CRM mapping configured" in result["reason"]


def test_sales_no_credentials_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    monkeypatch.delenv("DISPATCH_DRY_RUN", raising=False)
    _seed_crm_mapping("acme", "ghl")
    monkeypatch.setattr(_ghl_provider, "for_tenant", lambda tid: None)

    entry = {
        "id": "draft-sales-nocreds",
        "pipeline_id": "sales",
        "channel": "sales_cold_email",
        "body": "Hello",
        "metadata": {"contact_id": "c_1"},
    }
    result = dispatch.deliver_approved("acme", entry)
    assert result["ok"] is False
    assert "no ghl credentials stored" in result["reason"]


def test_sales_invalid_channel_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    monkeypatch.delenv("DISPATCH_DRY_RUN", raising=False)
    _seed_crm_mapping("acme", "ghl")

    entry = {
        "id": "draft-sales-badchan",
        "pipeline_id": "sales",
        "channel": "carrier_pigeon",
        "body": "Hello",
        "metadata": {"contact_id": "c_1"},
    }
    result = dispatch.deliver_approved("acme", entry)
    assert result["ok"] is False
    assert "channel must indicate email or sms" in result["reason"]


def test_sales_dry_run_short_circuits(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    monkeypatch.setenv("DISPATCH_DRY_RUN", "true")
    # No mapping seeded; no provider stubbed; DRY_RUN must skip both checks.

    entry = {
        "id": "draft-sales-dry",
        "pipeline_id": "sales",
        "channel": "sales_cold_email",
        "body": "Hello",
        "metadata": {"contact_id": "c_1"},
    }
    result = dispatch.deliver_approved("acme", entry)
    assert result["ok"] is True
    assert result["result"]["dry_run"] is True


# ---------------------------------------------------------------------------
# email_assistant - SMTP path
# ---------------------------------------------------------------------------


def test_email_assistant_sends_via_smtp(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    monkeypatch.delenv("DISPATCH_DRY_RUN", raising=False)

    _credentials.store_paste(
        "acme",
        "gmail_app_password",
        {"email_address": "owner@acme.com", "app_password": "abcd efgh ijkl mnop"},
    )

    captured: dict = {}

    def fake_smtp(host, port, *, username, password, msg):
        captured["host"] = host
        captured["port"] = port
        captured["username"] = username
        captured["password"] = password
        captured["msg"] = msg

    monkeypatch.setattr(dispatch, "_smtp_send", fake_smtp)

    entry = {
        "id": "draft-email-1",
        "pipeline_id": "email_assistant",
        "channel": "email",
        "recipient_hint": "lead@example.com",
        "subject": "Re: Quote request",
        "body": "Hi, here is the quote you asked for.",
        "metadata": {
            "from_email": "lead@example.com",
            "in_reply_to": "<original-msg-id@example.com>",
        },
    }
    result = dispatch.deliver_approved("acme", entry)
    assert result["ok"] is True
    assert result["result"]["sent"] is True
    assert captured["host"] == "smtp.gmail.com"
    assert captured["port"] == 465
    assert captured["username"] == "owner@acme.com"
    msg = captured["msg"]
    assert msg["From"] == "owner@acme.com"
    assert msg["To"] == "lead@example.com"
    assert msg["Subject"] == "Re: Quote request"
    assert msg["In-Reply-To"] == "<original-msg-id@example.com>"
    assert msg["References"] == "<original-msg-id@example.com>"
    assert "here is the quote" in msg.get_payload(decode=True).decode("utf-8")


def test_email_assistant_smtp_failure_raises_dispatch_error(tmp_path, monkeypatch):
    import smtplib as _smtplib

    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    monkeypatch.delenv("DISPATCH_DRY_RUN", raising=False)

    _credentials.store_paste(
        "acme",
        "gmail_app_password",
        {"email_address": "owner@acme.com", "app_password": "x"},
    )

    def boom(*a, **kw):
        raise _smtplib.SMTPAuthenticationError(535, b"Username and Password not accepted")

    monkeypatch.setattr(dispatch, "_smtp_send", boom)

    entry = {
        "id": "draft-email-fail",
        "pipeline_id": "email_assistant",
        "body": "x",
        "metadata": {"from_email": "lead@example.com"},
    }
    result = dispatch.deliver_approved("acme", entry)
    assert result["ok"] is False
    assert "SMTP send failed" in result["reason"]


def test_email_assistant_missing_creds_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    monkeypatch.delenv("DISPATCH_DRY_RUN", raising=False)

    entry = {
        "id": "draft-email-nocreds",
        "pipeline_id": "email_assistant",
        "body": "x",
        "metadata": {"from_email": "lead@example.com"},
    }
    result = dispatch.deliver_approved("acme", entry)
    assert result["ok"] is False
    assert "no gmail_app_password credentials" in result["reason"]


def test_email_assistant_missing_recipient_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    monkeypatch.delenv("DISPATCH_DRY_RUN", raising=False)

    _credentials.store_paste(
        "acme",
        "gmail_app_password",
        {"email_address": "owner@acme.com", "app_password": "x"},
    )

    entry = {
        "id": "draft-email-noaddr",
        "pipeline_id": "email_assistant",
        "body": "x",
        "metadata": {},
    }
    result = dispatch.deliver_approved("acme", entry)
    assert result["ok"] is False
    assert "missing valid recipient" in result["reason"]


def test_email_assistant_dry_run_short_circuits(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    monkeypatch.setenv("DISPATCH_DRY_RUN", "true")

    def explode(*a, **kw):
        raise AssertionError("SMTP must not be called in DRY_RUN")

    monkeypatch.setattr(dispatch, "_smtp_send", explode)

    entry = {
        "id": "draft-email-dry",
        "pipeline_id": "email_assistant",
        "body": "x",
        "metadata": {"from_email": "lead@example.com"},
    }
    result = dispatch.deliver_approved("acme", entry)
    assert result["ok"] is True
    assert result["result"]["dry_run"] is True


# ---------------------------------------------------------------------------
# Pin no_dispatcher for unwired onboarding pipelines.
# blog/social/seo/chat_widget intentionally stay unwired until their
# generic run.py pipelines land. This test guards against accidentally
# adding them to OUTGOING_HANDLERS without first proving the wire path.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pipeline_id", ["blog", "social", "seo", "chat_widget"])
def test_unwired_onboarding_pipeline_returns_no_dispatcher(
    tmp_path, monkeypatch, pipeline_id,
):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    entry = {
        "id": f"draft-{pipeline_id}-1",
        "pipeline_id": pipeline_id,
        "channel": "x",
        "body": "y",
    }
    result = dispatch.deliver_approved("acme", entry)
    assert result["ok"] is False
    assert result["reason"] == "no_dispatcher"


def test_wired_onboarding_pipelines_present_in_registry():
    """Lock in the registry shape so a refactor can't silently drop one of
    the four wired handlers."""
    assert "reviews" in dispatch.OUTGOING_HANDLERS
    assert "gbp" in dispatch.OUTGOING_HANDLERS
    assert "sales" in dispatch.OUTGOING_HANDLERS
    assert "email_assistant" in dispatch.OUTGOING_HANDLERS
