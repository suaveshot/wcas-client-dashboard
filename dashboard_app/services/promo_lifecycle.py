"""Promo lifecycle helpers.

Pure-function layer on top of `tenant_automations` for the promo system.
The data layer (enable/disable/prune_expired/list_enabled) already
handles atomic writes and expiry filtering on read; this module adds:

  - grant_promo / revoke_promo: validated, single-tenant write helpers
    that enforce catalog membership and source rules.
  - find_expiring_soon / find_expiring_soon_all_tenants: read-side
    helpers for "expiring in N days" alerting.
  - sweep_expired_all_tenants: platform-level garbage collection over
    every tenant directory, intended to be wired into the dispatcher
    daily tick by a separate task.

All clock-dependent functions accept an explicit `now` so tests can
inject deterministic time. Production callers pass
datetime.now(timezone.utc).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import automation_catalog, heartbeat_store, tenant_automations

log = logging.getLogger(__name__)


EXPIRING_SOON_DAYS = 3


class PromoError(Exception):
    """Validation failure in a promo lifecycle operation."""


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _tenant_root_base() -> Path:
    """Filesystem root that contains every per-tenant directory.

    Mirrors `heartbeat_store.tenant_root` but returns the parent so we
    can iterate tenants. Honors the same TENANT_ROOT env var.
    """
    return Path(os.getenv("TENANT_ROOT", "/opt/wc-solns"))


def _iter_tenant_ids() -> list[str]:
    """Every directory under TENANT_ROOT that looks like a real tenant.

    Skips entries whose name starts with `_` (reserved for platform-level
    bookkeeping such as `_platform`). Skips files. Skips names that
    would fail the heartbeat slug check.
    """
    base = _tenant_root_base()
    if not base.exists():
        return []
    out: list[str] = []
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        name = child.name
        if name.startswith("_"):
            continue
        try:
            heartbeat_store.tenant_root(name)
        except heartbeat_store.HeartbeatError:
            continue
        out.append(name)
    return out


def _validate_tenant(tenant_id: str) -> None:
    try:
        heartbeat_store.tenant_root(tenant_id)
    except heartbeat_store.HeartbeatError as exc:
        raise PromoError(f"invalid tenant_id {tenant_id!r}: {exc}") from exc


def _validate_automation(automation_id: str) -> None:
    if not automation_catalog.exists(automation_id):
        raise PromoError(f"automation {automation_id!r} not in catalog")


def validate_grant_args(tenant_id: str, automation_id: str, days: int) -> None:
    """Run every check `grant_promo` would run, without writing anything.

    Used by `grant_promo` itself (so the rules live in one place) and by
    admin CLIs that want to validate a dry-run without committing the
    enable() write. Raises PromoError on any failure; returns None on
    success.
    """
    _validate_tenant(tenant_id)
    _validate_automation(automation_id)
    if not isinstance(days, int) or isinstance(days, bool) or days <= 0:
        raise PromoError(f"days must be a positive integer, got {days!r}")


# ---------------------------------------------------------------------------
# write API
# ---------------------------------------------------------------------------


def grant_promo(
    tenant_id: str,
    automation_id: str,
    *,
    days: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Grant `tenant_id` a promo enrollment for `automation_id` lasting
    `days` days. Returns the new row dict written to automations.json.

    Validates:
      - tenant_id is a safe slug (via heartbeat_store)
      - automation_id exists in the catalog
      - days is a positive integer

    Always writes with source="promo_optin" so the data layer attaches
    expires_at and the read-side filter applies.
    """
    validate_grant_args(tenant_id, automation_id, days)

    base_now = now if now is not None else _now_utc()
    expires_at = (base_now + timedelta(days=days)).isoformat()
    return tenant_automations.enable(
        tenant_id,
        automation_id,
        source="promo_optin",
        expires_at=expires_at,
    )


