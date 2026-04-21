"""Day 1 smoke tests — landing renders, healthz returns ok, placeholder routes respond."""

from fastapi.testclient import TestClient

from dashboard_app.main import app

client = TestClient(app)


def test_landing_renders():
    r = client.get("/")
    assert r.status_code == 200
    assert "WestCoast Automation Solutions" in r.text
    assert "DM Serif Display" in r.text  # font preload present


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


def test_heartbeat_endpoint_placeholder():
    r = client.post("/api/heartbeat", json={"pipeline_id": "test"})
    assert r.status_code == 200
    body = r.json()
    assert body["received"] is True


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
