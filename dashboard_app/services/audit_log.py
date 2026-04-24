"""
Append-only audit log for activation + provisioning events.

Every provisioning tool call, credential write, TOS acceptance, and
mark_activation_complete fires one line into:

    /opt/wc-solns/<tenant_id>/audit/activation.log

Each line is a single JSON object, one per line (JSONL). Values that
look like secrets are scrubbed before write via `services.scrubber`.
File is chmod 600 on POSIX. Never read-modified-written: new lines are
simply appended.

The log lives on the same VPS as the app - Tier-1 post-hackathon adds
off-box shipping. Until then, Hostinger VPS snapshots cover backup.

Usage:
    audit_log.record(
        tenant_id="garcia_folklorico",
        event="tool_call",
        tool="create_ga4_property",
        args={"display_name": "Garcia Folklorico"},
        ok=True,
        actor_email="itzel@garciafolklorico.com",
    )

Contract:
- `record()` never raises. An audit-log write failure must not break
  the calling flow (the provisioning call itself is the source of
  truth; the log is the audit trail).
- Callers pass any extra fields as keyword args - they get persisted
  verbatim alongside the standard fields.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import heartbeat_store
from .scrubber import scrub

log = logging.getLogger("dashboard.audit_log")


def _log_path(tenant_id: str) -> Path:
    root = heartbeat_store.tenant_root(tenant_id) / "audit"
    root.mkdir(parents=True, exist_ok=True)
    return root / "activation.log"


def _scrub_value(v: Any) -> Any:
    """Best-effort scrub of a value before persistence.
    Strings go through the secret-shape scrubber. Dicts / lists recurse."""
    if isinstance(v, str):
        return scrub(v)
    if isinstance(v, dict):
        return {k: _scrub_value(sub) for k, sub in v.items()}
    if isinstance(v, list):
        return [_scrub_value(x) for x in v]
    return v


def record(
    *,
    tenant_id: str,
    event: str,
    ok: bool = True,
    actor_email: str = "",
    **extra: Any,
) -> None:
    """Append one audit entry. Never raises."""
    try:
        line = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": str(event)[:64],
            "tenant_id": str(tenant_id)[:64],
            "ok": bool(ok),
        }
        if actor_email:
            line["actor_email"] = str(actor_email)[:128]
        for key, value in extra.items():
            line[key] = _scrub_value(value)
        path = _log_path(tenant_id)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, separators=(",", ":")) + "\n")
        try:
            os.chmod(path, 0o600)
        except (PermissionError, NotImplementedError, OSError):
            # Windows / FUSE filesystems. Prod is Linux where this succeeds.
            pass
    except (heartbeat_store.HeartbeatError, OSError, TypeError, ValueError) as exc:
        # Never let the audit log break the caller.
        log.warning("audit_log.record failed tenant=%s event=%s: %s", tenant_id, event, exc)


def read_recent(tenant_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
    """Return the most recent N entries (newest first). For a post-hackathon
    /admin view. Returns empty list for any error."""
    try:
        path = _log_path(tenant_id)
    except heartbeat_store.HeartbeatError:
        return []
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for raw in reversed(lines[-max(1, limit):]):
        if not raw.strip():
            continue
        try:
            out.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return out
