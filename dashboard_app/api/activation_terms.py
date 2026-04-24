"""
Terms-of-service acceptance endpoint.

Flow:
    1. Authenticated tenant hits GET /activate without a stored TOS
       acceptance at or above CURRENT_TOS_VERSION -> /activate route
       redirects them to /activate/terms (rendered directly from main).
    2. They click the "I agree" button -> POST /api/activation/accept-terms.
    3. We record the acceptance in Airtable (version, timestamp, IP, UA)
       plus write an audit-log entry, then redirect to /activate.

We only accept the CURRENT version. An old version in the form body would
be rejected so a bookmarked/replayed POST can't downgrade the record.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from ..services import audit_log, clients_repo
from ..services.scrubber import scrub
from ..services.tenant_ctx import current_session, require_tenant

log = logging.getLogger("dashboard.activation_terms")

router = APIRouter(tags=["activation_terms"])


def _request_ip(request: Request) -> str:
    """Best-effort client IP. Respects X-Forwarded-For when present (Caddy sets it)."""
    xff = (request.headers.get("x-forwarded-for") or "").strip()
    if xff:
        return xff.split(",")[0].strip()[:64]
    client = request.client
    return (client.host if client else "")[:64]


@router.post("/api/activation/accept-terms")
async def accept_terms(
    request: Request,
    version: str = Form(""),
    tenant_id: str = Depends(require_tenant),
) -> RedirectResponse:
    """Persist the owner's TOS acceptance click + redirect to /activate."""
    session = current_session(request)
    actor_email = getattr(session, "email", "") if session else ""

    accepted_version = (version or "").strip() or clients_repo.CURRENT_TOS_VERSION
    if accepted_version != clients_repo.CURRENT_TOS_VERSION:
        # Replayed form with a stale version -> force them through the
        # current page again. Don't leak why.
        return RedirectResponse(url="/activate/terms?e=stale", status_code=303)

    ip = _request_ip(request)
    user_agent = (request.headers.get("user-agent") or "")[:400]

    try:
        record = clients_repo.find_by_tenant_id(tenant_id)
    except RuntimeError:
        record = None

    if record is None:
        log.warning("accept-terms with no client row tenant=%s", tenant_id)
        audit_log.record(
            tenant_id=tenant_id,
            event="tos_accept_no_record",
            ok=False,
            actor_email=actor_email,
            version=accepted_version,
        )
        # Still redirect; a session with no backing row is a config problem,
        # not an abuse vector. The /activate route guard will bounce them.
        return RedirectResponse(url="/activate", status_code=303)

    try:
        clients_repo.record_tos_acceptance(
            record["id"],
            version=accepted_version,
            ip=ip,
            user_agent=user_agent,
        )
    except RuntimeError as exc:
        log.exception("record_tos_acceptance failed tenant=%s: %s", tenant_id, scrub(str(exc)))
        audit_log.record(
            tenant_id=tenant_id,
            event="tos_accept_write_failed",
            ok=False,
            actor_email=actor_email,
            version=accepted_version,
            error=str(exc),
        )
        return RedirectResponse(url="/activate/terms?e=server", status_code=303)

    audit_log.record(
        tenant_id=tenant_id,
        event="tos_accepted",
        ok=True,
        actor_email=actor_email,
        version=accepted_version,
        ip=ip,
    )
    return RedirectResponse(url="/activate", status_code=303)
