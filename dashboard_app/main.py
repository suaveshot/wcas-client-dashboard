"""
WCAS Client Dashboard - FastAPI entrypoint.

Day 2 added: magic-link auth, signed session cookie, tenant-resolving
middleware, global exception handler, real /api/pipelines + /api/brand,
tenant-scoped heartbeat receiver. Day 1 preview route stays for demo.
"""

import logging
import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from .api import activation_chat as activation_chat_api
from .api import activation_panel as activation_panel_api
from .api import activation_simulate as activation_simulate_api
from .api import ask as ask_api
from .api import ask_global as ask_global_api
from .api import attention as attention_api
from .api import auth as auth_api
from .api import brand as brand_api
from .api import goals as goals_api
from .api import heartbeat as heartbeat_api
from .api import oauth as oauth_api
from .api import outgoing as outgoing_api
from .api import activation_samples as activation_samples_api
from .api import activation_screenshot as activation_screenshot_api
from .api import activation_terms as activation_terms_api
from .api import pipelines as pipelines_api
from .api import receipts as receipts_api
from .api import recs as recs_api
from .api import settings as settings_api
from .api import tenant as tenant_api
from .services import activation_state, activity_feed, clients_repo, credentials, errors, goals as goals_svc, home_context, outgoing_queue, recs_store, role_detail, roster, security_headers, seeded_recs, telemetry, tenant_ctx, tenant_prefs, validation_probe
from .services.tenant_ctx import current_session, require_tenant

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
TEMPLATES_DIR = APP_DIR / "templates"

app = FastAPI(
    title="WCAS Client Dashboard",
    description="Agency-level client activation + live automation telemetry.",
    version="0.7.8",
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

templates = Jinja2Templates(directory=TEMPLATES_DIR)
auth_api.attach_templates(templates)
oauth_api.attach_templates(templates)

# Security headers middleware. Registered first so it wraps the outer
# response - FastAPI evaluates middleware stack in reverse registration order.
app.middleware("http")(security_headers.security_headers_middleware)

# Session middleware runs on every request and attaches request.state.session.
app.middleware("http")(tenant_ctx.resolve_session_middleware)

app.include_router(auth_api.router)
app.include_router(pipelines_api.router)
app.include_router(brand_api.router)
app.include_router(heartbeat_api.router)
app.include_router(oauth_api.router)
app.include_router(activation_chat_api.router)
app.include_router(activation_panel_api.router)
app.include_router(activation_simulate_api.router)
app.include_router(activation_terms_api.router)
app.include_router(activation_samples_api.router)
app.include_router(activation_screenshot_api.router)
app.include_router(ask_api.router)
app.include_router(ask_global_api.router)
app.include_router(attention_api.router)
app.include_router(goals_api.router)
app.include_router(outgoing_api.router)
app.include_router(receipts_api.router)
app.include_router(recs_api.router)
app.include_router(settings_api.router)
app.include_router(tenant_api.router)


# --- Exception handlers ------------------------------------------------------


_STATUS_COPY: dict[int, dict[str, str]] = {
    403: {
        "title": "Not allowed",
        "heading": "Not allowed",
        "body": (
            "You don't have access to that page. If this looks wrong, email "
            "sam@westcoastautomationsolutions.com."
        ),
    },
    404: {
        "title": "Not found",
        "heading": "Nothing here",
        "body": "That page moved or never existed. Head back home and try again.",
    },
    405: {
        "title": "Method not allowed",
        "heading": "That request type isn't supported here",
        "body": "Head back home and try again.",
    },
    410: {
        "title": "Gone",
        "heading": "That's gone now",
        "body": "The page or resource you tried has been removed. Head back home.",
    },
    429: {
        "title": "Slow down",
        "heading": "Slow down a moment",
        "body": (
            "Too many requests in a short window. Wait fifteen seconds, then "
            "try again."
        ),
    },
}


def _login_redirect(request: Request) -> RedirectResponse:
    """Redirect to /auth/login, preserving the originally-requested path.

    Owners deep-linked to /goals/abc lose context if we drop the path on
    the floor. Append ?next=<current path+query> when it's an internal
    GET; auth.py::_safe_next validates on the read side. Only attaches
    next= for GET requests on non-auth paths to avoid loops.
    """
    from urllib.parse import quote
    path = request.url.path
    if request.method == "GET" and not path.startswith("/auth/"):
        next_param = path
        if request.url.query:
            next_param = f"{path}?{request.url.query}"
        url = f"/auth/login?next={quote(next_param, safe='/')}"
    else:
        url = "/auth/login"
    return RedirectResponse(url=url, status_code=303)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    # JSON clients (/api/*) get a JSON body; humans get the branded page.
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
    if exc.status_code == 401:
        return _login_redirect(request)
    status_copy = _STATUS_COPY.get(exc.status_code)
    if status_copy is not None:
        return templates.TemplateResponse(
            request,
            "placeholder.html",
            {
                "title": status_copy["title"],
                "heading": status_copy["heading"],
                "body": status_copy["body"],
            },
            status_code=exc.status_code,
        )
    # Truly unmapped status codes still get the chrome, with the code in the title
    # so we never ship the unbranded <h1>{code}</h1> fallback to a real owner.
    # The exception detail is preserved in the body when present so OAuth callbacks
    # and similar surfaces can still surface "missing code or state" / "tenant
    # mismatch" / etc. context to the user.
    detail = (exc.detail or "").strip() if isinstance(exc.detail, str) else ""
    body_text = (
        detail if detail else
        "We hit an unexpected response on that request. Head back home, "
        "and email the team if it keeps happening."
    )
    return templates.TemplateResponse(
        request,
        "placeholder.html",
        {
            "title": f"Error {exc.status_code}",
            "heading": "Something went sideways",
            "body": body_text,
        },
        status_code=exc.status_code,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "invalid request", "detail": exc.errors()}, status_code=422)
    # Browser form posts get a branded page instead of raw JSON.
    return templates.TemplateResponse(
        request,
        "placeholder.html",
        {
            "title": "That didn't go through",
            "heading": "Something didn't quite line up",
            "body": (
                "We couldn't process that submission. Head back and double-check "
                "the fields, then try again."
            ),
        },
        status_code=422,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    error_id = errors.new_error_id()
    errors.log_error(error_id, exc, request.url.path)
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            {"error": "internal error", "error_id": error_id},
            status_code=500,
        )
    return templates.TemplateResponse(
        request, "error.html", {"error_id": error_id}, status_code=500
    )


