"""
Tenant context + route protection.

Two pieces:

  resolve_session_middleware
    Runs on every request. Reads the signed session cookie (if any)
    and attaches the decoded payload to request.state.session so
    handlers can check auth state cheaply.

  require_tenant
    FastAPI dependency. Protected endpoints declare
    `tenant_id = Depends(require_tenant)` and get back a string.
    No cookie -> 401. Paused tenant -> still returns the id so the
    branded paused page can render; the view layer decides.

  require_admin
    Same idea, but 403 if role != "admin".
"""

from fastapi import Depends, HTTPException, Request, status

from . import sessions


async def resolve_session_middleware(request: Request, call_next):
    cookie_name = sessions.cookie_kwargs()["key"]
    raw = request.cookies.get(cookie_name)
    request.state.session = sessions.verify(raw) if raw else None
    return await call_next(request)


def current_session(request: Request) -> dict | None:
    return getattr(request.state, "session", None)


def require_tenant(request: Request) -> str:
    sess = current_session(request)
    if not sess or not sess.get("tid"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
        )
    return sess["tid"]


def require_admin(request: Request, _tid: str = Depends(require_tenant)) -> str:
    sess = current_session(request)
    if not sess or sess.get("rl") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin only",
        )
    return sess["tid"]
