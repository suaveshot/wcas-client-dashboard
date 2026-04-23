"""
Security headers middleware.

Applied to every response. HTML responses get the full header set; JSON
responses under /api/ skip CSP (no rendered HTML to protect) but keep the
other hardening headers.

HSTS is only emitted when PRODUCTION=true to avoid poisoning localhost /
dev setups that don't run TLS.
"""

from __future__ import annotations

import os

from fastapi import Request
from starlette.responses import Response


CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self';"
)


async def security_headers_middleware(request: Request, call_next) -> Response:
    response: Response = await call_next(request)
    is_api = request.url.path.startswith("/api/")
    is_prod = os.getenv("PRODUCTION", "false").lower() == "true"

    if not is_api:
        response.headers.setdefault("Content-Security-Policy", CSP)
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")

    if is_prod:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response
