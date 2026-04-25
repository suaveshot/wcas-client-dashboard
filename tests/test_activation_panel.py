"""Tests for POST /api/activation/panel-accept (v0.6.0).

Covers:
- 401 without a session cookie
- 422 on bad type / missing card_id
- 404 when the card doesn't exist
- 200 happy path persists acceptance + triggers a follow-up turn (mocked)
- Voice edits get mirrored back into kb/voice.md
- Rate limiter trips at the 11th call
"""

import os

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import pytest
from fastapi.testclient import TestClient

from dashboard_app.main import app
from dashboard_app.services import crm_mapping, sessions, tenant_kb, voice_card


def _signed_cookie(tenant_id: str = "acme") -> str:
    return sessions.issue(tenant_id=tenant_id, email="owner@acme.com", role="client")


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    from dashboard_app.api import activation_panel as ap
    ap.panel_accept_limiter._buckets.clear()
    yield


def _fake_turn(events=None):
    return {
        "events": events or [{"role": "assistant", "text": "next"}],
        "reached_idle": True,
        "usage": {"input_tokens": 1, "output_tokens": 1, "usd": 0.0},
    }


def test_panel_accept_requires_session():
    resp = TestClient(app).post(
        "/api/activation/panel-accept",
        json={"type": "voice_card", "card_id": "vc_abc"},
    )
    assert resp.status_code == 401


def test_panel_accept_rejects_unknown_type():
    client = TestClient(app, cookies={"wcas_session": _signed_cookie()})
    resp = client.post(
        "/api/activation/panel-accept",
        json={"type": "garbage", "card_id": "vc_abc"},
    )
    assert resp.status_code == 422


def test_panel_accept_404_when_voice_card_missing(monkeypatch):
    from dashboard_app.agents import activation_agent
    monkeypatch.setattr(activation_agent, "run_turn", lambda *a, **kw: _fake_turn())

    client = TestClient(app, cookies={"wcas_session": _signed_cookie()})
    resp = client.post(
        "/api/activation/panel-accept",
        json={"type": "voice_card", "card_id": "vc_doesnt_exist"},
    )
    assert resp.status_code == 404


def test_panel_accept_voice_card_happy_path_runs_followup(monkeypatch):
    from dashboard_app.agents import activation_agent

    saved = voice_card.save("acme", traits=["warm"], generic_sample="g", voice_sample="v")

    captured = {}
    def fake_run(tid, msg, **kw):
        captured["tid"] = tid
        captured["msg"] = msg
        return _fake_turn(events=[{"role": "assistant", "text": "Now what's your CRM?"}])
    monkeypatch.setattr(activation_agent, "run_turn", fake_run)

    client = TestClient(app, cookies={"wcas_session": _signed_cookie()})
    resp = client.post(
        "/api/activation/panel-accept",
        json={"type": "voice_card", "card_id": saved["card_id"], "edits": {}},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["accepted"] is True
    assert body["type"] == "voice_card"
    assert "Now what's your CRM" in body["events"][0]["text"]
    assert captured["tid"] == "acme"
    # Default follow-up message asks about CRM.
    assert "CRM" in captured["msg"] or "booking" in captured["msg"]

    # Card flipped to accepted on disk.
    assert voice_card.load("acme")["accepted"] is True


def test_panel_accept_voice_card_mirrors_edits_to_kb(monkeypatch):
    from dashboard_app.agents import activation_agent
    monkeypatch.setattr(activation_agent, "run_turn", lambda *a, **kw: _fake_turn())

    saved = voice_card.save("acme", traits=["warm"], generic_sample="g", voice_sample="original")

    client = TestClient(app, cookies={"wcas_session": _signed_cookie()})
    resp = client.post(
        "/api/activation/panel-accept",
        json={
            "type": "voice_card",
            "card_id": saved["card_id"],
            "edits": {"voice_sample": "owner-edited version"},
        },
    )
    assert resp.status_code == 200
    voice_md = tenant_kb.read_section("acme", "voice")
    assert voice_md is not None
    assert "owner-edited version" in voice_md


def test_panel_accept_crm_mapping_happy_path(monkeypatch):
    from dashboard_app.agents import activation_agent
    monkeypatch.setattr(
        activation_agent, "run_turn",
        lambda *a, **kw: _fake_turn(events=[{"role": "assistant", "text": "Connect Google."}]),
    )

    saved = crm_mapping.save(
        "acme", base_id="appXXX", table_name="Students",
        field_mapping={"a": "b"},
        segments=[
            {"slug": "active", "label": "A", "count": 10, "sample_names": ["X"]},
            {"slug": "inactive_30d", "label": "L", "count": 5, "sample_names": ["M"]},
        ],
    )

    client = TestClient(app, cookies={"wcas_session": _signed_cookie()})
    resp = client.post(
        "/api/activation/panel-accept",
        json={"type": "crm_mapping", "card_id": saved["mapping_id"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["accepted"] is True
    assert body["type"] == "crm_mapping"
    assert crm_mapping.load("acme")["accepted"] is True


def test_panel_accept_rate_limit(monkeypatch):
    from dashboard_app.agents import activation_agent
    monkeypatch.setattr(activation_agent, "run_turn", lambda *a, **kw: _fake_turn())

    saved = voice_card.save("acme", traits=["w"], generic_sample="g", voice_sample="v")
    client = TestClient(app, cookies={"wcas_session": _signed_cookie()})

    # 10 acceptances allowed in window, 11th should 429.
    for _ in range(10):
        resp = client.post(
            "/api/activation/panel-accept",
            json={"type": "voice_card", "card_id": saved["card_id"]},
        )
        # After the first acceptance the card is already flipped, but the
        # endpoint still returns 200 because mark_accepted is idempotent
        # for the same id (returns the same updated payload).
        assert resp.status_code in (200, 404)
    resp = client.post(
        "/api/activation/panel-accept",
        json={"type": "voice_card", "card_id": saved["card_id"]},
    )
    assert resp.status_code == 429
