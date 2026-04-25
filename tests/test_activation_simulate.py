"""Tests for POST /api/activation/simulate-customer (v0.6.0).

Mocks sample_outputs.generate_for_pipeline so no real Opus call fires.
"""

import os

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import pytest
from fastapi.testclient import TestClient

from dashboard_app.main import app
from dashboard_app.services import crm_mapping, sessions


def _signed_cookie(tenant_id: str = "acme") -> str:
    return sessions.issue(tenant_id=tenant_id, email="owner@acme.com", role="client")


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    from dashboard_app.api import activation_simulate as asim
    asim.simulate_limiter._buckets.clear()
    yield


def _seed_mapping():
    crm_mapping.save(
        "acme", base_id="appXXX", table_name="Students",
        field_mapping={"a": "b"},
        segments=[
            {"slug": "active", "label": "A", "count": 10, "sample_names": ["X"]},
            {"slug": "inactive_30d", "label": "L", "count": 5,
             "sample_names": ["Maria Sanchez", "Juan Diaz"]},
        ],
    )


def test_simulate_requires_session():
    resp = TestClient(app).post("/api/activation/simulate-customer")
    assert resp.status_code == 401


def test_simulate_409_without_crm_mapping():
    client = TestClient(app, cookies={"wcas_session": _signed_cookie()})
    resp = client.post("/api/activation/simulate-customer")
    assert resp.status_code == 409
    assert "CRM mapping" in resp.json()["error"] or "wizard" in resp.json()["error"]


def test_simulate_happy_path(monkeypatch):
    _seed_mapping()
    captured = {}
    def fake_generate(tenant_id, slug, *, template_vars=None, persist=True, **kw):
        captured["tenant_id"] = tenant_id
        captured["slug"] = slug
        captured["template_vars"] = template_vars
        captured["persist"] = persist
        return {
            "title": "Re-engagement draft for Maria",
            "body_markdown": "Subject: We miss you, Maria\n\nHola Maria...",
            "preview": "Warm re-engagement",
            "status": "ok",
            "usd": 0.002,
            "citations": [
                {"kind": "voice", "source": "voice"},
                {"kind": "data", "source": "last_engagement"},
            ],
        }
    from dashboard_app.services import sample_outputs
    monkeypatch.setattr(sample_outputs, "generate_for_pipeline", fake_generate)

    client = TestClient(app, cookies={"wcas_session": _signed_cookie()})
    resp = client.post("/api/activation/simulate-customer")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Maria Sanchez"
    assert body["days_inactive"] == 37
    assert "Maria" in body["body_markdown"]
    assert len(body["citations"]) == 2
    # Endpoint passed correct vars to the generator
    assert captured["slug"] == "live_simulation"
    assert captured["template_vars"]["name"] == "Maria Sanchez"
    assert captured["template_vars"]["days_inactive"] == 37
    assert captured["persist"] is False  # transient simulation, not saved


def test_simulate_rate_limit(monkeypatch):
    _seed_mapping()
    from dashboard_app.services import sample_outputs
    monkeypatch.setattr(
        sample_outputs, "generate_for_pipeline",
        lambda *a, **kw: {"title": "x", "body_markdown": "y", "preview": "z",
                          "status": "ok", "usd": 0.0, "citations": []},
    )

    client = TestClient(app, cookies={"wcas_session": _signed_cookie()})
    resp1 = client.post("/api/activation/simulate-customer")
    assert resp1.status_code == 200
    resp2 = client.post("/api/activation/simulate-customer")
    assert resp2.status_code == 429