# --- Public routes -----------------------------------------------------------


def _post_login_target(sess: dict) -> str:
    """Where a just-authenticated user should land.

    Clients whose activation hasn't been marked complete go to /activate
    (onboarding flow). Everyone else (including admins) lands on /dashboard.
    The /admin operator view was punted post-hackathon.
    """
    if sess.get("rl") == "admin":
        return "/dashboard"
    tid = sess.get("tid") or ""
    if tid and not activation_state.is_complete(tid):
        return "/activate"
    return "/dashboard"


@app.get("/", response_class=HTMLResponse)
async def landing(request: Request) -> HTMLResponse:
    """Public landing. Authenticated users are bounced to their dashboard."""
    sess = current_session(request)
    if sess:
        return RedirectResponse(url=_post_login_target(sess), status_code=303)
    index_path = STATIC_DIR / "index.html"
    return HTMLResponse(index_path.read_text(encoding="utf-8"))


@app.get("/healthz")
async def healthz() -> JSONResponse:
    """Container health probe. Docker + UptimeRobot hit this."""
    return JSONResponse(
        {"status": "ok", "version": app.version},
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
        },
    )


@app.get("/auth/dev-login")
async def dev_login(tenant: str = "americal_patrol") -> RedirectResponse:
    """Dev-only session shortcut. 404s in production.

    Lets a dev get a logged-in session without running Airtable + magic
    link email locally. Never reachable when PRODUCTION=true.
    """
    import re as _re
    from .services import sessions as _sessions

    if os.getenv("PRODUCTION", "false").lower() == "true":
        raise HTTPException(status_code=404, detail="not found")
    if not _re.match(r"^[a-z0-9][a-z0-9_-]{0,63}$", tenant):
        raise HTTPException(status_code=400, detail="invalid tenant slug")
    cookie_value = _sessions.issue(
        tenant_id=tenant, email=f"{tenant}@dev.local", role="client"
    )
    response = RedirectResponse(url="/activate", status_code=303)
    response.set_cookie(value=cookie_value, **_sessions.cookie_kwargs())
    return response


