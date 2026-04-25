"""
Magic-link auth routes.

Flow:
    1. Client hits /auth/login, enters email, submits.
    2. POST /auth/request generates token, hashes, stores hash in
       Airtable Clients row, emails the link. Always redirects to a
       neutral "check your inbox" page, even on unknown email, so we
       don't leak which emails are in the CRM.
    3. Client clicks link -> GET /auth/verify?token=... validates
       against the stored hash, checks expiry + consumed, marks
       consumed, issues signed cookie, redirects to /dashboard.
    4. /auth/logout clears the cookie.
"""

import logging
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..services import (
    audit_log,
    clients_repo,
    email_sender,
    rate_limit,
    sessions,
    tokens,
)
from ..services.scrubber import scrub

log = logging.getLogger("dashboard.auth")

router = APIRouter(prefix="/auth", tags=["auth"])

_TEMPLATES: Jinja2Templates | None = None


def attach_templates(templates: Jinja2Templates) -> None:
    global _TEMPLATES
    _TEMPLATES = templates


def _tmpl() -> Jinja2Templates:
    if _TEMPLATES is None:
        raise RuntimeError("auth templates not attached")
    return _TEMPLATES


def _magic_link_url(request: Request, token: str) -> str:
    # Prefer the public host from env so we don't accidentally email
    # out a localhost link when running behind a reverse proxy.
    base = (request.headers.get("x-forwarded-host")
            or request.headers.get("host")
            or "dashboard.westcoastautomationsolutions.com")
    scheme = "https" if "localhost" not in base else request.url.scheme
    return f"{scheme}://{base}/auth/verify?{urlencode({'token': token})}"


