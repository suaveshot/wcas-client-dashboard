"""
OAuth connection routes for per-tenant credential capture.

Flow:
    1. Client hits GET /auth/oauth/google/start (require_tenant).
    2. We generate an anti-CSRF state + PKCE verifier, sign them into
       a short-lived cookie bound to the session's tenant_id, and 302
       to Google's consent screen.
    3. Google redirects back to /auth/oauth/google/callback?code=...&state=...
    4. We verify state against the cookie, exchange code+verifier for
       tokens, persist the refresh_token via services.credentials,
       and redirect to /activate?connected=google.

Design notes:
- State cookie is SameSite=Lax (not Strict) so Google's top-level GET
  callback still carries it back. Path is scoped to /auth/oauth/.
- Cookie TTL is 5 minutes; if the user dawdles on Google's screen
  longer than that they simply re-start the flow.
- The cookie is tenant-bound: a different logged-in tenant cannot
  consume another tenant's in-flight OAuth round-trip.
- prompt=consent + access_type=offline forces Google to issue a new
  refresh_token on every approval, even if the user has approved
  before. Prevents the "no refresh_token in response" trap.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from fastapi.templating import Jinja2Templates

from ..services import activation_state, credentials, errors, scope_transparency, validation_probe
from ..services.scrubber import scrub
from ..services.tenant_ctx import require_tenant

log = logging.getLogger("dashboard.oauth")

router = APIRouter(tags=["oauth"])

_TEMPLATES: Jinja2Templates | None = None


def attach_templates(templates: Jinja2Templates) -> None:
    """Called from main.py after the Jinja environment is set up."""
    global _TEMPLATES
    _TEMPLATES = templates


def _tmpl() -> Jinja2Templates:
    if _TEMPLATES is None:
        raise RuntimeError("oauth templates not attached")
    return _TEMPLATES


GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

# Scopes enabled on the WCAS Dashboard GCP project's consent screen.
# Update both places together when adding a service. Ads scope
# (https://www.googleapis.com/auth/adwords) is intentionally absent -
# Google Ads API needs a separately approved developer token, deferred
# until post-hackathon per the plan.
GOOGLE_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    # business.manage is already read/write for GBP (locations, posts, reviews).
    "https://www.googleapis.com/auth/business.manage",
    # analytics.edit covers read + write, needed for the tier-2 create_ga4_property
    # tool. Superset of analytics.readonly, which we previously requested.
    "https://www.googleapis.com/auth/analytics.edit",
    # webmasters (not .readonly) is required to add sites to Search Console
    # for verify_gsc_domain.
    "https://www.googleapis.com/auth/webmasters",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
]

OAUTH_STATE_COOKIE_NAME = "wcas_oauth_state"
OAUTH_STATE_MAX_AGE_SECONDS = 300 # 5 minutes

# Role slugs that get advanced when a tenant completes Google OAuth.
# These are the existing AP pipelines that leverage the Google APIs we
# just got consent for. Gmail/Calendar/Ads are service-level connections
# without a dedicated pipeline yet, so they don't get their own rings.
GOOGLE_POWERED_ROLES: tuple[str, ...] = ("gbp", "seo", "reviews")


def _oauth_state_serializer() -> URLSafeTimedSerializer:
    secret = os.getenv("SESSION_SECRET", "")
    if not secret:
        raise RuntimeError("SESSION_SECRET is required for OAuth state signing")
    return URLSafeTimedSerializer(secret, salt="wcas-oauth-state-v1")


def _oauth_state_cookie_kwargs() -> dict[str, Any]:
    """Cookie options for the OAuth state cookie.

    Distinct from the session cookie: SameSite=Lax (not Strict) so the
    redirect FROM accounts.google.com carries it back; path scoped to
    /auth/oauth/ so it isn't transmitted on every page request.
    """
    secure = os.getenv("PRODUCTION", "false").lower() == "true"
    return {
        "key": OAUTH_STATE_COOKIE_NAME,
        "max_age": OAUTH_STATE_MAX_AGE_SECONDS,
        "httponly": True,
        "samesite": "lax",
        "secure": secure,
        "path": "/auth/oauth/",
    }


def _new_state_and_verifier() -> tuple[str, str, str]:
    """Return (state, verifier, challenge) for a fresh PKCE+state pair.

    state is an opaque nonce we round-trip through Google.
    verifier is the PKCE code_verifier (43-128 url-safe chars).
    challenge is base64url(sha256(verifier)) with no padding.
    """
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return state, verifier, challenge


def _google_client_config() -> tuple[str, str, str]:
    """Return (client_id, client_secret, redirect_uri) or raise."""
    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")
    client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "")
    redirect_uri = os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "")
    if not (client_id and client_secret and redirect_uri):
        raise RuntimeError(
            "GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET / "
            "GOOGLE_OAUTH_REDIRECT_URI must be set"
        )
    return client_id, client_secret, redirect_uri


# --- routes -----------------------------------------------------------------


@router.get("/auth/oauth/google/preview")
async def preview_google_oauth(
    request: Request,
    tenant_id: str = Depends(require_tenant),
):
    """Render the plain-English scope-transparency screen before Google OAuth.

    Clicking through points the browser at /auth/oauth/google/start?consent=1
    which is the only code path that actually redirects to accounts.google.com.
    """
    will_do, will_not = scope_transparency.promises_for("google", GOOGLE_SCOPES)
    return _tmpl().TemplateResponse(
        request,
        "activate/scope_preview.html",
        {
            "provider_label": scope_transparency.provider_display_name("google"),
            "will_do": will_do,
            "will_not": will_not,
            "continue_url": "/auth/oauth/google/start?consent=1",
        },
    )


@router.get("/auth/oauth/google/start")
async def start_google_oauth(
    request: Request,
    tenant_id: str = Depends(require_tenant),
) -> RedirectResponse:
    """Kick off the Google OAuth flow for the current tenant.

    Requires ?consent=1 - without it we redirect to the scope-preview
    screen first so the owner sees what the grant means in plain English.
    """
    if request.query_params.get("consent") != "1":
        return RedirectResponse(url="/auth/oauth/google/preview", status_code=303)

    try:
        client_id, _secret, redirect_uri = _google_client_config()
    except RuntimeError as exc:
        log.error("google oauth not configured: %s", exc)
        raise HTTPException(status_code=503, detail="oauth not configured") from exc

    state, verifier, challenge = _new_state_and_verifier()
    signed = _oauth_state_serializer().dumps(
        {"state": state, "verifier": verifier, "tid": tenant_id}
    )

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    url = f"{GOOGLE_AUTHORIZE_URL}?{urlencode(params)}"
    response = RedirectResponse(url=url, status_code=303)
    response.set_cookie(value=signed, **_oauth_state_cookie_kwargs())
    return response


@router.get("/auth/oauth/google/callback")
async def google_oauth_callback(
    request: Request,
    tenant_id: str = Depends(require_tenant),
) -> RedirectResponse:
    """Consume the Google redirect: verify state, exchange code, persist refresh."""
    params = request.query_params
    error = params.get("error")
    if error:
        # User denied consent (access_denied) or Google surfaced something else.
        # Land back on /activate with an error marker so the UI can speak to it.
        log.info("google oauth consent refused: %s", scrub(error))
        return _clear_state_and_redirect(f"/activate?connect_error={error}")

    code = params.get("code") or ""
    state = params.get("state") or ""
    if not code or not state:
        raise HTTPException(status_code=400, detail="missing code or state")

    cookie_blob = request.cookies.get(OAUTH_STATE_COOKIE_NAME) or ""
    if not cookie_blob:
        raise HTTPException(status_code=400, detail="oauth state cookie missing")

    try:
        signed = _oauth_state_serializer().loads(
            cookie_blob, max_age=OAUTH_STATE_MAX_AGE_SECONDS
        )
    except SignatureExpired:
        raise HTTPException(status_code=400, detail="oauth state expired") from None
    except BadSignature:
        raise HTTPException(status_code=400, detail="oauth state invalid") from None

    expected_state = signed.get("state")
    verifier = signed.get("verifier")
    bound_tid = signed.get("tid")
    if not (expected_state and verifier and bound_tid):
        raise HTTPException(status_code=400, detail="oauth state malformed")
    if not secrets.compare_digest(state, expected_state):
        raise HTTPException(status_code=400, detail="oauth state mismatch")
    if bound_tid != tenant_id:
        raise HTTPException(status_code=400, detail="oauth tenant mismatch")

    try:
        token_payload = exchange_google_code(code, verifier)
    except ProviderOAuthError as exc:
        err_id = errors.new_error_id()
        log.error("google token exchange failed err_id=%s: %s", err_id, exc)
        raise HTTPException(status_code=502, detail=f"token exchange failed ({err_id})") from exc

    refresh_token = token_payload.get("refresh_token")
    if not refresh_token:
        # Can happen if the user previously granted access and Google decided
        # not to reissue a refresh token. prompt=consent should prevent this;
        # if it still trips, surface a clear message so the user can retry
        # after revoking at myaccount.google.com/permissions.
        err_id = errors.new_error_id()
        log.error("google exchanged code but returned no refresh_token err_id=%s", err_id)
        return _clear_state_and_redirect(f"/activate?connect_error=no_refresh&e={err_id}")

    granted_scopes = (token_payload.get("scope") or "").split()
    credentials.store(
        tenant_id,
        "google",
        refresh_token=refresh_token,
        scopes=granted_scopes,
        validation_status="pending",
    )
    log.info("google oauth connected tenant=%s scopes=%d", tenant_id, len(granted_scopes))

    # Advance the Google-powered roles to 'credentials' step so the ring
    # grid reflects the connection immediately. Probe outcome below may
    # promote them further to 'connected'.
    activation_state.bulk_advance(tenant_id, list(GOOGLE_POWERED_ROLES), "credentials")

    # Fire the validation probe. Errors here must not block the redirect -
    # the ring grid just stays at 'credentials' and the UI can surface a
    # 'we couldn't verify live data yet' note.
    try:
        probe_result = validation_probe.probe_google(tenant_id)
        validation_probe.save_result(tenant_id, "google", probe_result)
        if probe_result.get("ok"):
            activation_state.bulk_advance(
                tenant_id, list(GOOGLE_POWERED_ROLES), "connected"
            )
            credentials.mark_validated(tenant_id, "google", "ok")
        else:
            credentials.mark_validated(tenant_id, "google", "broken")
    except Exception: # probe must never kill the redirect
        log.exception("probe_google raised for tenant=%s", tenant_id)
        credentials.mark_validated(tenant_id, "google", "broken")

    return _clear_state_and_redirect("/activate?connected=google")


@router.post("/api/activation/connect/{provider}")
async def connect_start_url(
    provider: str,
    _tid: str = Depends(require_tenant),
) -> JSONResponse:
    """Return the URL the front-end should open to begin an OAuth flow.

    Kept as a thin endpoint so the Managed Agent's `request_credential`
    tool can return a stable JSON shape without hand-coding paths.
    """
    if provider == "google":
        return JSONResponse({"oauth_start_url": "/auth/oauth/google/start"})
    # Meta/QBO/GHL wiring is deferred to post-hackathon (plan section
    # 'What this plan explicitly does NOT do'). Surface honestly.
    return JSONResponse(
        {"error": "provider_not_supported", "provider": provider},
        status_code=501,
    )


# --- helpers ----------------------------------------------------------------


class ProviderOAuthError(RuntimeError):
    """Google (or another provider) rejected our code exchange."""


def exchange_google_code(code: str, verifier: str) -> dict[str, Any]:
    """POST to Google's token endpoint. Monkeypatch-friendly for tests."""
    client_id, client_secret, redirect_uri = _google_client_config()
    try:
        resp = httpx.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
                "code_verifier": verifier,
            },
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        raise ProviderOAuthError(f"token endpoint unreachable: {exc}") from exc
    if resp.status_code != 200:
        raise ProviderOAuthError(f"HTTP {resp.status_code}: {resp.text[:200]}")
    body = resp.json()
    if not isinstance(body, dict):
        raise ProviderOAuthError("token endpoint returned non-object body")
    return body


def _clear_state_and_redirect(url: str) -> RedirectResponse:
    response = RedirectResponse(url=url, status_code=303)
    kwargs = _oauth_state_cookie_kwargs()
    response.delete_cookie(
        key=kwargs["key"],
        path=kwargs["path"],
    )
    return response
