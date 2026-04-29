"""Per-tenant automation enrollment.

Reads/writes /opt/wc-solns/<tenant>/config/automations.json. The dashboard
ring renderer uses this to decide which rings to show; the catalog
(automation_catalog) is the source of truth for what the rings ARE, this
file decides which ones a given tenant has.

File shape:
    {
      "tier": "pro",
      "enabled": [
        {"id": "reviews", "source": "tier_default", "enabled_at": "2026-04-29T10:00:00+00:00"},
        {"id": "voice_ai", "source": "promo_optin", "enabled_at": "...", "expires_at": "..."},
        {"id": "google_ads_manager", "source": "admin_added", "enabled_at": "...", "note": "..."}
      ]
    }

Source enum values:
    tier_default - seeded at onboarding from the chosen tier
    admin_added  - Sam manually added it (post-onboarding)
    promo_optin  - tenant opted into a promo (Phase 3F); has expires_at

Operations are atomic: every write goes through a tmp file + os.replace.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import automation_catalog, heartbeat_store

log = logging.getLogger(__name__)

CONFIG_DIR = "config"
CONFIG_FILENAME = "automations.json"
VALID_SOURCES = frozenset({"tier_default", "admin_added", "promo_optin"})


class TenantAutomationsError(ValueError):
    """Invalid input to a tenant_automations operation."""


def _config_path(tenant_id: str) -> Path:
    return heartbeat_store.tenant_root(tenant_id) / CONFIG_DIR / CONFIG_FILENAME


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read(tenant_id: str) -> dict[str, Any]:
    """Read the raw automations.json. Returns the empty document
    {tier: None, enabled: []} when the file is missing or malformed."""
    try:
        path = _config_path(tenant_id)
    except heartbeat_store.HeartbeatError:
        return {"tier": None, "enabled": []}
    if not path.exists():
        return {"tier": None, "enabled": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("tenant_automations: malformed automations.json for %s", tenant_id)
        return {"tier": None, "enabled": []}
    if not isinstance(data, dict):
        return {"tier": None, "enabled": []}
    enabled = data.get("enabled")
    if not isinstance(enabled, list):
        enabled = []
    return {
        "tier": data.get("tier"),
        "enabled": [e for e in enabled if isinstance(e, dict) and isinstance(e.get("id"), str)],
    }


def _write(tenant_id: str, data: dict[str, Any]) -> Path:
    path = _config_path(tenant_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)
    return path


# ---------------------------------------------------------------------------
# read API
# ---------------------------------------------------------------------------


def list_enabled(tenant_id: str, *, include_expired: bool = False) -> list[dict[str, Any]]:
    """Return the enabled automation entries for a tenant.

    By default expired promo entries are filtered out. Pass
    include_expired=True to surface them (e.g. for the admin UI).
    """
    data = _read(tenant_id)
    raw = data["enabled"]
    if include_expired:
        return list(raw)
    now_iso = _now_iso()
    out: list[dict[str, Any]] = []
    for entry in raw:
        expires_at = entry.get("expires_at")
        if isinstance(expires_at, str) and expires_at and expires_at < now_iso:
            continue
        out.append(entry)
    return out


def enabled_ids(tenant_id: str) -> list[str]:
    """Just the ids of currently enabled automations (expired filtered out)."""
    return [e["id"] for e in list_enabled(tenant_id)]


def is_enabled(tenant_id: str, automation_id: str) -> bool:
    return automation_id in enabled_ids(tenant_id)


def get_tier(tenant_id: str) -> str | None:
    return _read(tenant_id).get("tier")


# ---------------------------------------------------------------------------
# write API
# ---------------------------------------------------------------------------


def enable(
    tenant_id: str,
    automation_id: str,
    *,
    source: str = "admin_added",
    expires_at: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Enable an automation for a tenant. Idempotent: if already enabled,
    updates the source/expiry/note in place and returns the merged entry."""
    if not automation_catalog.exists(automation_id):
        raise TenantAutomationsError(f"unknown automation id: {automation_id!r}")
    if source not in VALID_SOURCES:
        raise TenantAutomationsError(f"invalid source {source!r}; expected one of {VALID_SOURCES}")
    if expires_at is not None and source != "promo_optin":
        raise TenantAutomationsError(
            "expires_at only valid when source='promo_optin'"
        )

    data = _read(tenant_id)
    existing_idx = next(
        (i for i, e in enumerate(data["enabled"]) if e.get("id") == automation_id),
        -1,
    )
    entry: dict[str, Any] = {
        "id": automation_id,
        "source": source,
        "enabled_at": _now_iso(),
    }
    if expires_at:
        entry["expires_at"] = expires_at
    if note:
        entry["note"] = note

    if existing_idx >= 0:
        # Preserve enabled_at across re-enables; everything else updates
        prior = data["enabled"][existing_idx]
        entry["enabled_at"] = prior.get("enabled_at") or entry["enabled_at"]
        data["enabled"][existing_idx] = entry
    else:
        data["enabled"].append(entry)

    _write(tenant_id, data)
    return entry


