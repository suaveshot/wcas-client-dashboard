"""Security header middleware tests."""

import os

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

from fastapi.testclient import TestClient

from dashboard_app.main import app


def test_html_response_has_full_header_set(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    monkeypatch.setenv("PRODUCTION", "false")
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Content-Security-Policy" in resp.headers
    assert "default-src 'self'" in resp.headers["Content-Security-Policy"]
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "camera=()" in resp.headers["Permissions-Policy"]
    # HSTS only when PRODUCTION=true
    assert "Strict-Transport-Security" not in resp.headers


def test_api_response_skips_csp_but_keeps_rest(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    monkeypatch.setenv("PRODUCTION", "false")
    client = TestClient(app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    # healthz is under /api ... actually /healthz isn't; let's use an /api/ route.
    resp = client.get("/api/pipelines")  # 401
    assert "Content-Security-Policy" not in resp.headers
    assert resp.headers["X-Frame-Options"] == "DENY"


def test_production_true_emits_hsts(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    monkeypatch.setenv("PRODUCTION", "true")
    client = TestClient(app)
    resp = client.get("/")
    assert "Strict-Transport-Security" in resp.headers
    assert "max-age=31536000" in resp.headers["Strict-Transport-Security"]
