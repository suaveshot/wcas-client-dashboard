"""
Per-tenant recommendation persistence.

Each refresh writes one file at:
    /opt/wc-solns/<tenant_id>/recs/<YYYY-MM-DD>.json

Shape:
    {
      "generated_at": "2026-04-23T22:14:01+00:00",
      "model": "claude-opus-4-7",
      "usd": 0.043,
      "input_tokens": 18234,
      "output_tokens": 612,
      "count": 4,
      "recs": [ {...}, {...}, ... ]
    }

Multiple writes on the same day overwrite each other (newest refresh wins).
History is preserved across days for the future "What changed since last
week?" view; we don't render it tonight.

Callers prefer recs_store over seeded_recs when a fresh file exists; if
the latest file is older than `MAX_FRESHNESS_HOURS`, callers should treat
it as stale and fall back to the seeded path.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import heartbeat_store


MAX_FRESHNESS_HOURS = 48  # treat files older than this as stale


def _recs_root(tenant_id: str) -> Path:
    """Path to the tenant's recs directory. Path-traversal guarded via
    heartbeat_store.tenant_root."""
    return heartbeat_store.tenant_root(tenant_id) / "recs"


def write_today(
    tenant_id: str,
    *,
    recs: list[dict[str, Any]],
    model: str,
    usd: float,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> Path:
    """Atomically write today's recs file and return the path."""
    root = _recs_root(tenant_id)
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    path = root / f"{now.date().isoformat()}.json"
    payload = {
        "generated_at": now.isoformat(),
        "model": model,
        "usd": round(float(usd), 6),
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "count": len(recs),
        "recs": recs,
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=False, default=str), encoding="utf-8")
    os.replace(tmp, path)
    return path


def read_latest(tenant_id: str) -> dict[str, Any] | None:
    """Return the freshest recs payload for this tenant, or None if no
    file exists. Does NOT enforce freshness; callers decide via
    `is_fresh()`."""
    try:
        root = _recs_root(tenant_id)
    except heartbeat_store.HeartbeatError:
        return None
    if not root.exists():
        return None
    files = sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return None
    try:
        return json.loads(files[0].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def is_fresh(payload: dict[str, Any] | None, max_hours: float = MAX_FRESHNESS_HOURS) -> bool:
    """True when the payload was generated within max_hours."""
    if not payload:
        return False
    ts = payload.get("generated_at")
    if not ts:
        return False
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return False
    age_hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    return age_hours <= max_hours


def list_dates(tenant_id: str) -> list[str]:
    """ISO dates of every recs file for this tenant, newest first.

    Used by the future 'recs history' view; no UI consumes it tonight."""
    try:
        root = _recs_root(tenant_id)
    except heartbeat_store.HeartbeatError:
        return []
    if not root.exists():
        return []
    dates = []
    for path in root.glob("*.json"):
        stem = path.stem  # YYYY-MM-DD
        try:
            datetime.fromisoformat(stem)
        except ValueError:
            continue
        dates.append(stem)
    return sorted(dates, reverse=True)
