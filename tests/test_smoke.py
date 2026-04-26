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


def test_dev_login_issues_session(monkeypatch):
    monkeypatch.setenv("PRODUCTION", "false")
    fresh = TestClient(app)  # Fresh client so the issued cookie doesn't leak.
    r = fresh.get("/auth/dev-login", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/activate"
    assert "wcas_session=" in r.headers.get("set-cookie", "")


def test_dev_login_404_in_production(monkeypatch):
    monkeypatch.setenv("PRODUCTION", "true")
    fresh = TestClient(app)
    r = fresh.get("/auth/dev-login", follow_redirects=False)
    assert r.status_code == 404


def test_dev_login_rejects_invalid_tenant(monkeypatch):
    monkeypatch.setenv("PRODUCTION", "false")
    fresh = TestClient(app)
    r = fresh.get("/auth/dev-login?tenant=../escape", follow_redirects=False)
    assert r.status_code == 400


def test_judge_demo_redirects_when_tenant_unseeded(tmp_path, monkeypatch):
    """With an empty TENANT_ROOT the judge tenant has no activation file,
    so the route falls back to /activate. Confirms the route mints a session
    and that the redirect logic mirrors the magic-link verify flow."""
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    fresh = TestClient(app)
    r = fresh.post("/auth/judge", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/activate"
    assert "wcas_session=" in r.headers.get("set-cookie", "")


def test_judge_demo_redirects_to_dashboard_when_seeded(tmp_path, monkeypatch):
    """Once the tenant is seeded with mark_complete, the route lands on
    /dashboard - which is the production behaviour after seed_judge_demo.py
    has run."""
    from dashboard_app.services import activation_state

    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    activation_state.mark_complete("riverbend_barbershop", note="test seed")

    fresh = TestClient(app)
    r = fresh.post("/auth/judge", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/dashboard"


def test_judge_demo_session_is_riverbend_barbershop(tmp_path, monkeypatch):
    from dashboard_app.services import sessions

    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    fresh = TestClient(app)
    r = fresh.post("/auth/judge", follow_redirects=False)
    assert r.status_code == 303
    cookie = fresh.cookies.get("wcas_session")
    assert cookie, "judge route did not set wcas_session cookie"
    payload = sessions.verify(cookie)
    assert payload is not None
    assert payload["tid"] == "riverbend_barbershop"
    assert payload["rl"] == "client"


def test_judge_demo_get_returns_405():
    r = client.get("/auth/judge", follow_redirects=False)
    assert r.status_code == 405


def test_activate_requires_auth():
    # /activate is behind require_tenant as of Day 4. Anonymous hits
    # land on /auth/login via the 401 redirect handler.
    r = client.get("/activate", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/auth/login"


def test_activate_renders_wizard_for_authed_tenant(tmp_path, monkeypatch):
    from dashboard_app.services import sessions

    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    # /activate now runs a TOS gate + completion-lock. Admin role bypasses
    # both so the render test can assert raw template content.
    cookie = sessions.issue(tenant_id="acme", email="owner@acme.com", role="admin")
    authed = TestClient(app)
    authed.cookies.set("wcas_session", cookie)
    r = authed.get("/activate")
    assert r.status_code == 200
    # v0.6.0: locked agent identity copy (voice + personalization pivot).
    assert "I learn your voice and your data" in r.text
    # Intro carousel ships with the wizard (4 slides describing the flow).
    assert "data-activate-intro" in r.text
    assert "First, I read your website" in r.text
    # Connect button routes through the scope-preview screen first (§0.5).
    assert "/auth/oauth/google/preview" in r.text
    # No vendor names leaked into rendered HTML.
    for banned in ("Claude", "Opus", "Anthropic", "OpenAI", "GPT"):
        assert banned not in r.text, f"{banned!r} leaked into /activate"


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


def test_unknown_route_returns_branded_404_html_for_humans():
    r = client.get("/does-not-exist", follow_redirects=False)
    assert r.status_code == 404
    assert "Nothing here" in r.text
    assert "application/json" not in r.headers.get("content-type", "")


def test_unknown_api_route_returns_json_404():
    r = client.get("/api/does-not-exist", follow_redirects=False)
    assert r.status_code == 404
    body = r.json()
    assert "error" in body


def test_role_detail_renders_empty_state_without_heartbeats(tmp_path, monkeypatch):
    import os as _os
    _os.environ["PREVIEW_MODE"] = "true"
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    r = client.get("/roles/patrol", follow_redirects=False)
    assert r.status_code == 200
    assert "Morning Reports" in r.text or "Patrol" in r.text
    assert "queued for its first run" in r.text


def test_role_detail_rejects_unsafe_slug():
    r = client.get("/roles/..%2Fetc%2Fpasswd", follow_redirects=False)
    assert r.status_code == 404


def test_sidebar_stubs_render_when_previewing():
    import os as _os
    _os.environ["PREVIEW_MODE"] = "true"
    for path, needle in [
        ("/roles", "Roles"),
        ("/activity", "Activity"),
        ("/recommendations", "Recommendations"),
        ("/settings", "Settings"),
    ]:
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 200, path
        assert needle in r.text


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
    """Brand rule enforcement: em dashes forbidden in committed HTML + CSS.

    Skips: .venv, __pycache__, node_modules (third-party deps), and any
    directory under "hackathon demo video/" which is local-only video
    project artifacts not deployed.
    """
    import pathlib

    em_dash = "—"
    root = pathlib.Path(__file__).resolve().parent.parent
    skip_segments = (".venv", "__pycache__", "node_modules", "hackathon demo video")
    for ext in ("*.html", "*.css", "*.py", "*.md"):
        for path in root.rglob(ext):
            path_str = str(path)
            if any(seg in path_str for seg in skip_segments):
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