@app.post("/auth/judge")
async def judge_demo_login() -> RedirectResponse:
    """Hackathon judge bypass: mint a session as the pre-seeded Riverbend
    Barbershop demo tenant and drop the judge directly onto /dashboard.

    The tenant is fully populated by `scripts/seed_judge_demo.py`: 7 roles
    activated, ~35 receipts, 5 pending drafts, 3 recommendations, 1 goal.
    Judges can browse every surface (Cmd-K Ask, /approvals, role drill-down,
    receipts drawer) without doing any onboarding.

    Gated by JUDGE_DEMO=true. Default off post-judging so a public POST
    can no longer mint a real session into the riverbend_barbershop tenant.
    """
    if os.getenv("JUDGE_DEMO", "false").lower() != "true":
        raise HTTPException(status_code=404, detail="not found")

    from .services import (
        activation_state as _astate,
        audit_log as _audit,
        sessions as _sessions,
    )

    tenant_id = "riverbend_barbershop"
    email = "judge@claudejudge.com"
    role = "client"

    cookie_value = _sessions.issue(tenant_id=tenant_id, email=email, role=role)
    landing = "/dashboard" if _astate.is_complete(tenant_id) else "/activate"

    _audit.record(
        tenant_id=tenant_id,
        event="judge_demo_session_issued",
        ok=True,
        actor_email=email,
    )

    response = RedirectResponse(url=landing, status_code=303)
    response.set_cookie(value=cookie_value, **_sessions.cookie_kwargs())
    return response


# Ring grid roster lives in services/roster.py so main.py, api/activation_chat.py,
# and anything else that renders the 7-ring grid share one source of truth.


@app.get("/activate", response_class=HTMLResponse)
async def activate_page(request: Request, tenant_id: str = Depends(require_tenant)):
    """Activation wizard: 45/55 chat-left + ring-grid-right layout.

    Two gates run before rendering, in order:
      1. §0.5 TOS gate - redirect to /activate/terms if the tenant has not
         clicked through the current TOS version yet.
      2. §0 completion-lock - if the tenant's row already has
         `Onboarding Completed At` set, redirect to a branded "onboarding
         closed" page.

    Admin sessions bypass both (Sam can always re-enter for demos).
    Airtable lookup failures fail open so a transient hiccup doesn't block
    a legitimate session.
    """
    session = current_session(request)
    role = getattr(session, "role", "client") if session else "client"
    client_record = None
    if role != "admin":
        try:
            client_record = clients_repo.find_by_tenant_id(tenant_id)
        except RuntimeError:
            client_record = None

        # §0.5 TOS gate.
        if client_record is not None and not clients_repo.has_accepted_tos_version(client_record):
            return RedirectResponse(url="/activate/terms", status_code=303)

        # §0 completion-lock.
        if client_record is not None and clients_repo.onboarding_completed_at(client_record):
            return templates.TemplateResponse(
                request,
                "onboarding_closed.html",
                {
                    "tenant_id": tenant_id,
                    "heading": "Onboarding is closed for this account",
                    "message": (
                        "Your activation wizard already finished. Everything is already "
                        "connected and the automations are running. If you want to reconfigure "
                        "any of them, send a note and I'll walk you through it."
                    ),
                },
            )

    role_slugs = roster.role_slugs()
    rings_by_slug = {r["slug"]: r for r in activation_state.ring_view(tenant_id, role_slugs)}
    roster_with_state = [
        {**role, **rings_by_slug.get(role["slug"], {"step": None, "percent_complete": 0.0})}
        for role in roster.ACTIVATION_ROSTER
    ]
    google_cred = credentials.load(tenant_id, "google")
    probe_summary = validation_probe.load_result(tenant_id, "google")
    connected_hint = request.query_params.get("connected", "")
    connect_error = request.query_params.get("connect_error", "")
    return templates.TemplateResponse(
        request,
        "activate.html",
        {
            "tenant_id": tenant_id,
            "tenant_name": tenant_id.replace("_", " ").title(),
            "roster": roster_with_state,
            "google_connected": google_cred is not None,
            "google_validation_status": (google_cred or {}).get("validation_status", ""),
            "probe_summary": probe_summary,
            "connected_hint": connected_hint,
            "connect_error": connect_error,
        },
    )


@app.get("/api/activation/state")
async def activation_state_api(tenant_id: str = Depends(require_tenant)) -> JSONResponse:
    """JSON state for the activate page's poll-after-OAuth flow."""
    role_slugs = roster.role_slugs()
    rings_by_slug = {r["slug"]: r for r in activation_state.ring_view(tenant_id, role_slugs)}
    rings = [
        {**role, **rings_by_slug.get(role["slug"], {"step": None, "percent_complete": 0.0})}
        for role in roster.ACTIVATION_ROSTER
    ]
    google_cred = credentials.load(tenant_id, "google")
    return JSONResponse({
        "rings": rings,
        "google_connected": google_cred is not None,
        "google_validation_status": (google_cred or {}).get("validation_status", ""),
        "probe_summary": validation_probe.load_result(tenant_id, "google"),
    })


def _demo_gate_open() -> bool:
    """Both /demo cinematics are gated post-judging.

    Set JUDGE_DEMO=true on the VPS .env when recording marketing footage,
    handing the link to a portfolio reviewer, etc. Default off so a real
    owner who lands on /demo/dashboard via search engine cannot see the
    synthetic Riverbend Barbershop data.
    """
    return os.getenv("JUDGE_DEMO", "false").lower() == "true"


