"""Tests for POST /api/activation/chat router.

Mocks activation_agent.run_turn so the Anthropic SDK is never exercised.
"""

import os

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import pytest
from fastapi.testclient import TestClient

from dashboard_app.main import app
from dashboard_app.services import sessions


def _signed_cookie(tenant_id: str = "acme") -> str:
    return sessions.issue(tenant_id=tenant_id, email="owner@acme.com", role="client")


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    # Reset the rate limiter between tests.
    from dashboard_app.api import activation_chat as ac
    ac.activation_chat_limiter._buckets.clear()
    yield


def _fake_turn(events=None, reached_idle=True):
    return {
        "events": events or [{"role": "assistant", "text": "ok"}],
        "reached_idle": reached_idle,
        "usage": {"input_tokens": 5, "output_tokens": 3, "usd": 0.0004},
    }


# ---------------------------------------------------------------------------


def test_chat_requires_session():
    client = TestClient(app)
    resp = client.post("/api/activation/chat", json={"message": "hi"})
    assert resp.status_code == 401


def test_chat_rejects_empty_message(monkeypatch):
    from dashboard_app.agents import activation_agent
    monkeypatch.setattr(activation_agent, "run_turn", lambda *a, **kw: _fake_turn())

    client = TestClient(app, cookies={"wcas_session": _signed_cookie()})
    resp = client.post("/api/activation/chat", json={"message": ""})
    assert resp.status_code == 422


def test_chat_happy_path_returns_events_and_rings(monkeypatch):
    from dashboard_app.agents import activation_agent
    monkeypatch.setattr(
        activation_agent, "run_turn",
        lambda *a, **kw: _fake_turn(events=[
            {"role": "tool", "name": "fetch_site_facts", "ok": True, "summary": "fetched foo.com (200)"},
            {"role": "assistant", "text": "Got your site."},
        ]),
    )

    client = TestClient(app, cookies={"wcas_session": _signed_cookie()})
    resp = client.post("/api/activation/chat", json={"message": "hi"})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Events carried through.
    roles = [e.get("role") for e in body["events"]]
    assert "tool" in roles
    assert "assistant" in roles
    # Ring grid reflects the configured roster (9 generic WCAS pipelines).
    from dashboard_app.services.roster import ACTIVATION_ROSTER
    assert len(body["rings"]) == len(ACTIVATION_ROSTER)
    # Reached idle
    assert body["reached_idle"] is True
    # Usage carried through
    assert body["usage"]["input_tokens"] == 5


def test_chat_reset_true_clears_session_first(monkeypatch):
    from dashboard_app.agents import activation_agent

    reset_calls = []
    monkeypatch.setattr(
        activation_agent, "reset_session",
        lambda tid, **kw: reset_calls.append(tid) or True,
    )
    turn_calls = []
    def fake_run_turn(tid, msg, **kw):
        turn_calls.append((tid, msg))
        return _fake_turn()
    monkeypatch.setattr(activation_agent, "run_turn", fake_run_turn)

    client = TestClient(app, cookies={"wcas_session": _signed_cookie()})
    resp = client.post(
        "/api/activation/chat",
        json={"message": "start over", "reset": True},
    )
    assert resp.status_code == 200
    assert reset_calls == ["acme"]
    assert turn_calls == [("acme", "start over")]


def test_chat_rate_limit_returns_429(monkeypatch):
    from dashboard_app.agents import activation_agent
    monkeypatch.setattr(activation_agent, "run_turn", lambda *a, **kw: _fake_turn())

    client = TestClient(app, cookies={"wcas_session": _signed_cookie()})
    # 20 messages allowed per 5 min per tenant. 21st must 429.
    for i in range(20):
        r = client.post("/api/activation/chat", json={"message": f"hi {i}"})
        assert r.status_code == 200, f"req {i} failed: {r.text}"
    r21 = client.post("/api/activation/chat", json={"message": "one more"})
    assert r21.status_code == 429


def test_chat_strips_message_whitespace(monkeypatch):
    from dashboard_app.agents import activation_agent

    recorded: list[str] = []
    def fake_run_turn(tid, msg, **kw):
        recorded.append(msg)
        return _fake_turn()
    monkeypatch.setattr(activation_agent, "run_turn", fake_run_turn)

    client = TestClient(app, cookies={"wcas_session": _signed_cookie()})
    resp = client.post(
        "/api/activation/chat",
        json={"message": "   looks right   "},
    )
    assert resp.status_code == 200
    assert recorded == ["looks right"]
