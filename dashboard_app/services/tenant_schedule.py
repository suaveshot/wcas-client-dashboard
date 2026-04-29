"""Per-tenant pipeline scheduling.

Each tenant declares when their enabled automations should fire by
storing a cron-style schedule under:

    /opt/wc-solns/<tenant_id>/config/schedule.json

Phase 2D's VPS-side scheduler dispatcher (lands later) reads every
tenant's schedule.json on each tick, picks the entries due to fire,
and runs the relevant generic pipeline with TENANT_ID set. Until that
runner lands, this module is the read/write contract the dashboard
admin UI + tier-default seeder use.

File shape:

    {
      "version": 1,
      "tenant_id": "garcia_folklorico",
      "updated_at": "2026-04-29T18:04:01+00:00",
      "entries": [
        {
          "pipeline_id": "reviews",
          "cron": "0 8 * * *",
          "enabled": true,
          "last_modified_at": "2026-04-29T18:04:01+00:00",
          "source": "tier_default"
        },
        ...
      ]
    }

Source enum mirrors tenant_automations:
    tier_default - seeded from the tier catalog at activation
    admin_added  - Sam set/changed it manually
    owner_change - the owner edited cadence in the dashboard UI

Atomic writes via tmp + os.replace - same pattern as every other
config file in this layer.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import automation_catalog, heartbeat_store

log = logging.getLogger(__name__)

CONFIG_DIR = "config"
CONFIG_FILENAME = "schedule.json"
SCHEMA_VERSION = 1
VALID_SOURCES = frozenset({"tier_default", "admin_added", "owner_change"})


class ScheduleError(ValueError):
    """Invalid input to a tenant_schedule operation."""


# ---------------------------------------------------------------------------
# cron validation
# ---------------------------------------------------------------------------

# Standard 5-field cron: minute hour day-of-month month day-of-week.
# Each field accepts: number | range (a-b) | step (*/n, a-b/n) | list (a,b,c)
# | wildcard (*). We validate structure + numeric ranges per field.
_FIELD_RANGES: tuple[tuple[int, int], ...] = (
    (0, 59),  # minute
    (0, 23),  # hour
    (1, 31),  # day-of-month
    (1, 12),  # month
    (0, 6),   # day-of-week (0 = Sunday)
)

_TOKEN_RE = re.compile(r"^(?:\*|\d+|\d+-\d+)(?:/\d+)?$")


def _validate_cron_field(token: str, lo: int, hi: int) -> bool:
    for piece in token.split(","):
        if not _TOKEN_RE.match(piece):
            return False
        # Strip step suffix for range check.
        head, _, _step = piece.partition("/")
        if head == "*":
            continue
        if "-" in head:
            a_s, b_s = head.split("-", 1)
            try:
                a, b = int(a_s), int(b_s)
            except ValueError:
                return False
            if not (lo <= a <= hi and lo <= b <= hi and a <= b):
                return False
        else:
            try:
                v = int(head)
            except ValueError:
                return False
            if not (lo <= v <= hi):
                return False
    return True


def is_valid_cron(expr: str) -> bool:
    """True when `expr` is a syntactically valid 5-field cron string."""
    if not isinstance(expr, str):
        return False
    parts = expr.strip().split()
    if len(parts) != 5:
        return False
    return all(
        _validate_cron_field(parts[i], lo, hi)
        for i, (lo, hi) in enumerate(_FIELD_RANGES)
    )


# ---------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------


def _config_path(tenant_id: str) -> Path:
    return heartbeat_store.tenant_root(tenant_id) / CONFIG_DIR / CONFIG_FILENAME


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_doc(tenant_id: str) -> dict[str, Any]:
    return {
        "version": SCHEMA_VERSION,
        "tenant_id": tenant_id,
        "updated_at": _now_iso(),
        "entries": [],
    }


def _read(tenant_id: str) -> dict[str, Any]:
    """Read the raw schedule.json. Returns the empty document when the
    file is missing or malformed - never raises on reader errors."""
    try:
        path = _config_path(tenant_id)
    except heartbeat_store.HeartbeatError:
        return _empty_doc(tenant_id)
    if not path.exists():
        return _empty_doc(tenant_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("tenant_schedule: malformed schedule.json for %s", tenant_id)
        return _empty_doc(tenant_id)
    if not isinstance(data, dict):
        return _empty_doc(tenant_id)
    entries_raw = data.get("entries")
    if not isinstance(entries_raw, list):
        entries_raw = []
    cleaned: list[dict[str, Any]] = []
    for e in entries_raw:
        if not isinstance(e, dict):
            continue
        pid = e.get("pipeline_id")
        cron = e.get("cron")
        if not isinstance(pid, str) or not isinstance(cron, str):
            continue
        cleaned.append(e)
    return {
        "version": data.get("version", SCHEMA_VERSION),
        "tenant_id": data.get("tenant_id", tenant_id),
        "updated_at": data.get("updated_at", _now_iso()),
        "entries": cleaned,
    }


def _write(tenant_id: str, doc: dict[str, Any]) -> Path:
    path = _config_path(tenant_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc["updated_at"] = _now_iso()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)
    return path


# ---------------------------------------------------------------------------
# read API
# ---------------------------------------------------------------------------


def list_entries(tenant_id: str, *, enabled_only: bool = False) -> list[dict[str, Any]]:
    """All schedule entries for a tenant. enabled_only=True filters to
    rows actively scheduled (the dispatcher uses that filter)."""
    entries = _read(tenant_id).get("entries", [])
    if not enabled_only:
        return list(entries)
    return [e for e in entries if e.get("enabled", True)]


def get_entry(tenant_id: str, pipeline_id: str) -> dict[str, Any] | None:
    for e in _read(tenant_id).get("entries", []):
        if e.get("pipeline_id") == pipeline_id:
            return dict(e)
    return None


def is_enabled(tenant_id: str, pipeline_id: str) -> bool:
    entry = get_entry(tenant_id, pipeline_id)
    return bool(entry and entry.get("enabled", True))


# ---------------------------------------------------------------------------
# write API
# ---------------------------------------------------------------------------


def set_entry(
    tenant_id: str,
    pipeline_id: str,
    cron: str,
    *,
    enabled: bool = True,
    source: str = "admin_added",
) -> dict[str, Any]:
    """Create or update a schedule entry. Idempotent: re-calling with the
    same args produces the same row (only last_modified_at moves)."""
    if not automation_catalog.exists(pipeline_id):
        raise ScheduleError(f"unknown pipeline_id: {pipeline_id!r}")
    if not is_valid_cron(cron):
        raise ScheduleError(f"invalid cron expression: {cron!r}")
    if source not in VALID_SOURCES:
        raise ScheduleError(f"invalid source {source!r}; expected one of {VALID_SOURCES}")

    doc = _read(tenant_id)
    entries = doc["entries"]
    new_entry: dict[str, Any] = {
        "pipeline_id": pipeline_id,
        "cron": cron,
        "enabled": bool(enabled),
        "last_modified_at": _now_iso(),
        "source": source,
    }
    idx = next(
        (i for i, e in enumerate(entries) if e.get("pipeline_id") == pipeline_id),
        -1,
    )
    if idx >= 0:
        entries[idx] = new_entry
    else:
        entries.append(new_entry)
    _write(tenant_id, doc)
    return new_entry


def enable(tenant_id: str, pipeline_id: str) -> bool:
    """Flip an existing entry's enabled flag to True. Returns False when
    the entry doesn't exist (use set_entry to create one)."""
    doc = _read(tenant_id)
    for e in doc["entries"]:
        if e.get("pipeline_id") == pipeline_id:
            if e.get("enabled") is True:
                return True
            e["enabled"] = True
            e["last_modified_at"] = _now_iso()
            _write(tenant_id, doc)
            return True
    return False


