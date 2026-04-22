"""
Session cookie service.

Signed, time-limited cookie carrying the minimum the app needs to
resolve a request to a tenant: tenant_id, email, role. Nothing
about the tenant's business lives here; the middleware reads this
cookie, verifies the signature, and attaches tenant_id to the
request for downstream handlers.

Signing uses itsdangerous.URLSafeTimedSerializer with SESSION_SECRET
from env. The same secret must be set on every app instance.
"""

import os
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

SESSION_MAX_AGE_SECONDS = 60 * 60 * 24  # 24 hours rolling


def _secret() -> str:
    secret = os.getenv("SESSION_SECRET", "")
    if not secret:
        raise RuntimeError(
            "SESSION_SECRET is required. "
            "Generate with: python -c 'import secrets; print(secrets.token_urlsafe(32))'"
        )
    return secret


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_secret(), salt="wcas-session-v1")


def issue(tenant_id: str, email: str, role: str = "client") -> str:
    """Build a signed cookie value."""
    payload = {"tid": tenant_id, "em": email, "rl": role}
    return _serializer().dumps(payload)


def verify(token: str) -> dict[str, Any] | None:
    """Return the session payload or None if invalid/expired."""
    if not token:
        return None
    try:
        payload = _serializer().loads(token, max_age=SESSION_MAX_AGE_SECONDS)
    except SignatureExpired:
        return None
    except BadSignature:
        return None
    if not isinstance(payload, dict):
        return None
    if "tid" not in payload or "em" not in payload:
        return None
    return payload


def cookie_kwargs() -> dict[str, Any]:
    """Safe defaults for Set-Cookie: HttpOnly + SameSite=Strict + Secure in prod."""
    domain = os.getenv("COOKIE_DOMAIN") or None
    # Local dev over http needs Secure=False. Prod sets PRODUCTION=true.
    secure = os.getenv("PRODUCTION", "false").lower() == "true"
    return {
        "key": os.getenv("COOKIE_NAME", "wcas_session"),
        "max_age": SESSION_MAX_AGE_SECONDS,
        "httponly": True,
        "samesite": "strict",
        "secure": secure,
        "domain": domain,
        "path": "/",
    }