def _render_magic_link_email(
    request: Request, token: str, tenant_display: str
) -> tuple[str, str]:
    url = _magic_link_url(request, token)
    html = _tmpl().get_template("emails/magic_link.html").render(
        link_url=url,
        tenant_display=tenant_display or "your dashboard",
        ttl_minutes=max(1, tokens.ttl_seconds() // 60),
    )
    text = _tmpl().get_template("emails/magic_link.txt").render(
        link_url=url,
        tenant_display=tenant_display or "your dashboard",
        ttl_minutes=max(1, tokens.ttl_seconds() // 60),
    )
    return html, text


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request) -> HTMLResponse:
    return _tmpl().TemplateResponse(request, "auth/login.html", {"error": None})


@router.post("/request", response_class=HTMLResponse)
async def request_magic_link(request: Request, email: str = Form(...)) -> HTMLResponse:
    email_clean = email.strip().lower()
    if "@" not in email_clean or len(email_clean) > 254:
        return _tmpl().TemplateResponse(
            request, "auth/login.html", {"error": "That doesn't look like a valid email."}
        )

    if not rate_limit.login_limiter.allow(email_clean):
        # Always show the neutral "check your inbox" screen, even on rate-limit.
        # Privacy: attacker can't tell if we refused because of rate vs unknown email.
        log.warning("login rate-limit hit for %s", scrub(email_clean))
        return _tmpl().TemplateResponse(request, "auth/check_inbox.html", {"email": email_clean})

    try:
        record = clients_repo.find_by_email(email_clean)
    except RuntimeError as exc:
        # Airtable not configured (local dev without creds) -> show the
        # neutral page but log loudly so Sam sees it.
        log.error("Airtable unavailable on /auth/request: %s", exc)
        return _tmpl().TemplateResponse(request, "auth/check_inbox.html", {"email": email_clean})

    # Gate: require both (a) an active client row AND (b) Sam has flipped
    # the Onboarding Approved field. Unapproved + unknown + rate-limited
    # all fall through to the same neutral "check your inbox" page so an
    # attacker can't enumerate which emails are in the CRM. Approval
    # denials are logged server-side for Sam's audit.
    if record is not None and clients_repo.is_active(record):
        if not clients_repo.is_onboarding_approved(record):
            tenant_for_log = clients_repo.extract_tenant_id(record) or "unknown"
            log.info(
                "magic-link denied (not approved) tenant=%s email=%s",
                tenant_for_log,
                scrub(email_clean),
            )
            audit_log.record(
                tenant_id=tenant_for_log,
                event="magic_link_denied_unapproved",
                ok=False,
                actor_email=email_clean,
                reason="Onboarding Approved = false",
            )
        else:
            token = tokens.generate_token()
            try:
                clients_repo.stash_magic_link(
                    record["id"], tokens.hash_token(token), tokens.expiry_timestamp()
                )
                html_body, text_body = _render_magic_link_email(
                    request, token, clients_repo.extract_tenant_id(record)
                )
                email_sender.send_html(
                    to_email=email_clean,
                    subject="Your dashboard sign-in link",
                    html_body=html_body,
                    text_body=text_body,
                )
            except (email_sender.EmailSendError, RuntimeError):
                log.exception("magic link send failed for %s", scrub(email_clean))
                # Still show check-inbox page; don't leak failure.

    return _tmpl().TemplateResponse(request, "auth/check_inbox.html", {"email": email_clean})


@router.get("/verify")
async def verify_magic_link(request: Request, token: str = "") -> RedirectResponse:
    if not token:
        return RedirectResponse(url="/auth/login?e=missing", status_code=303)

    candidate_hash = tokens.hash_token(token)

    try:
        record = clients_repo.find_by_hash(candidate_hash)
    except RuntimeError:
        log.exception("verify lookup failed (airtable not configured)")
        return RedirectResponse(url="/auth/login?e=server", status_code=303)

    if record is None:
        return RedirectResponse(url="/auth/login?e=invalid", status_code=303)
    stored_hash, expiry_iso, consumed = clients_repo.extract_magic_link(record)

    if consumed or not stored_hash:
        return RedirectResponse(url="/auth/login?e=used", status_code=303)
    if tokens.is_expired(expiry_iso):
        return RedirectResponse(url="/auth/login?e=expired", status_code=303)
    if not tokens.hashes_match(candidate_hash, stored_hash):
        return RedirectResponse(url="/auth/login?e=invalid", status_code=303)

    tenant_id = clients_repo.extract_tenant_id(record)
    email = clients_repo.extract_email(record)
    role = clients_repo.extract_role(record, email)

    if not tenant_id or not email:
        log.error("verify succeeded but record missing tenant_id/email record_id=%s", record.get("id"))
        return RedirectResponse(url="/auth/login?e=incomplete", status_code=303)

    clients_repo.mark_consumed(record["id"])

    # Audit + alert Sam. Both are best-effort; failures log but don't break login.
    audit_log.record(
        tenant_id=tenant_id,
        event="magic_link_verified",
        ok=True,
        actor_email=email,
    )
    email_sender.alert_sam(
        tenant_id=tenant_id,
        event_type="onboarding_started",
        subject=f"[WCAS] {tenant_id} signed in",
        body=(
            f"Client sign-in on the activation wizard.\n\n"
            f"Tenant:  {tenant_id}\n"
            f"Email:   {email}\n"
            f"Role:    {role}\n"
        ),
    )

    cookie_value = sessions.issue(tenant_id=tenant_id, email=email, role=role)
    # First-time owners land on /activate to run the onboarding chat; returning
    # owners (activation already marked complete) go straight to /dashboard.
    # Admins skip the activation gate (the /admin view itself was punted
    # post-hackathon) and land on /dashboard.
    if role == "admin":
        landing = "/dashboard"
    else:
        from ..services import activation_state
        landing = "/dashboard" if activation_state.is_complete(tenant_id) else "/activate"
    response = RedirectResponse(url=landing, status_code=303)
    response.set_cookie(value=cookie_value, **sessions.cookie_kwargs())
    return response


@router.post("/logout")
async def logout() -> RedirectResponse:
    response = RedirectResponse(url="/", status_code=303)
    kwargs = sessions.cookie_kwargs()
    response.delete_cookie(
        key=kwargs["key"],
        domain=kwargs["domain"],
        path=kwargs["path"],
    )
    return response
