"""Google OAuth start + callback flow tests.

Every test monkeypatches the one function that hits the network
(`oauth_api.exchange_google_code`) so no real Google calls happen.
"""

import os
from urllib.parse import parse_qs, urlparse

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")
os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
os.environ.setdefault("GOOGLE_OAUTH_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:8000/auth/oauth/google/callback")

import pytest
from fastapi.testclient import TestClient

from dashboard_app.api import oauth as oauth_api
from dashboard_app.main import app
from dashboard_app.services import credentials, sessions, validation_probe


def _tenant_cookie(tenant_id: str = "acme") -> str:
    return sessions.issue(tenant_id=tenant_id, email="owner@acme.com", role="client")


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    credentials.clear_access_token_cache()
    # Default: probe returns an honest empty-success so tests that don't
    # care about the probe don't hit the real Google token endpoint.
    monkeypatch.setattr(
        validation_probe,
        "probe_google",
        lambda tenant_id: {"ok": True, "errors": {}, "summary": {"gmail": {"email": "stub"}}},
    )
    yield
    credentials.clear_access_token_cache()


def _client() -> TestClient:
    # follow_redirects=False so we can inspect the 303 + cookies from /start.
    return TestClient(app, follow_redirects=False)


# --- /auth/oauth/google/start ----------------------------------------------


def test_start_unauthenticated_is_401_or_redirected_to_login():
    client = _client()
    resp = client.get("/auth/oauth/google/start?consent=1")
    # Global 401 handler rewrites to /auth/login 303; either way no Google URL.
    assert resp.status_code in (303, 401)
    if resp.status_code == 303:
        assert "accounts.google.com" not in resp.headers.get("location", "")


def test_start_without_consent_redirects_to_scope_preview():
    """New §0.5 behavior: the /start URL requires ?consent=1. Without it,
    the owner lands on the plain-English scope-preview screen first."""
    client = _client()
    client.cookies.set("wcas_session", _tenant_cookie())
    resp = client.get("/auth/oauth/google/start")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/auth/oauth/google/preview"


def test_start_redirects_to_google_with_correct_params():
    client = _client()
    client.cookies.set("wcas_session", _tenant_cookie())
    resp = client.get("/auth/oauth/google/start?consent=1")
    assert resp.status_code == 303
    location = resp.headers["location"]
    parsed = urlparse(location)
    assert parsed.netloc == "accounts.google.com"
    assert parsed.path == "/o/oauth2/v2/auth"
    qs = parse_qs(parsed.query)
    assert qs["client_id"][0] == "test-client-id.apps.googleusercontent.com"
    assert qs["response_type"] == ["code"]
    assert qs["access_type"] == ["offline"]
    assert qs["prompt"] == ["consent"]
    assert qs["code_challenge_method"] == ["S256"]
    assert qs["code_challenge"][0]  # non-empty
    assert qs["state"][0]
    scopes = qs["scope"][0].split()
    assert "https://www.googleapis.com/auth/gmail.modify" in scopes
    assert "https://www.googleapis.com/auth/calendar" in scopes
    assert "openid" in scopes
    # Write scopes for tier-2 account-creation tools.
    assert "https://www.googleapis.com/auth/analytics.edit" in scopes
    assert "https://www.googleapis.com/auth/webmasters" in scopes
    # Google Ads scope deferred per plan.
    assert "https://www.googleapis.com/auth/adwords" not in scopes


def test_start_sets_oauth_state_cookie():
    client = _client()
    client.cookies.set("wcas_session", _tenant_cookie())
    resp = client.get("/auth/oauth/google/start?consent=1")
    assert resp.status_code == 303
    cookie_blob = resp.cookies.get("wcas_oauth_state")
    assert cookie_blob
    # The cookie is signed + contains the same state that's in the URL.
    parsed = urlparse(resp.headers["location"])
    url_state = parse_qs(parsed.query)["state"][0]
    signed = oauth_api._oauth_state_serializer().loads(cookie_blob)
    assert signed["state"] == url_state
    assert signed["tid"] == "acme"
    assert signed["verifier"]


