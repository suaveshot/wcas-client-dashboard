"""
POST /api/activation/screenshot - the owner uploads a vendor-UI screenshot
during credential-capture, the next chat turn includes a plain-English
description of what's actually on screen.

Why this exists (from project_activation_screenshot_library memory):
Claude's training data goes stale on vendor UIs fast. When the agent's
guidance says "click Settings > API" but Google renamed the menu, the
owner can snap a screenshot and the orchestrator re-grounds on the
actual pixels.

Flow:
  1. Owner clicks the camera button in the chat composer, picks a
     screenshot (or drags + drops one).
  2. Browser POSTs multipart/form-data to /api/activation/screenshot
     with the image.
  3. We validate size + mime, scrub the filename, save to
     /opt/wc-solns/<tenant_id>/activation_screenshots/<ts>-<rand>.png
     chmod 600, and return the server path.
  4. The chat composer then sends its text message to
     /api/activation/chat with a `screenshots: [path, ...]` field.
  5. The chat handler calls describe_screenshot() on each path before
     handing the turn to the Managed Agent. The agent receives the
     owner's typed message plus a "[screenshot context]" block so it
     can respond to the actual UI state.

Privacy:
  - Screenshots live in the tenant's private directory; never shared
    across tenants.
  - The scrubber runs over any text extracted from the image before it
    lands in the agent's context or an audit log.
  - Rate limit: 6 uploads per 10 minutes per tenant.
"""

from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from ..services import audit_log, heartbeat_store, rate_limit
from ..services.tenant_ctx import require_tenant

log = logging.getLogger("dashboard.activation_screenshot")

router = APIRouter(tags=["activation_screenshot"])


_MAX_BYTES = 5 * 1024 * 1024  # 5 MB per image, plenty for a browser screenshot
_ALLOWED_MIME = frozenset({"image/png", "image/jpeg", "image/webp"})
_EXT_FOR_MIME = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}


def _tenant_screenshot_dir(tenant_id: str) -> Path:
    root = heartbeat_store.tenant_root(tenant_id) / "activation_screenshots"
    root.mkdir(parents=True, exist_ok=True)
    return root


@router.post("/api/activation/screenshot")
async def upload_screenshot(
    image: UploadFile = File(...),
    tenant_id: str = Depends(require_tenant),
) -> dict[str, str]:
    if not rate_limit.activation_samples_limiter.allow(f"screenshot:{tenant_id}"):
        raise HTTPException(status_code=429, detail="rate_limited")

    mime = (image.content_type or "").lower().strip()
    if mime not in _ALLOWED_MIME:
        audit_log.record(
            tenant_id=tenant_id,
            event="screenshot_upload_rejected_mime",
            ok=False,
            mime=mime,
        )
        raise HTTPException(status_code=400, detail="unsupported_content_type")

    body = await image.read()
    if len(body) > _MAX_BYTES:
        audit_log.record(
            tenant_id=tenant_id,
            event="screenshot_upload_rejected_size",
            ok=False,
            bytes=len(body),
        )
        raise HTTPException(status_code=400, detail="too_large")
    if len(body) == 0:
        raise HTTPException(status_code=400, detail="empty_file")

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = _EXT_FOR_MIME.get(mime, ".png")
    filename = f"{ts}-{secrets.token_hex(6)}{suffix}"
    # filename is server-generated; no user-supplied filename reaches disk.
    target = _tenant_screenshot_dir(tenant_id) / filename
    try:
        target.write_bytes(body)
        try:
            os.chmod(target, 0o600)
        except (PermissionError, NotImplementedError, OSError):
            pass
    except OSError as exc:
        log.exception("screenshot write failed tenant=%s", tenant_id)
        raise HTTPException(status_code=500, detail="write_failed") from exc

    audit_log.record(
        tenant_id=tenant_id,
        event="screenshot_uploaded",
        ok=True,
        path=filename,
        bytes=len(body),
        mime=mime,
    )
    # Return only the bare filename; the chat handler resolves it against
    # the tenant's private directory server-side, so a client can't pass
    # paths from other tenants.
    return {"path": filename, "mime": mime, "bytes": str(len(body))}
