"""Smoke tests spanning Day 1 scaffold + Day 2 auth boundaries."""

import os

os.environ.setdefault("HEARTBEAT_SHARED_SECRET", "test-secret-not-real")
os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

from fastapi.testclient import TestClient

from dashboard_app.main import app

client = TestClient(app)


def test_landing_renders():
    r = client.get("/")
    assert r.status_code == 200
    assert "WestCoast Automation Solutions" in r.text
    assert "DM+Serif+Display" in r.text  # Google Fonts link uses plus-encoding


def test_healthz_ok():
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"


def test_activate_placeholder():
    r = client.get("/activate")
    assert r.status_code == 200
    assert "Day 3" in r.text


def test_pipelines_api_requires_auth():
    # No session cookie -> 401, JSON body (not HTML).
    r = client.get("/api/pipelines")
    assert r.status_code == 401
    body = r.json()
    assert "error" in body


def test_brand_api_requires_auth():
    r = client.get("/api/brand")
    assert r.status_code == 401


def test_heartbeat_requires_secret():
    # No header at all -> 401
    r = client.post("/api/heartbeat", json={"pipeline_id": "test"})
    assert r.status_code == 401

    # Wrong header -> 401
    r = client.post(
        "/api/heartbeat",
        json={"pipeline_id": "test"},
        headers={"X-Heartbeat-Secret": "wrong"},
    )
    assert r.status_code == 401

    # Correct header, no tenant -> 200 but not stored (backwards-compat branch)
    r = client.post(
        "/api/heartbeat",
        json={"pipeline_id": "test"},
        headers={"X-Heartbeat-Secret": os.environ["HEARTBEAT_SHARED_SECRET"]},
    )
    assert r.status_code == 200
    assert r.json()["received"] is True


def test_heartbeat_with_tenant_writes_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    r = client.post(
        "/api/heartbeat",
        json={
            "pipeline_id": "patrol_automation",
            "status": "ok",
            "last_run": "2026-04-22T07:00:00Z",
            "summary": "3 DARs drafted",
        },
        headers={
            "X-Heartbeat-Secret": os.environ["HEARTBEAT_SHARED_SECRET"],
            "X-Tenant-Id": "americal_patrol",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["received"] is True
    assert body["stored"] is True
    snapshot = tmp_path / "americal_patrol" / "state_snapshot" / "patrol_automation.json"
    assert snapshot.exists()


def test_heartbeat_rejects_unsafe_pipeline_id():
    r = client.post(
        "/api/heartbeat",
        json={"pipeline_id": "../../etc/passwd", "status": "ok"},
        headers={
            "X-Heartbeat-Secret": os.environ["HEARTBEAT_SHARED_SECRET"],
            "X-Tenant-Id": "americal_patrol",
        },
    )
    assert r.status_code == 400


def test_terms_page():
    r = client.get("/terms")
    assert r.status_code == 200
    assert "Terms" in r.text


def test_privacy_page():
    r = client.get("/privacy")
    assert r.status_code == 200
    assert "Privacy" in r.text


def test_auth_login_page_renders():
    r = client.get("/auth/login")
    assert r.status_code == 200
    assert "Sign in" in r.text
    assert "email" in r.text.lower()


def test_auth_request_shows_neutral_page_for_unknown_email():
    # Even an unknown email redirects to check-inbox; we never leak existence.
    r = client.post(
        "/auth/request",
        data={"email": "nobody-in-crm@example.com"},
        follow_redirects=False,
    )
    # Could be 200 (template) or 303 depending on rate-limit/air state; accept either.
    assert r.status_code in (200, 303)
    if r.status_code == 200:
        assert "Check your inbox" in r.text or "check your inbox" in r.text.lower()


def test_auth_verify_bad_token_redirects_to_login():
    r = client.get("/auth/verify?token=definitely-not-real", follow_redirects=False)
    assert r.status_code == 303
    assert "/auth/login" in r.headers.get("location", "")


def test_session_middleware_lets_valid_cookie_through():
    from dashboard_app.services import sessions

    cookie = sessions.issue(tenant_id="test_tenant", email="test@example.com", role="client")
    name = sessions.cookie_kwargs()["key"]

    r = client.get("/api/pipelines", cookies={name: cookie})
    assert r.status_code == 200
    body = r.json()
    assert body["tenant_id"] == "test_tenant"
    assert "pipelines" in body


def test_no_em_dashes_in_source():
    """Brand rule enforcement: em dashes forbidden in committed HTML + CSS."""
    import pathlib

    em_dash = "—"
    root = pathlib.Path(__file__).resolve().parent.parent
    for ext in ("*.html", "*.css", "*.py", "*.md"):
        for path in root.rglob(ext):
            if ".venv" in str(path) or "__pycache__" in str(path):
                continue
            if path.name == "test_smoke.py":
                continue  # this file references the char for the check
            text = path.read_text(encoding="utf-8", errors="replace")
            assert em_dash not in text, f"em dash found in {path}"


def test_no_llm_vendor_in_rendered_html():
    """
    Brand rule: client-facing HTML must never mention Claude, Opus, Anthropic,
    or other LLM vendor names. The spark glyph + generic verbs carry the
    assistant identity. Internal docs (plan files, READMEs) are exempt and
    live outside the routes hit here.
    """
    os.environ["PREVIEW_MODE"] = "true"  # enable /dashboard for the scan
    banned = ("Claude", "Opus 4.7", "Opus 4.6", "Anthropic", "OpenAI", "GPT-")

    routes = ["/", "/activate", "/terms", "/privacy", "/dashboard", "/auth/login"]
    for route in routes:
        r = client.get(route, follow_redirects=False)
        assert r.status_code in (200, 303), f"{route} returned {r.status_code}"
        if r.status_code != 200:
            continue
        for word in banned:
            assert word not in r.text, f"{word!r} leaked into rendered HTML at {route}"
