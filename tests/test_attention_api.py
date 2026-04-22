"""Attention banner API tests."""

import os

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

from fastapi.testclient import TestClient

from dashboard_app.main import app
from dashboard_app.services import sessions


def _signed_cookie(tenant_id: str = "acme", email: str = "owner@acme.com") -> str:
    return sessions.issue(tenant_id=tenant_id, email=email, role="client")


def test_attention_act_rejects_unauthenticated(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    client = TestClient(app)
    resp = client.post("/api/attention/act", json={"action": "dismiss"})
    assert resp.status_code == 401


def test_attention_act_rejects_invalid_action(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    client = TestClient(app)
    cookie = _signed_cookie()
    resp = client.post(
        "/api/attention/act",
        json={"action": "explode"},
        cookies={"wcas_session": cookie},
    )
    assert resp.status_code == 400


def test_attention_act_writes_decision_row(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    client = TestClient(app)
    cookie = _signed_cookie(tenant_id="acme")

    resp = client.post(
        "/api/attention/act",
        json={"action": "dismiss"},
        cookies={"wcas_session": cookie},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # The decision should now live in the tenant's decisions.jsonl.
    log = tmp_path / "acme" / "decisions.jsonl"
    assert log.exists()
    content = log.read_text(encoding="utf-8")
    assert "attention.dismiss" in content
    assert "Dismissed" in content


def test_attention_act_accepts_all_three_actions(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    client = TestClient(app)
    cookie = _signed_cookie(tenant_id="acme")
    for action in ("apply", "dismiss", "snooze"):
        resp = client.post(
            "/api/attention/act",
            json={"action": action},
            cookies={"wcas_session": cookie},
        )
        assert resp.status_code == 200, (action, resp.text)
