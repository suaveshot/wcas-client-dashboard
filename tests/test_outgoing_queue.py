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