def revoke_promo(tenant_id: str, automation_id: str) -> bool:
    """Revoke an active promo enrollment. Returns True if a row was
    removed, False if the tenant never had this automation.

    Raises PromoError if the automation IS enrolled but its source is
    not `promo_optin` (tier_default and admin_added rows go through
    different surfaces; we refuse to misroute them).
    """
    _validate_tenant(tenant_id)
    _validate_automation(automation_id)

    rows = tenant_automations.list_enabled(tenant_id, include_expired=True)
    match = next((r for r in rows if r.get("id") == automation_id), None)
    if match is None:
        return False
    source = match.get("source")
    if source != "promo_optin":
        raise PromoError(
            f"refusing to revoke {automation_id!r} on {tenant_id!r}: "
            f"source is {source!r}, not 'promo_optin'"
        )
    return tenant_automations.disable(tenant_id, automation_id)


# ---------------------------------------------------------------------------
# read API
# ---------------------------------------------------------------------------


def find_expiring_soon(
    tenant_id: str,
    *,
    now: datetime,
    threshold_days: int = EXPIRING_SOON_DAYS,
) -> list[dict[str, Any]]:
    """Return promo rows whose expires_at falls in [now, now + threshold_days].

    Only inspects active (non-expired) rows. Filters to source=promo_optin
    so admin_added / tier_default entries (which never carry expires_at)
    can't accidentally surface.
    """
    cutoff = now + timedelta(days=threshold_days)
    out: list[dict[str, Any]] = []
    for entry in tenant_automations.list_enabled(tenant_id, include_expired=False):
        if entry.get("source") != "promo_optin":
            continue
        expires_at = entry.get("expires_at")
        if not isinstance(expires_at, str) or not expires_at:
            continue
        try:
            expires_dt = datetime.fromisoformat(expires_at)
        except ValueError:
            continue
        if expires_dt.tzinfo is None:
            expires_dt = expires_dt.replace(tzinfo=timezone.utc)
        if now <= expires_dt <= cutoff:
            out.append(entry)
    return out


def find_expiring_soon_all_tenants(
    *,
    now: datetime,
    threshold_days: int = EXPIRING_SOON_DAYS,
) -> dict[str, list[dict[str, Any]]]:
    """Map of {tenant_id: [expiring rows]} across every tenant directory.

    Tenants with zero expiring rows are omitted from the result.
    """
    out: dict[str, list[dict[str, Any]]] = {}
    for tenant_id in _iter_tenant_ids():
        rows = find_expiring_soon(tenant_id, now=now, threshold_days=threshold_days)
        if rows:
            out[tenant_id] = rows
    return out


def sweep_expired_all_tenants(*, now: datetime) -> dict[str, Any]:
    """Run prune_expired across every tenant directory.

    Returns a dispatcher-friendly summary:
        {
          "swept_at": iso,
          "tenants_swept": int,
          "rows_pruned": int,
          "by_tenant": {tenant_id: count},
        }

    The `now` parameter is included in the summary as `swept_at` so the
    dispatcher tick can emit a stable run record. The data layer's
    prune_expired uses its own internal clock for the comparison; the
    dispatcher tick passes the same `now` it logs elsewhere so traces
    line up.
    """
    by_tenant: dict[str, int] = {}
    total = 0
    tenants = _iter_tenant_ids()
    for tenant_id in tenants:
        try:
            count = tenant_automations.prune_expired(tenant_id)
        except Exception:
            log.exception("promo_lifecycle: prune_expired failed for %s", tenant_id)
            continue
        by_tenant[tenant_id] = count
        total += count
    return {
        "swept_at": now.isoformat(),
        "tenants_swept": len(tenants),
        "rows_pruned": total,
        "by_tenant": by_tenant,
    }


__all__ = [
    "EXPIRING_SOON_DAYS",
    "PromoError",
    "grant_promo",
    "revoke_promo",
    "find_expiring_soon",
    "find_expiring_soon_all_tenants",
    "sweep_expired_all_tenants",
]
