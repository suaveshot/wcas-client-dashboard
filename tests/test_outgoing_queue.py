"""Outgoing queue + approvals API tests."""

import os

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import pytest
from fastapi.testclient import TestClient

from dashboard_app.main import app
from dashboard_app.services import outgoing_queue, receipts, sessions


def _signed_cookie(tenant_id: str = "acme") -> str:
    return sessions.issue(tenant_id=tenant_id, email="owner@acme.com", role="client")


def test_enqueue_and_list_pending(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    entry = outgoing_queue.enqueue(
        tenant_id="acme",
        pipeline_id="sales_pipeline",
        channel="email",
        recipient_hint="jane@example.com",
        subject="Follow up",
        body="Hi Jane, checking in.",
    )
    assert entry["status"] == "pending"
    pending = outgoing_queue.list_pending("acme")
    assert len(pending) == 1
    assert pending[0]["id"] == entry["id"]


def test_enqueue_rejects_vendor_leak(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    with pytest.raises(outgoing_queue.OutgoingError):
        outgoing_queue.enqueue(
            tenant_id="acme",
            pipeline_id="sales_pipeline",
            channel="email",
            recipient_hint="jane@example.com",
            subject="x",
            body="Powered by Claude to help you",
        )


def test_approve_moves_to_archive_and_writes_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    entry = outgoing_queue.enqueue(
        tenant_id="acme",
        pipeline_id="sales_pipeline",
        channel="email",
        recipient_hint="jane@example.com",
        subject="Test subject",
        body="Hello Jane.",
    )
    result = outgoing_queue.approve("acme", entry["id"])
    assert result["status"] == "approved"
    assert outgoing_queue.list_pending("acme") == []

    rcpts = receipts.list_for_pipeline("acme", "sales_pipeline")
    assert len(rcpts) == 1
    assert rcpts[0]["subject"] == "Test subject"


def test_edit_rewrites_body_before_send(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    entry = outgoing_queue.enqueue(
        tenant_id="acme",
        pipeline_id="sales_pipeline",
        channel="email",
        recipient_hint="jane@example.com",
        subject="Test",
        body="Original body.",
    )
    result = outgoing_queue.approve("acme", entry["id"], edited_body="Rewritten by owner.")
    assert result["status"] == "edited"
    rcpts = receipts.list_for_pipeline("acme", "sales_pipeline")
    assert rcpts[0]["body"] == "Rewritten by owner."


def test_skip_archives_with_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    entry = outgoing_queue.enqueue(
        tenant_id="acme",
        pipeline_id="social",
        channel="post",
        recipient_hint="Facebook",
        subject="Weekly tip",
        body="Friendly reminder text.",
    )
    outgoing_queue.skip("acme", entry["id"], reason="voice was off")
    assert outgoing_queue.list_pending("acme") == []


def test_api_outgoing_requires_session(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    client = TestClient(app)
    resp = client.get("/api/outgoing/pending")
    assert resp.status_code == 401


def test_api_outgoing_full_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    entry = outgoing_queue.enqueue(
        tenant_id="acme",
        pipeline_id="sales_pipeline",
        channel="email",
        recipient_hint="jane@example.com",
        subject="Hi",
        body="Body.",
    )
    client = TestClient(app)
    cookie = _signed_cookie("acme")

    resp = client.get("/api/outgoing/pending", cookies={"wcas_session": cookie})
    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"]["pending"] == 1
    assert len(data["drafts"]) == 1

    resp = client.post(
        f"/api/outgoing/{entry['id']}/approve",
        json={},
        cookies={"wcas_session": cookie},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"

    resp = client.get("/api/outgoing/pending", cookies={"wcas_session": cookie})
    assert resp.json()["summary"]["pending"] == 0


def test_summary_urgency_buckets(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    import json
    from datetime import datetime, timedelta, timezone
    from dashboard_app.services import heartbeat_store

    # Seed drafts with back-dated timestamps so we hit green / amber / red
    root = heartbeat_store.tenant_root("acme") / "outgoing"
    root.mkdir(parents=True, exist_ok=True)
    pending = root / "pending.jsonl"
    now = datetime.now(timezone.utc)
    with pending.open("w", encoding="utf-8") as fh:
        for offset_hours, name in [(0.25, "g"), (6, "a"), (30, "r")]:
            entry = {
                "id": f"draft-{name}",
                "created_at": (now - timedelta(hours=offset_hours)).isoformat(),
                "pipeline_id": "sales_pipeline",
                "channel": "email",
                "recipient_hint": "x",
                "subject": "s",
                "body": "b",
                "status": "pending",
            }
            fh.write(json.dumps(entry) + "\n")

    summary = outgoing_queue.summary("acme")
    assert summary["pending"] == 3
    assert summary["green"] == 1
    assert summary["amber"] == 1
    assert summary["red"] == 1


# ---------------------------------------------------------------------------
# W3: API approve endpoint dispatches via services.dispatch
# (closes audits/phase0_approvals.md::F1)
# ---------------------------------------------------------------------------


def test_api_approve_dispatches_to_known_handler(tmp_path, monkeypatch):
    """Approve a reviews-pipeline draft via API and confirm dispatch.deliver_approved
    fires (DRY_RUN=true so the reference handler logs instead of hitting GBP)."""
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    monkeypatch.setenv("DISPATCH_DRY_RUN", "true")
    entry = outgoing_queue.enqueue(
        tenant_id="acme",
        pipeline_id="reviews",
        channel="gbp_review_reply",
        recipient_hint="Maria",
        subject="Reply",
        body="Thank you Maria.",
    )
    client = TestClient(app)
    cookie = _signed_cookie("acme")
    resp = client.post(
        f"/api/outgoing/{entry['id']}/approve",
        json={},
        cookies={"wcas_session": cookie},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "approved"
    assert payload["dispatch"]["ok"] is True
    assert payload["dispatch"]["status"] == "delivered"
    assert payload["dispatch"]["result"]["dry_run"] is True


def test_api_approve_returns_no_dispatcher_for_unregistered_pipeline(tmp_path, monkeypatch):
    """Pipelines without a registered handler still get the queue approval
    (the audit trail is honest: owner approved). The dispatch outcome is
    surfaced as no_dispatcher so the FE can warn that nothing was sent yet."""
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    entry = outgoing_queue.enqueue(
        tenant_id="acme",
        pipeline_id="sales_pipeline",  # not in OUTGOING_HANDLERS yet
        channel="email",
        recipient_hint="x@y.com",
        subject="x",
        body="Body.",
    )
    client = TestClient(app)
    cookie = _signed_cookie("acme")
    resp = client.post(
        f"/api/outgoing/{entry['id']}/approve",
        json={},
        cookies={"wcas_session": cookie},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "approved"
    assert payload["dispatch"]["ok"] is False
    assert payload["dispatch"]["reason"] == "no_dispatcher"


def test_api_approve_marks_failed_when_handler_raises(tmp_path, monkeypatch):
    """Handler raising DispatchError flips archived.jsonl status to
    approved_send_failed for the durable record."""
    import json as _json
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))

    from dashboard_app.services import dispatch, heartbeat_store

    def _boom(tenant_id, payload):
        raise dispatch.DispatchError("simulated GBP outage")

    monkeypatch.setitem(dispatch.OUTGOING_HANDLERS, "reviews", _boom)

    entry = outgoing_queue.enqueue(
        tenant_id="acme",
        pipeline_id="reviews",
        channel="gbp_review_reply",
        recipient_hint="Maria",
        subject="Reply",
        body="Thank you Maria.",
    )
    client = TestClient(app)
    cookie = _signed_cookie("acme")
    resp = client.post(
        f"/api/outgoing/{entry['id']}/approve",
        json={},
        cookies={"wcas_session": cookie},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["dispatch"]["ok"] is False
    assert "simulated GBP outage" in payload["dispatch"]["reason"]

    archive = heartbeat_store.tenant_root("acme") / "outgoing" / "archived.jsonl"
    rows = [_json.loads(line) for line in archive.read_text(encoding="utf-8").splitlines() if line.strip()]
    target = next(r for r in rows if r["id"] == entry["id"])
    assert target["status"] == "approved_send_failed"
    assert "simulated GBP outage" in target["dispatch_error"]


def test_api_approve_skips_when_tenant_paused(tmp_path, monkeypatch):
    """Tenant paused -> dispatch.deliver_approved short-circuits with
    reason=tenant_paused. The queue entry still archives as approved
    (the owner did approve), but nothing actually sends."""
    import json as _json
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))

    from dashboard_app.services import heartbeat_store

    config_path = heartbeat_store.tenant_root("acme") / "tenant_config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(_json.dumps({"status": "paused"}), encoding="utf-8")

    entry = outgoing_queue.enqueue(
        tenant_id="acme",
        pipeline_id="reviews",
        channel="gbp_review_reply",
        recipient_hint="Maria",
        subject="Reply",
        body="Thank you Maria.",
    )
    client = TestClient(app)
    cookie = _signed_cookie("acme")
    resp = client.post(
        f"/api/outgoing/{entry['id']}/approve",
        json={},
        cookies={"wcas_session": cookie},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["dispatch"]["ok"] is False
    assert payload["dispatch"]["reason"] == "tenant_paused"