def disable(tenant_id: str, pipeline_id: str) -> bool:
    """Flip an existing entry's enabled flag to False. Returns True when
    a flip happened, False when the entry was already disabled or absent.

    Disable does NOT remove the entry - the cron string + source survive
    so re-enabling later is one click instead of a full reconfiguration.
    """
    doc = _read(tenant_id)
    for e in doc["entries"]:
        if e.get("pipeline_id") == pipeline_id:
            if e.get("enabled") is False:
                return False
            e["enabled"] = False
            e["last_modified_at"] = _now_iso()
            _write(tenant_id, doc)
            return True
    return False


def remove(tenant_id: str, pipeline_id: str) -> bool:
    """Drop the entry entirely. Returns True on remove, False on no-op."""
    doc = _read(tenant_id)
    before = len(doc["entries"])
    doc["entries"] = [e for e in doc["entries"] if e.get("pipeline_id") != pipeline_id]
    if len(doc["entries"]) == before:
        return False
    _write(tenant_id, doc)
    return True


# ---------------------------------------------------------------------------
# tier-aware defaults (shared with seed_for_tier)
# ---------------------------------------------------------------------------

# Default cron string per automation. The catalog stores cadence as a
# human label (e.g. "Hourly during business hours", "Mon 7am"); this
# table maps each id to a concrete cron expression the dispatcher can
# act on. Anything not listed falls back to the daily default.
_DEFAULT_CRON_BY_ID: dict[str, str] = {
    # Core 7
    "gbp":              "0 10 * * 1",     # Mondays 10am
    "seo":              "0 7 * * 1",      # Mondays 7am
    "reviews":          "0 9-17 * * *",   # business hours, hourly
    "blog":             "0 9 1-7 * 1",    # 1st Monday 9am
    "social":           "0 10 * * 2,4,6", # Tue/Thu/Sat 10am
    "email_assistant":  "*/15 * * * *",   # every 15 minutes
    "chat_widget":      "*/5 * * * *",    # heartbeat every 5 minutes
    # Add-ons
    "voice_ai":         "*/5 * * * *",
    "seo_recs":         "0 7 * * 1",      # Mondays 7am, after SEO
    "review_engine":    "0 11 * * 1",     # Mondays 11am
    "win_back":         "0 8 * * 2",      # Tuesdays 8am
    # AP-only (kept for completeness; AP runs on its own scheduler today)
    "daily_reports":    "0 7 * * *",
    "guard_compliance": "0 6 * * *",
    "incident_trends":  "0 8 * * *",
}

