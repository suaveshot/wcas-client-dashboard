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
    monkeypatch.setenv("JUDGE_DEMO", "true")
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

    monkeypatch.setenv("JUDGE_DEMO", "true")
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    activation_state.mark_complete("riverbend_barbershop", note="test seed")

    fresh = TestClient(app)
    r = fresh.post("/auth/judge", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/dashboard"


def test_judge_demo_session_is_riverbend_barbershop(tmp_path, monkeypatch):
    from dashboard_app.services import sessions

    monkeypatch.setenv("JUDGE_DEMO", "true")
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


def test_judge_demo_404_when_gate_closed(tmp_path, monkeypatch):
    """Default JUDGE_DEMO=false means the route is hidden from the public
    after judging closes. Prevents a stray POST from minting a real
    riverbend_barbershop session."""
    monkeypatch.delenv("JUDGE_DEMO", raising=False)
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    fresh = TestClient(app)
    r = fresh.post("/auth/judge", follow_redirects=False)
    assert r.status_code == 404


def test_demo_routes_404_when_gate_closed(monkeypatch):
    """/demo, /demo/activation, /demo/dashboard all 404 unless JUDGE_DEMO=true.
    Prevents search-engine-landing owners from seeing synthetic demo data."""
    monkeypatch.delenv("JUDGE_DEMO", raising=False)
    fresh = TestClient(app)
    for path in ("/demo", "/demo/activation", "/demo/dashboard"):
        r = fresh.get(path, follow_redirects=False)
        assert r.status_code == 404, f"{path} should 404 with gate closed"


def test_demo_routes_open_when_gate_set(monkeypatch):
    monkeypatch.setenv("JUDGE_DEMO", "true")
    fresh = TestClient(app)
    r = fresh.get("/demo", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/demo/activation"
    r = fresh.get("/demo/activation")
    assert r.status_code == 200
    r = fresh.get("/demo/dashboard")
    assert r.status_code == 200


def test_judge_demo_get_returns_405():
    r = client.get("/auth/judge", follow_redirects=False)
    assert r.status_code == 405


def test_activate_requires_auth():
    # /activate is behind require_tenant as of Day 4. Anonymous hits
    # land on /auth/login via the 401 redirect handler. As of W2 the
    # redirect appends ?next= so the post-login round-trip lands them
    # back on /activate.
    r = client.get("/activate", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/auth/login")
    assert "next=/activate" in r.headers["location"]


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


def test_login_renders_error_codes():
    """W2.1: ?e=expired etc. should surface a friendly message on /auth/login.

    Fragments avoid apostrophes since Jinja autoescape renders them as &#39;
    in the response body.
    """
    for code, fragment in [
        ("expired", "expired"),
        ("used", "already been used"),
        ("invalid", "look right"),
        ("missing", "Sign-in link missing"),
        ("server", "wrong on our side"),
        ("incomplete", "quite set up yet"),
    ]:
        r = client.get(f"/auth/login?e={code}")
        assert r.status_code == 200, f"login GET with e={code} should still render"
        assert fragment in r.text, f"login page missing copy for e={code}: '{fragment}'"


def test_login_ignores_unknown_error_code():
    """Unknown ?e= value should render a clean form, no leaked error string."""
    r = client.get("/auth/login?e=NOT-A-REAL-CODE")
    assert r.status_code == 200
    assert "NOT-A-REAL-CODE" not in r.text


def test_protected_route_redirect_preserves_next(monkeypatch):
    """W2.2: hitting a session-gated GET unauthed should send ?next=<path>.

    Use /settings as the canary: it has no PREVIEW_MODE bypass branch,
    so the redirect path is deterministic regardless of env state.
    """
    monkeypatch.delenv("PREVIEW_MODE", raising=False)
    r = client.get("/goals", follow_redirects=False)
    assert r.status_code == 303
    location = r.headers.get("location", "")
    assert location.startswith("/auth/login?next=")
    assert "/goals" in location


def test_login_redirect_rejects_external_next():
    """_safe_next must reject scheme-relative + external URLs."""
    from dashboard_app.api.auth import _safe_next

    assert _safe_next("//evil.com/path") is None
    assert _safe_next("https://evil.com/path") is None
    assert _safe_next("javascript:alert(1)") is None
    assert _safe_next("/goals") == "/goals"
    assert _safe_next("/roles/reviews?demo=1") == "/roles/reviews?demo=1"
    assert _safe_next("") is None
    assert _safe_next(None) is None


def test_validation_error_renders_html_for_browsers():
    """W2.3: 422 on non-API route should render placeholder.html, not raw JSON."""
    # Force a validation error on the magic-link request without an email.
    r = client.post("/auth/request", data={})
    assert r.status_code == 422
    assert "quite line up" in r.text  # avoid apostrophe (Jinja auto-escapes to &#39;)
    assert "WestCoast Automation Solutions" in r.text  # branded chrome present
    assert "application/json" not in r.headers.get("content-type", "")


def test_validation_error_still_json_for_api():
    """W2.3 inverse: /api/* validation errors stay JSON for programmatic clients."""
    from dashboard_app.services import sessions

    cookie = sessions.issue(tenant_id="test_tenant", email="test@example.com", role="client")
    name = sessions.cookie_kwargs()["key"]
    # /api/goals POST requires title, metric, target, timeframe; empty body fails validation.
    r = client.post("/api/goals", json={}, cookies={name: cookie})
    assert r.status_code == 422
    assert r.headers["content-type"].startswith("application/json")
    assert "invalid request" in r.text


def test_status_403_renders_branded_page():
    """W2.4: 403 falls through StarletteHTTPException to the status-code map."""
    from fastapi import HTTPException

    @app.get("/_test/forbidden")
    async def _forbidden():
        raise HTTPException(status_code=403, detail="not allowed")

    try:
        r = client.get("/_test/forbidden")
        assert r.status_code == 403
        assert "Not allowed" in r.text
        assert "WestCoast Automation Solutions" in r.text  # placeholder.html chrome
        assert "<h1>403</h1>" not in r.text  # no unbranded fallback
    finally:
        # Best-effort cleanup; avoid leaking the test route into other tests.
        app.router.routes = [r for r in app.router.routes if getattr(r, "path", "") != "/_test/forbidden"]


def test_prefs_partial_renders_in_authed_pages(tmp_path, monkeypatch):
    """W4.1+W4.3: WCAS_PREFS injection appears on every authenticated surface
    and exposes the two safe fields (privacy_default, feed_dense_default).
    Verified via the activity page using a session cookie + a tmp tenant root.
    """
    from dashboard_app.services import sessions, heartbeat_store

    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    # Force fresh tenant_root resolution with the tmp path.
    heartbeat_store._configure_root.cache_clear() if hasattr(heartbeat_store, "_configure_root") else None

    cookie = sessions.issue(tenant_id="prefs_test", email="t@example.com", role="client")
    name = sessions.cookie_kwargs()["key"]

    r = client.get("/activity", cookies={name: cookie})
    assert r.status_code == 200
    assert "window.WCAS_PREFS" in r.text
    assert "privacy_default" in r.text
    assert "feed_dense_default" in r.text


def test_prefs_partial_falls_through_when_prefs_absent():
    """W4.1: public landing has no `prefs` in context; partial must NOT render
    a malformed script block. Use the non-prefs-aware /healthz... actually
    the public homepage is static, so use 404 placeholder which has no prefs.
    """
    r = client.get("/this-route-does-not-exist")
    assert r.status_code == 404
    assert "window.WCAS_PREFS" not in r.text


def test_activity_renders_dense_toggle(tmp_path, monkeypatch):
    """W4.5: /activity must expose .ap-feed__toggle-btn so shell.js can wire it.

    Empty feed renders only the no-activity placeholder per service-layer
    contract, so seed a heartbeat so the toggle markup is included.
    """
    from dashboard_app.services import sessions, heartbeat_store

    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    if hasattr(heartbeat_store, "_configure_root"):
        heartbeat_store._configure_root.cache_clear()
    # Seed a heartbeat directly so feed has at least one row.
    tenant_dir = tmp_path / "toggle_test" / "heartbeats"
    tenant_dir.mkdir(parents=True, exist_ok=True)
    (tenant_dir / "patrol.json").write_text(
        '{"pipeline_id":"patrol","received_at":"2026-04-28T12:00:00+00:00",'
        '"payload":{"status":"ok","summary":"Patrol completed.","last_run":"2026-04-28T12:00:00+00:00"}}',
        encoding="utf-8",
    )

    cookie = sessions.issue(tenant_id="toggle_test", email="t@example.com", role="client")
    name = sessions.cookie_kwargs()["key"]
    r = client.get("/activity", cookies={name: cookie})
    assert r.status_code == 200
    assert "ap-feed__toggle-btn" in r.text
    assert "Dense" in r.text and "Relaxed" in r.text


def test_error_html_uses_team_not_first_name():
    """W2.5: error.html must say 'the team' not just bare 'Sam', so a tenant
    who has never met Sam doesn't read the error page as cryptic."""
    import pathlib

    here = pathlib.Path(__file__).resolve().parent.parent
    text = (here / "dashboard_app/templates/error.html").read_text(encoding="utf-8")
    # Old copy was 'so Sam can look it up' + 'Fastest fix: email <sam@...>'.
    # New copy must reference 'the WCAS team' / 'the team' explicitly.
    assert "the WCAS team" in text or "the team" in text
    # Don't fully ban the name 'Sam' (the email address still contains it),
    # but make sure the bare "so Sam can look it up" friend-of-a-friend phrasing is gone.
    assert "so Sam can" not in text





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

    Catches both the literal U+2014 character AND HTML-entity encodings
    (&mdash;, &#8212;, &#x2014;) so the brand rule survives copy/paste
    from word processors that auto-encode.

    Skips: .venv, __pycache__, node_modules (third-party deps), the
    audits/ tree (which legitimately quotes the forbidden patterns when
    documenting violations), JOURNAL.md (internal change log that
    references past entity-scrub fixes), and the local-only
    "hackathon demo video/" project artifacts.
    """
    import pathlib
    import re

    forbidden_re = re.compile(r"—|&mdash;|&#8212;|&#x2014;", re.IGNORECASE)
    root = pathlib.Path(__file__).resolve().parent.parent
    skip_segments = (".venv", "__pycache__", "node_modules", "hackathon demo video", "audits")
    skip_filenames = ("test_smoke.py", "JOURNAL.md")
    for ext in ("*.html", "*.css", "*.py", "*.md"):
        for path in root.rglob(ext):
            path_str = str(path)
            if any(seg in path_str for seg in skip_segments):
                continue
            if path.name in skip_filenames:
                continue  # internal docs that legitimately reference the patterns
            text = path.read_text(encoding="utf-8", errors="replace")
            match = forbidden_re.search(text)
            assert match is None, f"em dash ({match.group()!r}) found in {path}"


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