@app.get("/demo")
async def demo_root() -> RedirectResponse:
    """Ergonomic shortcut: /demo always lands on the activation cinematic."""
    if not _demo_gate_open():
        raise HTTPException(status_code=404, detail="not found")
    return RedirectResponse(url="/demo/activation", status_code=303)


@app.get("/demo/activation", response_class=HTMLResponse)
async def demo_activation(request: Request) -> HTMLResponse:
    """Five-scene scripted activation cinematic from Claude Design.

    Standalone prototype, no auth, no tenant. Used for the hackathon
    submission video. Backend untouched; the page is pure CSS+JS.
    """
    if not _demo_gate_open():
        raise HTTPException(status_code=404, detail="not found")
    return templates.TemplateResponse(request, "demo_activation.html", {})


@app.get("/demo/dashboard", response_class=HTMLResponse)
async def demo_dashboard(request: Request) -> HTMLResponse:
    """Six-scene scripted dashboard cinematic from Claude Design.

    Standalone prototype, no auth, no tenant. Speaker notes embedded
    inline in the page. Sister cinematic to /demo/activation.
    """
    if not _demo_gate_open():
        raise HTTPException(status_code=404, detail="not found")
    return templates.TemplateResponse(request, "demo_dashboard.html", {})


@app.get("/terms", response_class=HTMLResponse)
async def terms(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "legal/terms.html", {})


@app.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "legal/privacy.html", {})


@app.get("/legal/terms", response_class=HTMLResponse)
async def legal_terms(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "legal/terms.html", {})


@app.get("/legal/privacy", response_class=HTMLResponse)
async def legal_privacy(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "legal/privacy.html", {})


@app.get("/activate/terms", response_class=HTMLResponse)
async def activate_terms_page(request: Request, tenant_id: str = Depends(require_tenant)) -> HTMLResponse:
    """Consent screen shown once per tenant before /activate renders."""
    error_code = request.query_params.get("e", "")
    error_msg = {
        "stale": "That version is out of date. Accept the current terms to continue.",
        "server": "Something went sideways saving that. Try again.",
    }.get(error_code, None)
    return templates.TemplateResponse(
        request,
        "activate/terms.html",
        {
            "tos_version": clients_repo.CURRENT_TOS_VERSION,
            "error": error_msg,
        },
    )


# --- Sidebar nav stubs (full surfaces ship Day 3-4) --------------------------


def _sidebar_stub(request: Request, title: str, body: str) -> HTMLResponse:
    sess = current_session(request)
    if sess is None and os.getenv("PREVIEW_MODE", "false").lower() != "true":
        return _login_redirect(request)
    return templates.TemplateResponse(
        request,
        "placeholder.html",
        {"title": title, "heading": title, "body": body},
    )


@app.get("/roles", response_class=HTMLResponse)
async def roles_page(request: Request):
    sess = current_session(request)
    preview = os.getenv("PREVIEW_MODE", "false").lower() == "true"
    if sess is None and not preview:
        return _login_redirect(request)
    tenant_id = sess["tid"] if sess else "americal_patrol"

    from .services import heartbeat_store as _hb
    raw_snaps = _hb.read_all(tenant_id)
    role_rows = []
    for snap in raw_snaps:
        pid = snap.get("pipeline_id", "")
        if not pid:
            continue
        payload = snap.get("payload") or {}
        last_run_iso = payload.get("last_run") or snap.get("received_at", "")
        last_run_text, age_hours = home_context._humanize_ago(last_run_iso)
        state, _state_text, _grade, _spark = home_context._state_from_status(
            payload.get("status", ""), age_hours
        )
        role_rows.append({
            "slug": pid.replace("_", "-"),
            "name": home_context._role_display(pid),
            "state": state,
            "last_run": last_run_text,
            "last_action": payload.get("last_action") or payload.get("summary") or "",
            "run_count": int(payload.get("run_count") or 0),
        })
    role_rows.sort(key=lambda r: r["name"])

    return templates.TemplateResponse(
        request,
        "roles.html",
        {
            "tenant_id": tenant_id,
            "tenant_name": home_context._display_from_slug(tenant_id),
            "roles": role_rows,
            "prefs": tenant_prefs.read(tenant_id),
        },
    )