def test_start_returns_503_when_google_oauth_unconfigured(monkeypatch):
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    client = _client()
    client.cookies.set("wcas_session", _tenant_cookie())
    resp = client.get("/auth/oauth/google/start?consent=1")
    assert resp.status_code == 503
    assert "oauth not configured" in resp.text.lower()


# --- /auth/oauth/google/callback -------------------------------------------


def _prime_oauth_session(tenant: str = "acme") -> tuple[TestClient, str, str]:
    """Walk through /start so the test has a real state cookie + URL state."""
    client = _client()
    client.cookies.set("wcas_session", _tenant_cookie(tenant))
    resp = client.get("/auth/oauth/google/start?consent=1")
    assert resp.status_code == 303
    url_state = parse_qs(urlparse(resp.headers["location"]).query)["state"][0]
    cookie_blob = resp.cookies.get("wcas_oauth_state")
    assert cookie_blob
    client.cookies.set("wcas_oauth_state", cookie_blob, path="/auth/oauth/")
    return client, url_state, cookie_blob


def test_callback_happy_path_stores_refresh_token(monkeypatch, tmp_path):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    client, state, _cookie = _prime_oauth_session()

    captured = {}

    def fake_exchange(code: str, verifier: str):
        captured["code"] = code
        captured["verifier"] = verifier
        return {
            "access_token": "ya29.access-token",
            "refresh_token": "1//0g-refresh-token",
            "expires_in": 3599,
            "scope": " ".join(
                [
                    "openid",
                    "https://www.googleapis.com/auth/gmail.modify",
                    "https://www.googleapis.com/auth/calendar",
                ]
            ),
            "token_type": "Bearer",
        }

    monkeypatch.setattr(oauth_api, "exchange_google_code", fake_exchange)
    resp = client.get(f"/auth/oauth/google/callback?code=abc123&state={state}")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/activate?connected=google"
    assert captured["code"] == "abc123"
    assert captured["verifier"]  # non-empty

    cred = credentials.load("acme", "google")
    assert cred is not None
    assert cred["refresh_token"] == "1//0g-refresh-token"
    assert "https://www.googleapis.com/auth/gmail.modify" in cred["scopes"]
    # Probe (stubbed ok=True) should have promoted roles to "connected"
    # and marked the credential as validated.
    assert cred["validation_status"] == "ok"
    from dashboard_app.services import activation_state
    assert activation_state.role_step("acme", "gbp") == "connected"
    assert activation_state.role_step("acme", "seo") == "connected"
    assert activation_state.role_step("acme", "reviews") == "connected"


def test_callback_probe_failure_leaves_rings_at_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    client, state, _cookie = _prime_oauth_session()

    monkeypatch.setattr(oauth_api, "exchange_google_code", lambda c, v: {
        "access_token": "ya29.x",
        "refresh_token": "1//0g-broken",
        "expires_in": 3599,
        "scope": "https://www.googleapis.com/auth/gmail.modify",
    })
    # Override the autouse probe mock: this test simulates a probe that fails.
    monkeypatch.setattr(
        validation_probe,
        "probe_google",
        lambda tenant_id: {"ok": False, "errors": {"access_token": "refresh revoked"}, "summary": {}},
    )

    resp = client.get(f"/auth/oauth/google/callback?code=abc&state={state}")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/activate?connected=google"

    cred = credentials.load("acme", "google")
    assert cred is not None
    assert cred["validation_status"] == "broken"
    from dashboard_app.services import activation_state
    # Rings stayed at credentials (did not advance to connected).
    assert activation_state.role_step("acme", "gbp") == "credentials"
    assert activation_state.role_step("acme", "seo") == "credentials"