_DEFAULT_FALLBACK_CRON = "0 8 * * *"  # daily 8am


def default_cron_for(pipeline_id: str) -> str:
    """Return the canonical default cron for an automation id."""
    return _DEFAULT_CRON_BY_ID.get(pipeline_id, _DEFAULT_FALLBACK_CRON)


def seed_for_tier(
    tenant_id: str,
    tier: str,
    *,
    overwrite: bool = False,
) -> list[dict[str, Any]]:
    """Seed schedule.json with default cron entries for every tier_default
    automation. Idempotent by default: existing tier_default rows are
    preserved (so an owner who tweaked their cron isn't reset).

    Pass overwrite=True to wipe + reseed (e.g. on tier change).
    """
    if tier not in automation_catalog.VALID_TIERS:
        raise ScheduleError(f"unknown tier {tier!r}")

    doc = _read(tenant_id)
    existing_by_id = {e.get("pipeline_id"): e for e in doc["entries"]}

    new_entries: list[dict[str, Any]] = []
    for aid in automation_catalog.tier_default_ids(tier):
        prior = existing_by_id.get(aid)
        if prior is not None and not overwrite:
            new_entries.append(prior)
            continue
        new_entries.append({
            "pipeline_id": aid,
            "cron": default_cron_for(aid),
            "enabled": True,
            "last_modified_at": _now_iso(),
            "source": "tier_default",
        })

    # Preserve admin_added + owner_change rows that aren't in the tier set.
    keep_other = [
        e for e in doc["entries"]
        if e.get("source") in ("admin_added", "owner_change")
        and e.get("pipeline_id") not in {x["pipeline_id"] for x in new_entries}
    ]

    doc["entries"] = new_entries + keep_other
    _write(tenant_id, doc)
    return doc["entries"]


__all__ = [
    "CONFIG_DIR",
    "CONFIG_FILENAME",
    "SCHEMA_VERSION",
    "VALID_SOURCES",
    "ScheduleError",
    "default_cron_for",
    "disable",
    "enable",
    "get_entry",
    "is_enabled",
    "is_valid_cron",
    "list_entries",
    "remove",
    "seed_for_tier",
    "set_entry",
]
