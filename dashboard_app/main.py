"""
WCAS Client Dashboard  -  FastAPI entrypoint.

Day 1 scaffold: landing route, healthz, placeholder routes for /activate,
/api/pipelines, /api/heartbeat, /terms, /privacy. Real auth, tenant
scoping, and agent wiring ship Day 2-4.

All HTML rendering goes through Jinja2 with auto-escape ON by default.
No string-concatenated HTML responses, so Day 2+ additions can't accidentally
introduce XSS when user data starts flowing in.
"""

import os
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
TEMPLATES_DIR = APP_DIR / "templates"

app = FastAPI(
    title="WCAS Client Dashboard",
    description="Agency-level client activation + live automation telemetry.",
    version="0.1.0",
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

templates = Jinja2Templates(directory=TEMPLATES_DIR)


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
async def activate_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "placeholder.html",
        {
            "title": "Activation",
            "heading": "Activation flow",
            "body": (
                "Coming Day 3: the Opus 4.7 Activation Orchestrator will walk "
                "you through getting your purchased pipelines live in about 30 "
                "minutes."
            ),
        },
    )


@app.get("/api/pipelines")
async def api_pipelines() -> JSONResponse:
    """Placeholder. Real tenant-scoped pipeline status ships Day 2."""
    return JSONResponse({"pipelines": [], "status": "scaffold"})


@app.post("/api/heartbeat")
async def api_heartbeat(
    request: Request,
    x_heartbeat_secret: str | None = Header(default=None, alias="X-Heartbeat-Secret"),
) -> JSONResponse:
    """
    Accepts pipeline state pushes from Americal Patrol's PC.

    Day 1: shared-secret check is active. Real tenant resolution + storage
    ships Day 2. Without the secret (or with a wrong one) this endpoint is
    closed  -  the repo is public, so we never leave a placeholder open.
    """
    expected = os.getenv("HEARTBEAT_SHARED_SECRET", "")
    if not expected or x_heartbeat_secret != expected:
        raise HTTPException(status_code=401, detail="unauthorized")
    return JSONResponse({"received": True, "status": "scaffold"})


@app.get("/terms", response_class=HTMLResponse)
async def terms(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "placeholder.html",
        {
            "title": "Terms",
            "heading": "Terms of Service",
            "body": "Final content shipping Day 5. In plain English, owner to owner.",
        },
    )


@app.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "placeholder.html",
        {
            "title": "Privacy",
            "heading": "Privacy Policy",
            "body": "Final content shipping Day 5. What we collect, why, and how to export or delete.",
        },
    )