def disable(tenant_id: str, automation_id: str) -> bool:
    """Remove an automation from the enabled list. Returns True when an
    entry was removed, False when there was nothing to remove."""
    data = _read(tenant_id)
    before = len(data["enabled"])
    data["enabled"] = [e for e in data["enabled"] if e.get("id") != automation_id]
    if len(data["enabled"]) == before:
        return False
    _write(tenant_id, data)
    return True


def seed_for_tier(
    tenant_id: str,
    tier: str,
    *,
    overwrite: bool = False,
) -> list[dict[str, Any]]:
    """Seed automations.json with every tier_default for the given tier.

    By default this is idempotent: if an automations.json already exists
    AND has at least one tier_default entry, this is a no-op. Pass
    overwrite=True at activation time to wipe + reseed (e.g. after a tier
    change).

    Returns the resulting `enabled` list.
    """
    if tier not in automation_catalog.VALID_TIERS:
        raise TenantAutomationsError(f"unknown tier {tier!r}")

    data = _read(tenant_id)
    has_tier_defaults = any(
        e.get("source") == "tier_default" for e in data["enabled"]
    )
    if has_tier_defaults and not overwrite:
        # Always update the stored tier so a tier change is reflected
        if data.get("tier") != tier:
            data["tier"] = tier
            _write(tenant_id, data)
        return data["enabled"]

    # Drop existing tier_default entries; preserve admin_added + promo_optin
    preserved = [e for e in data["enabled"] if e.get("source") != "tier_default"]
    new_entries: list[dict[str, Any]] = []
    for aid in automation_catalog.tier_default_ids(tier):
        if any(e.get("id") == aid for e in preserved):
            continue  # already present via another source
        new_entries.append({
            "id": aid,
            "source": "tier_default",
            "enabled_at": _now_iso(),
        })

    data = {"tier": tier, "enabled": preserved + new_entries}
    _write(tenant_id, data)
    return data["enabled"]


def prune_expired(tenant_id: str) -> int:
    """Remove every promo_optin entry whose expires_at is in the past.
    Returns the number of entries pruned."""
    data = _read(tenant_id)
    now_iso = _now_iso()
    kept: list[dict[str, Any]] = []
    pruned = 0
    for entry in data["enabled"]:
        expires_at = entry.get("expires_at")
        if isinstance(expires_at, str) and expires_at and expires_at < now_iso:
            pruned += 1
            continue
        kept.append(entry)
    if pruned > 0:
        data["enabled"] = kept
        _write(tenant_id, data)
    return pruned


__all__ = [
    "CONFIG_DIR",
    "CONFIG_FILENAME",
    "VALID_SOURCES",
    "TenantAutomationsError",
    "list_enabled",
    "enabled_ids",
    "is_enabled",
    "get_tier",
    "enable",
    "disable",
    "seed_for_tier",
    "prune_expired",
]
