"""
WCAS Client Dashboard  -  FastAPI entrypoint.

Day 1 scaffold: landing route, healthz, placeholder routes for /activate,
/api/pipelines, /api/heartbeat, /terms, /privacy. Real auth, tenant
scoping, and agent wiring ship Day 2-4.
"""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
TEMPLATES_DIR = APP_DIR / "templates"

app = FastAPI(
    title="WCAS Client Dashboard",
    description="Agency-level client activation + live automation telemetry.",
    version="0.1.0",
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
async def landing(request: Request) -> HTMLResponse:
    """Public landing  -  magic link required to access dashboard routes."""
    index_path = STATIC_DIR / "index.html"
    return HTMLResponse(index_path.read_text(encoding="utf-8"))


@app.get("/healthz")
async def healthz() -> JSONResponse:
    """Container health probe. Docker + UptimeRobot hit this."""
    return JSONResponse({"status": "ok", "version": app.version})


@app.get("/activate", response_class=HTMLResponse)
async def activate_page() -> HTMLResponse:
    """Activation flow placeholder. Real chat UI ships Day 3."""
    return HTMLResponse(
        "<!doctype html><meta charset='utf-8'>"
        "<title>Activation · WCAS</title>"
        "<link rel='stylesheet' href='/static/styles.css'>"
        "<main style='max-width:720px;margin:96px auto;padding:0 24px;'>"
        "<h1>Activation flow</h1>"
        "<p>Coming Day 3: the Opus 4.7 Activation Orchestrator will walk you through "
        "getting your purchased pipelines live in about 30 minutes.</p>"
        "</main>"
    )


@app.get("/api/pipelines")
async def api_pipelines() -> JSONResponse:
    """Placeholder. Real tenant-scoped pipeline status ships Day 2."""
    return JSONResponse({"pipelines": [], "status": "scaffold"})


@app.post("/api/heartbeat")
async def api_heartbeat(request: Request) -> JSONResponse:
    """Placeholder. Real shared-secret auth + tenant scope ships Day 2."""
    return JSONResponse({"received": True, "status": "scaffold"})


@app.get("/terms", response_class=HTMLResponse)
async def terms() -> HTMLResponse:
    """Terms of service. Real content drafted Day 5."""
    return HTMLResponse(
        "<!doctype html><meta charset='utf-8'>"
        "<title>Terms · WCAS Dashboard</title>"
        "<link rel='stylesheet' href='/static/styles.css'>"
        "<main style='max-width:720px;margin:96px auto;padding:0 24px;'>"
        "<h1>Terms of Service</h1>"
        "<p>Final content shipping Day 5. In plain English, owner to owner.</p>"
        "</main>"
    )


@app.get("/privacy", response_class=HTMLResponse)
async def privacy() -> HTMLResponse:
    """Privacy policy. Real content drafted Day 5."""
    return HTMLResponse(
        "<!doctype html><meta charset='utf-8'>"
        "<title>Privacy · WCAS Dashboard</title>"
        "<link rel='stylesheet' href='/static/styles.css'>"
        "<main style='max-width:720px;margin:96px auto;padding:0 24px;'>"
        "<h1>Privacy Policy</h1>"
        "<p>Final content shipping Day 5. What we collect, why, and how to export or delete.</p>"
        "</main>"
    )
