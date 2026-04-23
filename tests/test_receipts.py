"""Receipts store + /api/receipts tests."""

import os

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

from fastapi.testclient import TestClient

from dashboard_app.main import app
from dashboard_app.services import receipts, sessions


def _signed_cookie(tenant_id: str = "acme") -> str:
    return sessions.issue(tenant_id=tenant_id, email="owner@acme.com", role="client")


def test_append_and_list_for_pipeline(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    rid1 = receipts.append(
        tenant_id="acme",
        pipeline_id="sales_pipeline",
        channel="email",
        recipient_hint="jane@example.com",
        subject="Follow up",
        body="Hi Jane, just checking in.",
        cost_usd=0.0003,
    )
    rid2 = receipts.append(
        tenant_id="acme",
        pipeline_id="sales_pipeline",
        channel="email",
        recipient_hint="mike@example.com",
        subject="Port quote",
        body="Hi Mike, sending draft coverage options.",
    )
    rows = receipts.list_for_pipeline("acme", "sales_pipeline", limit=10)
    assert len(rows) == 2
    # Newest first
    ids = [r["id"] for r in rows]
    assert rid2 in ids and rid1 in ids
    subjects = [r["subject"] for r in rows]
    assert "Port quote" in subjects
    assert rows[0]["bytes"] > 0


def test_list_all_across_pipelines(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    receipts.append(tenant_id="acme", pipeline_id="sales_pipeline", channel="email",
                    recipient_hint="x@y.com", subject="A", body="hello")
    receipts.append(tenant_id="acme", pipeline_id="reviews", channel="reply",
                    recipient_hint="Maria G.", subject="B", body="thanks Maria")
    rows = receipts.list_all("acme")
    pids = {r["pipeline_id"] for r in rows}
    assert "sales_pipeline" in pids
    assert "reviews" in pids


def test_invalid_pipeline_id_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    rows = receipts.list_for_pipeline("acme", "../escape", limit=10)
    assert rows == []


def test_append_rejects_path_traversal_pipeline(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    import pytest
    with pytest.raises(ValueError):
        receipts.append(tenant_id="acme", pipeline_id="../escape", channel="email",
                        recipient_hint="x", subject="s", body="b")


def test_api_receipts_requires_session(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    client = TestClient(app)
    resp = client.get("/api/receipts")
    assert resp.status_code == 401
    resp2 = client.get("/api/receipts/sales_pipeline")
    assert resp2.status_code == 401


def test_api_receipts_returns_tenant_scoped_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    receipts.append(tenant_id="acme", pipeline_id="sales_pipeline", channel="email",
                    recipient_hint="x@y.com", subject="Hi", body="body text")

    client = TestClient(app)
    cookie = _signed_cookie("acme")
    resp = client.get("/api/receipts/sales_pipeline", cookies={"wcas_session": cookie})
    assert resp.status_code == 200
    data = resp.json()
    assert data["tenant_id"] == "acme"
    assert data["pipeline_id"] == "sales_pipeline"
    assert len(data["receipts"]) == 1
    assert data["receipts"][0]["subject"] == "Hi"
