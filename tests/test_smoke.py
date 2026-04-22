"""Day 1 smoke tests — landing renders, healthz returns ok, placeholder routes respond."""

import os

os.environ.setdefault("HEARTBEAT_SHARED_SECRET", "test-secret-not-real")

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


def test_pipelines_api_placeholder():
    r = client.get("/api/pipelines")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "scaffold"


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

    # Correct header -> 200
    r = client.post(
        "/api/heartbeat",
        json={"pipeline_id": "test"},
        headers={"X-Heartbeat-Secret": os.environ["HEARTBEAT_SHARED_SECRET"]},
    )
    assert r.status_code == 200
    assert r.json()["received"] is True


def test_terms_page():
    r = client.get("/terms")
    assert r.status_code == 200
    assert "Terms" in r.text


def test_privacy_page():
    r = client.get("/privacy")
    assert r.status_code == 200
    assert "Privacy" in r.text


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

    routes = ["/", "/activate", "/terms", "/privacy", "/dashboard"]
    for route in routes:
        r = client.get(route)
        assert r.status_code == 200, f"{route} returned {r.status_code}"
        for word in banned:
            assert word not in r.text, f"{word!r} leaked into rendered HTML at {route}"