@app.get("/roles/{role_slug}", response_class=HTMLResponse)
async def role_detail_page(request: Request, role_slug: str):
    import re
    if not re.match(r"^[a-z0-9][a-z0-9_-]{0,63}$", role_slug):
        raise HTTPException(status_code=404, detail="role not found")

    sess = current_session(request)
    preview = os.getenv("PREVIEW_MODE", "false").lower() == "true"

    if sess is None and not preview:
        return _login_redirect(request)

    tenant_id = sess["tid"] if sess else "americal_patrol"
    ctx = role_detail.build(tenant_id=tenant_id, role_slug=role_slug)
    ctx.setdefault("tenant_name", home_context._display_from_slug(tenant_id))
    ctx.setdefault("prefs", tenant_prefs.read(tenant_id))
    return templates.TemplateResponse(request, "role_detail.html", ctx)


@app.get("/activity", response_class=HTMLResponse)
async def activity_page(request: Request):
    sess = current_session(request)
    preview = os.getenv("PREVIEW_MODE", "false").lower() == "true"
    if sess is None and not preview:
        return _login_redirect(request)
    tenant_id = sess["tid"] if sess else "americal_patrol"
    feed = activity_feed.build(tenant_id, max_rows=80)
    return templates.TemplateResponse(
        request,
        "activity.html",
        {
            "tenant_id": tenant_id,
            "tenant_name": home_context._display_from_slug(tenant_id),
            "feed": feed,
            "prefs": tenant_prefs.read(tenant_id),
        },
    )


@app.get("/recommendations", response_class=HTMLResponse)
async def recommendations_page(request: Request):
    sess = current_session(request)
    preview = os.getenv("PREVIEW_MODE", "false").lower() == "true"
    if sess is None and not preview:
        return _login_redirect(request)
    tenant_id = sess["tid"] if sess else "americal_patrol"
    is_admin = bool(sess and sess.get("rl") == "admin")

    # Prefer the most recent Opus refresh if one exists and is fresh (<48h);
    # fall back to the deterministic seeded recs so the surface is never empty.
    fresh = recs_store.read_latest(tenant_id)
    source = "opus"
    generated_at = None
    rec_model = None
    if recs_store.is_fresh(fresh):
        all_recs = list(fresh.get("recs") or [])
        generated_at = fresh.get("generated_at")
        rec_model = fresh.get("model")
    else:
        all_recs = seeded_recs.build_with_drafts(tenant_id, limit=12)
        source = "seeded"

    from .services import rec_actions as _rec_actions
    live = _rec_actions.filter_unacted(tenant_id, [r for r in all_recs if not r.get("draft")])
    drafts = [r for r in all_recs if r.get("draft")] if is_admin else []

    return templates.TemplateResponse(
        request,
        "recommendations.html",
        {
            "tenant_id": tenant_id,
            "tenant_name": home_context._display_from_slug(tenant_id),
            "live_recs": live,
            "draft_recs": drafts,
            "show_drafts": is_admin,
            "recs_source": source,
            "recs_generated_at": generated_at,
            "recs_model": rec_model,
            "prefs": tenant_prefs.read(tenant_id),
        },
    )


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    import json as _json
    from .services import heartbeat_store as _hb

    sess = current_session(request)
    preview = os.getenv("PREVIEW_MODE", "false").lower() == "true"
    if sess is None and not preview:
        return _login_redirect(request)
    tenant_id = sess["tid"] if sess else "americal_patrol"

    prefs = tenant_prefs.read(tenant_id)
    snaps = telemetry.pipelines_for(tenant_id)
    by_pid = {snap.get("pipeline_id"): snap for snap in snaps if snap.get("pipeline_id")}

    # F8: always render the canonical 7 onboarding roles, even before a tenant
    # has any heartbeats. New owners see the full safety matrix on day 0
    # instead of the chicken-and-egg "no toggles until first send" hole.
    pipelines = []
    seen: set[str] = set()
    for role in roster.ACTIVATION_ROSTER:
        pid = role["slug"]
        seen.add(pid)
        pipelines.append({
            "pipeline_id": pid,
            "display": role["name"],
            "require_approval": bool(prefs.get("require_approval", {}).get(pid, False)),
            "has_heartbeat": pid in by_pid,
        })
    # Plus any extra heartbeat-backed pipelines (e.g., AP's legacy patrol_automation)
    # that aren't in the canonical roster - never hide work the tenant is doing.
    for snap in snaps:
        pid = snap.get("pipeline_id", "")
        if not pid or pid in seen:
            continue
        pipelines.append({
            "pipeline_id": pid,
            "display": home_context._role_display(pid),
            "require_approval": bool(prefs.get("require_approval", {}).get(pid, False)),
            "has_heartbeat": True,
        })

    # F1 + F5: surface the kill-switch status so the template can render
    # the correct Pause / Resume button + a "Paused since" banner.
    tenant_status = "active"
    paused_since = None
    try:
        config_path = _hb.tenant_root(tenant_id) / "tenant_config.json"
        if config_path.exists():
            cfg = _json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(cfg, dict):
                tenant_status = str(cfg.get("status") or "active")
                paused_since = cfg.get("status_updated_at")
    except (_hb.HeartbeatError, OSError, _json.JSONDecodeError):
        pass

    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "tenant_id": tenant_id,
            "tenant_name": home_context._display_from_slug(tenant_id),
            "owner_name": (sess.get("em") or "").split("@")[0] if sess else "demo",
            "owner_email": sess.get("em", "") if sess else "demo@claudejudge.com",
            "prefs": prefs,
            "prefs_json": _json.dumps(prefs),
            "pipelines": pipelines,
            "tenant_status": tenant_status,
            "paused_since": paused_since,
        },
    )


