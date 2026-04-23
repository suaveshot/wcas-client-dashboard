"""
Per-tenant activation progress for the ring grid.

Stored at:
    /opt/wc-solns/<tenant_id>/activation.json

Shape:
    {
      "updated_at": "2026-04-23T15:04:05+00:00",
      "roles": {
        "gbp":   {"step": "credentials", "step_at": "2026-04-23T15:04:05+00:00"},
        "seo":   {"step": "credentials", "step_at": "2026-04-23T15:04:05+00:00"},
        ...
      }
    }

STEPS are ordered. A role's `step` is the highest stage it has reached.
Absence from `roles` means the role has not started activation yet.

    credentials -> credential captured (tokens stored)
    config      -> role-specific config captured (GBP location id, etc)
    connected   -> validation probe returned ok against live APIs
    first_run   -> the pipeline has actually executed once

Advance is MONOTONIC: a role cannot regress from a later step to an
earlier one via advance(); use reset_role() if a disconnect flow needs
to drop it back to zero.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import heartbeat_store


STEPS: tuple[str, ...] = ("credentials", "config", "connected", "first_run")
_STEP_INDEX: dict[str, int] = {s: i for i, s in enumerate(STEPS)}

# Safe-slug guard for role identifiers. Matches the pipeline_id rule.
_SAFE_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class ActivationError(ValueError):
    """Invalid tenant, role, step, or illegal transition."""


def _state_path(tenant_id: str) -> Path:
    return heartbeat_store.tenant_root(tenant_id) / "activation.json"


def _validate_slug(slug: str) -> None:
    if not _SAFE_SLUG.match(slug or ""):
        raise ActivationError(f"invalid role slug: {slug!r}")


def _validate_step(step: str) -> None:
    if step not in _STEP_INDEX:
        raise ActivationError(f"unknown step: {step!r}")


def get(tenant_id: str) -> dict[str, Any]:
    """Return the raw state dict for this tenant (empty if nothing saved)."""
    try:
        path = _state_path(tenant_id)
    except heartbeat_store.HeartbeatError:
        return {"updated_at": None, "roles": {}}
    if not path.exists():
        return {"updated_at": None, "roles": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"updated_at": None, "roles": {}}
    if not isinstance(data, dict) or not isinstance(data.get("roles"), dict):
        return {"updated_at": None, "roles": {}}
    return data


def role_step(tenant_id: str, slug: str) -> str | None:
    """Current step for a single role, or None if not started."""
    _validate_slug(slug)
    entry = get(tenant_id)["roles"].get(slug)
    if isinstance(entry, dict):
        return entry.get("step")
    return None


def advance(tenant_id: str, slug: str, to_step: str) -> dict[str, Any]:
    """Move a role to the named step. No-op if already at or past it.

    Raises ActivationError on an attempted regression.
    """
    _validate_slug(slug)
    _validate_step(to_step)
    state = get(tenant_id)
    existing = state["roles"].get(slug) or {}
    existing_step = existing.get("step") if isinstance(existing, dict) else None
    if existing_step is not None:
        _validate_step(existing_step)
        if _STEP_INDEX[to_step] < _STEP_INDEX[existing_step]:
            raise ActivationError(
                f"cannot regress {slug!r} from {existing_step} to {to_step}; use reset_role()"
            )
        if _STEP_INDEX[to_step] == _STEP_INDEX[existing_step]:
            # Idempotent: same step, no-op, no write.
            return state
    now = datetime.now(timezone.utc).isoformat()
    state["roles"][slug] = {"step": to_step, "step_at": now}
    state["updated_at"] = now
    _write(tenant_id, state)
    return state


def bulk_advance(tenant_id: str, slugs: list[str], to_step: str) -> dict[str, Any]:
    """Advance several roles to the same step in a single write.

    Skips any role that would regress (logs silently; callers that need
    regression detection should call advance() per-slug). Each slug that
    is already at or past `to_step` is left as-is.
    """
    _validate_step(to_step)
    state = get(tenant_id)
    now = datetime.now(timezone.utc).isoformat()
    dirty = False
    for slug in slugs:
        _validate_slug(slug)
        existing = state["roles"].get(slug) or {}
        existing_step = existing.get("step") if isinstance(existing, dict) else None
        if existing_step is not None and _STEP_INDEX[to_step] <= _STEP_INDEX[existing_step]:
            continue
        state["roles"][slug] = {"step": to_step, "step_at": now}
        dirty = True
    if dirty:
        state["updated_at"] = now
        _write(tenant_id, state)
    return state


def reset_role(tenant_id: str, slug: str) -> bool:
    """Drop a role from activation state (disconnect flow). True if removed."""
    _validate_slug(slug)
    state = get(tenant_id)
    if slug not in state["roles"]:
        return False
    state["roles"].pop(slug, None)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    _write(tenant_id, state)
    return True


def ring_view(tenant_id: str, role_slugs: list[str]) -> list[dict[str, Any]]:
    """Render the ring grid for a specific ordered list of roles.

    Each entry is {slug, step, percent_complete}. percent_complete is the
    fraction of STEPS reached (0.0 for not-started, 0.25 for credentials,
    1.0 for first_run). Convenient for templates.
    """
    state = get(tenant_id)
    out: list[dict[str, Any]] = []
    total = len(STEPS)
    for slug in role_slugs:
        _validate_slug(slug)
        entry = state["roles"].get(slug)
        if isinstance(entry, dict) and entry.get("step") in _STEP_INDEX:
            step = entry["step"]
            # "credentials" is step 0 of 4 ~= 25% ring fill; "first_run" is 100%.
            percent = (_STEP_INDEX[step] + 1) / total
            out.append({"slug": slug, "step": step, "percent_complete": round(percent, 4)})
        else:
            out.append({"slug": slug, "step": None, "percent_complete": 0.0})
    return out


def _write(tenant_id: str, state: dict[str, Any]) -> Path:
    path = _state_path(tenant_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path
