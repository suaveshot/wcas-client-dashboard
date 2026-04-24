"""
Screenshot -> plain-English description.

The Managed Agents beta loop is text-only today. To let owners send
screenshots and have the orchestrator "see" them, we call the multimodal
Messages API directly on each uploaded image, produce a short text
description of what's on screen, and hand that description to the agent
as part of the next user message.

Design:
- Runs through opus.chat (cache_system=True) so the instruction block
  caches across screenshots in the same session.
- Uses Opus (multimodal) regardless of ACTIVATION_AGENT_MODEL so the
  description quality stays high even when the agent is running Haiku.
- Budget-capped via the same cost_tracker path; if the cap is reached,
  return a placeholder so the chat still flows.

Security:
- Filenames come from activation_screenshot.upload_screenshot (server-
  generated tokens, no user input). `describe_path` refuses any file
  outside the tenant's activation_screenshots directory.
- The returned text runs through scrubber.scrub before it lands in the
  audit log or the agent context.
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path

from . import heartbeat_store, opus
from .scrubber import scrub

log = logging.getLogger("dashboard.screenshot_vision")


_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


_DESCRIBE_SYSTEM = (
    "You describe screenshots of vendor admin UIs for a small-business "
    "onboarding orchestrator. The orchestrator will use your description to "
    "give the business owner accurate next-step guidance. Stay literal: name "
    "the screen title, the visible navigation items, the buttons the owner "
    "could click, and any obvious state (error banners, missing-fields, "
    "unverified badges). Keep it under 8 short lines. No em dashes. Never "
    "speculate about what's behind a button; only describe what's on screen. "
    "Skip any personal information you can read (emails, phone numbers); "
    "describe them as [redacted] instead."
)


def _resolve_tenant_path(tenant_id: str, filename: str) -> Path:
    """Refuse anything that isn't a plain filename in the tenant's screenshot dir.
    Raises ValueError on attempted traversal or mismatched tenant root."""
    if not filename or "/" in filename or "\\" in filename or filename.startswith("."):
        raise ValueError("invalid filename")
    root = heartbeat_store.tenant_root(tenant_id) / "activation_screenshots"
    candidate = (root / filename).resolve()
    root_resolved = root.resolve()
    # On Windows, is_relative_to only works on Python 3.9+; we're 3.12.
    if not candidate.is_relative_to(root_resolved):
        raise ValueError("path escapes tenant directory")
    if not candidate.is_file():
        raise ValueError("file not found")
    return candidate


def describe_path(tenant_id: str, filename: str) -> str:
    """Return a short plain-English description of the screenshot contents.

    `filename` is the server-generated name returned by /api/activation/screenshot
    (no directory components, no traversal). We resolve it against the tenant's
    private screenshot directory before reading.
    """
    path = _resolve_tenant_path(tenant_id, filename)
    suffix = path.suffix.lower()
    media_type = _MIME_BY_EXT.get(suffix)
    if media_type is None:
        raise ValueError(f"unsupported extension: {suffix}")

    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"read failed: {exc}") from exc
    if len(raw) == 0:
        raise ValueError("empty file")

    encoded = base64.b64encode(raw).decode("ascii")

    # Multimodal Messages API shape.
    content = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": encoded,
            },
        },
        {
            "type": "text",
            "text": (
                "Describe this vendor UI screenshot in under 8 short lines. "
                "Name the screen, visible navigation items, buttons, and obvious "
                "state. Redact personal info. No speculation."
            ),
        },
    ]

    try:
        result = opus.chat(
            tenant_id=tenant_id,
            system=_DESCRIBE_SYSTEM,
            messages=[{"role": "user", "content": content}],
            # Use Opus (multimodal quality matters) regardless of the agent's model.
            model=os.getenv("WCAS_SCREENSHOT_MODEL", "claude-opus-4-7"),
            max_tokens=320,
            temperature=0.1,
            kind="screenshot_describe",
            note=f"shot:{filename}",
            cache_system=True,
        )
    except opus.OpusBudgetExceeded:
        return "[screenshot received; daily budget reached, description skipped]"
    except opus.OpusUnavailable as exc:
        log.warning("screenshot vision unavailable: %s", exc)
        return "[screenshot received; vision temporarily unavailable]"
    except Exception:  # defensive
        log.exception("describe_path failed tenant=%s filename=%s", tenant_id, filename)
        return "[screenshot received; description failed]"

    text = (result.text or "").strip()
    if not text:
        return "[screenshot received; no description produced]"
    # Strip any secret-shaped tokens that slipped through.
    return scrub(text)