@app.get("/goals", response_class=HTMLResponse)
async def goals_page(request: Request):
    sess = current_session(request)
    preview = os.getenv("PREVIEW_MODE", "false").lower() == "true"
    if sess is None and not preview:
        return _login_redirect(request)
    tenant_id = sess["tid"] if sess else "americal_patrol"

    data = goals_svc.read(tenant_id)
    goals_list = []
    for g in (data.get("goals") or []):
        target = float(g.get("target") or 0)
        current = float(g.get("current") or 0)
        pct = 0 if target <= 0 else max(0, min(100, int((current / target) * 100)))
        g2 = dict(g)
        g2["percent"] = pct
        goals_list.append(g2)

    return templates.TemplateResponse(
        request,
        "goals.html",
        {
            "tenant_id": tenant_id,
            "tenant_name": home_context._display_from_slug(tenant_id),
            "goals_list": goals_list,
            "can_add": len(goals_list) < goals_svc.MAX_GOALS,
            "prefs": tenant_prefs.read(tenant_id),
        },
    )


@app.get("/approvals", response_class=HTMLResponse)
async def approvals_page(request: Request):
    """Approvals inbox: pending drafts for pipelines with `Approve before send` on."""
    from datetime import datetime, timezone

    sess = current_session(request)
    preview = os.getenv("PREVIEW_MODE", "false").lower() == "true"
    if sess is None and not preview:
        return _login_redirect(request)

    tenant_id = sess["tid"] if sess else "americal_patrol"
    drafts = outgoing_queue.list_pending(tenant_id)

    now = datetime.now(timezone.utc)
    enriched = []
    for d in drafts:
        try:
            created = datetime.fromisoformat((d.get("created_at") or "").replace("Z", "+00:00"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            hours = (now - created).total_seconds() / 3600
        except (ValueError, TypeError):
            hours = 0.0
        urgency = "green" if hours < 2 else "amber" if hours < 12 else "red"
        if hours < 1:
            age_human = f"{int(hours * 60)} min ago"
        elif hours < 24:
            age_human = f"{int(hours)}h ago"
        else:
            days = int(hours // 24)
            age_human = f"{days} day{'s' if days != 1 else ''} ago"
        d2 = dict(d)
        d2["urgency"] = urgency
        d2["age_human"] = age_human
        d2["pipeline_display"] = home_context._role_display(d.get("pipeline_id", ""))
        enriched.append(d2)

    return templates.TemplateResponse(
        request,
        "approvals.html",
        {
            "tenant_id": tenant_id,
            "tenant_name": home_context._display_from_slug(tenant_id),
            "drafts": enriched,
            "pending_count": len(enriched),
            "prefs": tenant_prefs.read(tenant_id),
        },
    )


# --- Dashboard preview (kept from Day 1 for demo continuity) ---------------


def _demo_home_context() -> dict:
    """Static mock data for the Home preview route.

    Fabricated values only; no client data. Day 3/4 replaces this with
    real tenant-scoped reads once the Managed Agents land and pipelines
    have enough telemetry to summarize.
    """
    spark_up = "M0,22 L15,18 L30,20 L45,14 L60,16 L75,10 L90,12 L105,7 L120,9 L135,5 L150,7 L165,3 L180,5 L200,2"
    spark_down = "M0,6 L20,8 L40,7 L60,11 L80,9 L100,14 L120,13 L140,17 L160,16 L180,20 L200,22"
    spark_flat = "M0,14 L25,13 L50,14 L75,13 L100,14 L125,13 L150,14 L175,13 L200,14"
    spark_mixed = "M0,18 L15,15 L30,17 L45,11 L60,14 L75,8 L90,11 L105,5 L120,8 L135,12 L150,9 L165,14 L180,10 L200,13"

    return {
        "tenant_name": "Americal Patrol",
        "owner_name": "Sam Alarcon",
        "owner_initials": "SA",
        "today_date": "2026-04-22",
        "refresh_ago": "2 min ago",
        "next_refresh": "8:00 AM local",
        "pinned_roles": [
            {"slug": "reviews", "name": "Reviews", "active": True, "auto": True, "state": "active", "pulse": True},
            {"slug": "morning-reports", "name": "Morning Reports", "active": True, "auto": True, "state": "active", "pulse": False},
            {"slug": "sales-pipeline", "name": "Sales Pipeline", "active": True, "auto": True, "state": "active", "pulse": False},
        ],
        "rail_health": {"total": 14, "running": 11, "attention": 2, "error": 1, "paused": 0},
        "recent_asks": [
            {"question": "Why is Ads pacing 18% under goal?", "cost_usd": 0.0008, "ts": "2026-04-22T14:03:00+00:00"},
            {"question": "Which role saved me the most time this week?", "cost_usd": 0.0007, "ts": "2026-04-22T12:14:00+00:00"},
            {"question": "Is there anything in the sales pipeline going stale?", "cost_usd": 0.0009, "ts": "2026-04-21T17:45:00+00:00"},
        ],
        "attention": {
            "kind": "behind",
            "text": "Ads is pacing 18% under goal for the month.",
        },
        "narrative": (
            "Here's your week, Sam. Reviews and Morning Reports did the "
            "heavy lifting, with 12 new 5-stars and zero dropped DAR emails. "
            "One thing to watch is Ads pacing, and there's a recommendation "
            "queued below."
        ),
        "hero_stats": [
            {"label": "Weeks saved", "value": "12.4", "direction": "up",
             "delta_text": "+2.1 this week", "trajectory": "ok",
             "status_text": "on track",
             "verified_tip": "Calculated from 187 automated actions since Feb 3",
             "spark_path": spark_up},
            {"label": "Revenue influenced", "value": "$38,260", "direction": "up",
             "delta_text": "+$4,120 vs last month", "trajectory": "ok",
             "status_text": "on track",
             "verified_tip": "Traced to 9 deals with pipeline first-touch attribution",
             "spark_path": spark_mixed},
            {"label": "Goal progress", "value": "68%", "direction": "down",
             "delta_text": "behind pace by 7%", "trajectory": "warn",
             "status_text": "behind",
             "verified_tip": "Measured against 3 active goals set Feb 3",
             "spark_path": spark_down},
        ],
        "roles": [
            {"slug": "seo", "name": "SEO", "state": "active", "state_text": "active", "actions": 23, "influenced": "1,840", "last_run": "2 min ago", "grade": "A", "spark_path": spark_up},
            {"slug": "ads", "name": "Ads", "state": "attention", "state_text": "needs attention", "actions": 8, "influenced": "620", "last_run": "14 min ago", "grade": "C", "spark_path": spark_down},
            {"slug": "reviews", "name": "Reviews", "state": "active", "state_text": "active", "actions": 12, "influenced": "2,800", "last_run": "3 min ago", "grade": "A", "spark_path": spark_up},
            {"slug": "morning-reports", "name": "Morning Reports", "state": "active", "state_text": "active", "actions": 7, "influenced": "0", "last_run": "today at 7:00 AM", "grade": "A", "spark_path": spark_flat},
            {"slug": "blog", "name": "Blog Posts", "state": "active", "state_text": "active", "actions": 3, "influenced": "340", "last_run": "4h ago", "grade": "B", "spark_path": spark_up},
            {"slug": "sales-pipeline", "name": "Sales Pipeline", "state": "active", "state_text": "active", "actions": 56, "influenced": "12,400", "last_run": "18 min ago", "grade": "B", "spark_path": spark_mixed},
            {"slug": "social", "name": "Social Posts", "state": "active", "state_text": "active", "actions": 4, "influenced": "0", "last_run": "yesterday", "grade": "B", "spark_path": spark_flat},
            {"slug": "gbp", "name": "Google Business", "state": "error", "state_text": "error", "actions": 0, "influenced": "0", "last_run": "37 days ago", "grade": "F", "spark_path": spark_flat},
            {"slug": "website", "name": "Website", "state": "active", "state_text": "active", "actions": 2, "influenced": "0", "last_run": "2 days ago", "grade": "B", "spark_path": spark_flat},
            {"slug": "chat-widget", "name": "Chat Widget", "state": "active", "state_text": "active", "actions": 18, "influenced": "4,900", "last_run": "6 min ago", "grade": "A", "spark_path": spark_up},
            {"slug": "incident-alerts", "name": "Incident Alerts", "state": "active", "state_text": "active", "actions": 2, "influenced": "0", "last_run": "yesterday at 2:15 AM", "grade": "A", "spark_path": spark_flat},
            {"slug": "client-reports", "name": "Client Reports", "state": "active", "state_text": "active", "actions": 14, "influenced": "0", "last_run": "today at 7:00 AM", "grade": "A", "spark_path": spark_flat},
            {"slug": "watchdog", "name": "Watchdog", "state": "active", "state_text": "active", "actions": 21, "influenced": "0", "last_run": "32 sec ago", "grade": "A", "spark_path": spark_up},
            {"slug": "supervisor-reports", "name": "Supervisor Reports", "state": "paused", "state_text": "paused", "actions": 0, "influenced": "0", "last_run": "paused 2 days ago", "grade": None, "spark_path": spark_flat},
        ],
        "feed": [
            {"time": "12:42 PM", "role": "SEO", "role_slug": "seo",
             "icon_path": "M3 3h18v18H3zM16 11l-4 4-4-4M12 15V3",
             "action": "Published \"5 HVAC tips to save money in spring\" to blog",
             "link": "#", "link_text": "View blog post", "relative": "2 min ago"},
            {"time": "12:38 PM", "role": "Reviews", "role_slug": "reviews",
             "icon_path": "M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z",
             "action": "Replied to 5-star review from Jane D.",
             "link": None, "link_text": None, "relative": "6 min ago"},
            {"time": "12:30 PM", "role": "Sales Pipeline", "role_slug": "sales-pipeline",
             "icon_path": "M4 4h16v4H4zM4 12h16v4H4zM4 20h16",
             "action": "Sent follow-up touch to 7 warm leads",
             "link": "#", "link_text": "View sequence", "relative": "14 min ago"},
            {"time": "12:18 PM", "role": "Ads", "role_slug": "ads",
             "icon_path": "M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83",
             "action": "Paused underperforming headline variant in Brand campaign",
             "link": None, "link_text": None, "relative": "26 min ago"},
            {"time": "11:56 AM", "role": "Chat Widget", "role_slug": "chat-widget",
             "icon_path": "M21 11.5a8.38 8.38 0 0 1-9 8.5 8.5 8.5 0 0 1-7.6-4.5L3 21l1.5-3.4A8.38 8.38 0 0 1 3 11.5 8.5 8.5 0 0 1 11.5 3 8.38 8.38 0 0 1 21 11.5z",
             "action": "Booked discovery call for HVAC inquiry",
             "link": "#", "link_text": "View conversation", "relative": "1h ago"},
            {"time": "11:30 AM", "role": "Morning Reports", "role_slug": "morning-reports",
             "icon_path": "M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
             "action": "Sent DAR emails for 3 properties",
             "link": None, "link_text": None, "relative": "1h 30m ago"},
        ],
        "recommendations": [
            {"goal": "GROW LEADS",
             "headline": "Your morning emails go out at 7am, but customer open times cluster 6:30 to 7:30.",
             "reason": (
                 "Shifting send time to 6:45 aligns with when property "
                 "managers actually check their inbox. Industry data suggests "
                 "a 12 to 18% open-rate lift for shops your size."
             )},
            {"goal": "HEALTH",
             "headline": "Google Business has been erroring for 37 days.",
             "reason": (
                 "Root cause is an expired OAuth token. Reconnecting takes "
                 "about 2 minutes and restores review monitoring plus "
                 "post scheduling."
             )},
            {"goal": "GROW REVIEWS",
             "headline": "Ads is pacing 18% under goal this month.",
             "reason": (
                 "Nine search impressions dropped this week on high-intent "
                 "keywords. Raising the CPC cap on the Brand campaign by "
                 "$0.40 should recover the lost traffic within 4 days."
             )},
        ],
        "total_recs": 7,
    }


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    """Home surface.

    Real behaviour: session-gated, context built from live telemetry.
    PREVIEW_MODE=true keeps the Day-1 mock accessible for demo video recording
    so judges can see the surface without a real magic link.
    """
    sess = current_session(request)
    preview = os.getenv("PREVIEW_MODE", "false").lower() == "true"

    if sess is None and not preview:
        return _login_redirect(request)

    if sess is None and preview:
        # Demo path: use the hand-crafted AP mock for video recording.
        return templates.TemplateResponse(request, "home.html", _demo_home_context())

    # Real path: compose context from this tenant's live telemetry.
    tenant_id = sess["tid"]
    owner = sess.get("em", "")
    ctx = home_context.build(tenant_id=tenant_id, owner_name=owner, tenant_display="")
    ctx.setdefault("prefs", tenant_prefs.read(tenant_id))
    return templates.TemplateResponse(request, "home.html", ctx)
