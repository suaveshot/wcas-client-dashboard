"""Global 'ask your business' tests."""

import json
import os

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

from fastapi.testclient import TestClient

from dashboard_app.main import app
from dashboard_app.services import global_ask, heartbeat_store, opus, sessions


def _signed_cookie(tenant_id: str = "acme") -> str:
    return sessions.issue(tenant_id=tenant_id, email="owner@acme.com", role="client")


def test_compose_context_handles_empty_tenant(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    ctx = global_ask.compose_context("brand_new")
    assert "prompt" in ctx
    assert "sources" in ctx
    # Brand-new tenant: prompt mentions no heartbeats + no goals pinned.
    assert "no heartbeats received" in ctx["prompt"]
    assert "no goals pinned" in ctx["prompt"]


def test_compose_context_includes_heartbeats(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    heartbeat_store.write_snapshot("acme", "patrol", {
        "status": "ok",
        "last_run": "2026-04-22T07:00:00+00:00",
        "summary": "Sent DARs to Harbor Lights and Manhattan Plaza.",
        "state_summary": {"dars_sent": 2, "errors": 0},
    })
    heartbeat_store.write_snapshot("acme", "gbp", {
        "status": "error",
        "last_run": "2026-03-16T12:00:00+00:00",
        "summary": "OAuth token expired",
    })
    ctx = global_ask.compose_context("acme")
    assert "patrol" in ctx["prompt"]
    assert "gbp" in ctx["prompt"]
    assert "Sent DARs" in ctx["prompt"]
    assert "OAuth token expired" in ctx["prompt"]
    labels = [s["label"] for s in ctx["sources"]]
    assert "patrol" in labels
    assert "gbp" in labels


def test_compose_context_includes_goals_when_present(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    tenant_root = tmp_path / "acme"
    tenant_root.mkdir(parents=True, exist_ok=True)
    (tenant_root / "goals.json").write_text(json.dumps({
        "goals": [
            {"title": "Get 20 new 5-star reviews", "metric": "reviews", "target": 20, "timeframe": "90d"}
        ]
    }), encoding="utf-8")
    ctx = global_ask.compose_context("acme")
    assert "Get 20 new 5-star reviews" in ctx["prompt"]
    assert any(s["source"] == "goals" for s in ctx["sources"])


def test_ask_global_requires_session(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    client = TestClient(app)
    resp = client.post("/api/ask_global", json={"question": "why is ads pacing weird"})
    assert resp.status_code == 401


def test_ask_global_rate_limits(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    # Patch opus so we never hit the network
    from dashboard_app.services import rate_limit
    monkeypatch.setattr(opus, "chat", lambda **kw: opus.OpusResult(
        text="fine",
        model="claude-haiku-4-5",
        input_tokens=10,
        output_tokens=5,
        usd=0.0001,
        stop_reason="end_turn",
    ))
    # Reset the limiter so other tests don't leak state
    rate_limit.ask_global_limiter._buckets.clear()

    client = TestClient(app)
    cookie = _signed_cookie("acme")

    for i in range(2):
        resp = client.post(
            "/api/ask_global",
            json={"question": f"tell me something interesting {i}"},
            cookies={"wcas_session": cookie},
        )
        assert resp.status_code == 200, resp.text

    # Third in the window -> 429
    resp = client.post(
        "/api/ask_global",
        json={"question": "one more please"},
        cookies={"wcas_session": cookie},
    )
    assert resp.status_code == 429


def test_ask_global_returns_cited_answer(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    heartbeat_store.write_snapshot("acme2", "gbp", {
        "status": "error",
        "last_run": "2026-03-16T12:00:00+00:00",
        "summary": "OAuth token expired",
    })

    from dashboard_app.services import rate_limit
    rate_limit.ask_global_limiter._buckets.clear()

    def fake_chat(**kw):
        assert kw["kind"] == "ask_global"
        assert kw["cache_system"] is True
        assert "gbp" in kw["messages"][0]["content"]
        return opus.OpusResult(
            text="Your Google Business is offline because the OAuth token expired on March 16. Reconnect in settings to bring it back.",
            model="claude-haiku-4-5",
            input_tokens=320,
            output_tokens=48,
            usd=0.0006,
            stop_reason="end_turn",
        )
    monkeypatch.setattr(opus, "chat", fake_chat)

    client = TestClient(app)
    cookie = _signed_cookie("acme2")
    resp = client.post(
        "/api/ask_global",
        json={"question": "Why is my Google Business broken?"},
        cookies={"wcas_session": cookie},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "OAuth" in data["answer"]
    assert data["cost_usd"] > 0
    labels = [s["label"] for s in data["sources"]]
    assert "gbp" in labels


def test_ask_global_vendor_leak_gets_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    from dashboard_app.services import rate_limit
    rate_limit.ask_global_limiter._buckets.clear()

    monkeypatch.setattr(opus, "chat", lambda **kw: opus.OpusResult(
        text="Powered by Claude Opus 4.7, I can say your GBP is offline.",
        model="claude-haiku-4-5",
        input_tokens=10,
        output_tokens=5,
        usd=0.0001,
        stop_reason="end_turn",
    ))

    client = TestClient(app)
    cookie = _signed_cookie("acme3")
    resp = client.post(
        "/api/ask_global",
        json={"question": "is my GBP ok?"},
        cookies={"wcas_session": cookie},
    )
    assert resp.status_code == 200
    # Guardrail rejects vendor leak; returns canned safe answer
    assert "safe answer" in resp.json()["answer"].lower()