def test_callback_probe_raising_does_not_kill_redirect(monkeypatch, tmp_path):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    client, state, _cookie = _prime_oauth_session()

    monkeypatch.setattr(oauth_api, "exchange_google_code", lambda c, v: {
        "access_token": "ya29.x",
        "refresh_token": "1//0g-x",
        "expires_in": 3599,
        "scope": "",
    })

    def explode(tenant_id):
        raise RuntimeError("probe implementation bug")

    monkeypatch.setattr(validation_probe, "probe_google", explode)

    resp = client.get(f"/auth/oauth/google/callback?code=abc&state={state}")
    # Redirect still happens; credential still stored.
    assert resp.status_code == 303
    assert resp.headers["location"] == "/activate?connected=google"
    cred = credentials.load("acme", "google")
    assert cred is not None
    assert cred["validation_status"] == "broken"


def test_callback_rejects_mismatched_state(monkeypatch):
    client, _good_state, _cookie = _prime_oauth_session()
    resp = client.get("/auth/oauth/google/callback?code=abc&state=not-the-real-state")
    assert resp.status_code == 400
    assert "mismatch" in resp.text.lower()


def test_callback_rejects_missing_cookie(monkeypatch):
    client = _client()
    client.cookies.set("wcas_session", _tenant_cookie())
    resp = client.get("/auth/oauth/google/callback?code=abc&state=whatever")
    assert resp.status_code == 400
    assert "cookie" in resp.text.lower()


def test_callback_rejects_tenant_mismatch(monkeypatch, tmp_path):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    # Prime an OAuth round-trip as tenant acme.
    client, state, cookie_blob = _prime_oauth_session("acme")
    # Then swap the session cookie to a different tenant before returning.
    client.cookies.set("wcas_session", _tenant_cookie("other_tenant"))
    resp = client.get(f"/auth/oauth/google/callback?code=abc&state={state}")
    assert resp.status_code == 400
    assert "tenant" in resp.text.lower()


def test_callback_user_denied_redirects_with_error_marker():
    client, _state, _cookie = _prime_oauth_session()
    resp = client.get("/auth/oauth/google/callback?error=access_denied")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/activate?connect_error=access_denied"
    # And nothing got stored.
    assert credentials.load("acme", "google") is None


def test_callback_rejects_missing_code_or_state():
    client, _state, _cookie = _prime_oauth_session()
    resp = client.get("/auth/oauth/google/callback")
    assert resp.status_code == 400
    assert "missing" in resp.text.lower()


def test_callback_returns_502_when_token_exchange_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    client, state, _cookie = _prime_oauth_session()

    def fake_exchange(code: str, verifier: str):
        raise oauth_api.ProviderOAuthError("HTTP 400: invalid_grant")

    monkeypatch.setattr(oauth_api, "exchange_google_code", fake_exchange)
    resp = client.get(f"/auth/oauth/google/callback?code=abc&state={state}")
    assert resp.status_code == 502
    # No credential should have been written.
    assert credentials.load("acme", "google") is None


def test_callback_handles_missing_refresh_token(monkeypatch, tmp_path):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    client, state, _cookie = _prime_oauth_session()

    def fake_exchange(code: str, verifier: str):
        # Google sometimes omits refresh_token when the user has already granted.
        return {"access_token": "ya29.x", "expires_in": 3599, "scope": ""}

    monkeypatch.setattr(oauth_api, "exchange_google_code", fake_exchange)
    resp = client.get(f"/auth/oauth/google/callback?code=abc&state={state}")
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/activate?connect_error=no_refresh")
    assert credentials.load("acme", "google") is None


# --- POST /api/activation/connect/{provider} -------------------------------


def test_connect_start_url_for_google():
    client = _client()
    client.cookies.set("wcas_session", _tenant_cookie())
    resp = client.post("/api/activation/connect/google")
    assert resp.status_code == 200
    assert resp.json() == {"oauth_start_url": "/auth/oauth/google/start"}


def test_connect_start_url_unauthenticated_is_401():
    client = _client()
    resp = client.post("/api/activation/connect/google")
    assert resp.status_code == 401


def test_connect_start_url_unsupported_provider_is_501():
    client = _client()
    client.cookies.set("wcas_session", _tenant_cookie())
    resp = client.post("/api/activation/connect/meta")
    assert resp.status_code == 501
    assert resp.json()["error"] == "provider_not_supported"
