"""
Per-tenant preferences stored at /opt/wc-solns/<tenant>/prefs.json.

Shape:
    {
        "privacy_default": bool,
        "feed_dense_default": bool,
        "email_digest": bool,
        "errors_only": bool,
        "timezone": str,
        "require_approval": {"<pipeline_id>": bool, ...}
    }

Not secret - safe to read and write freely by owner-level sessions.
"""

from __future__ import annotations

import json
from typing import Any

from . import heartbeat_store


DEFAULTS: dict[str, Any] = {
    "privacy_default": False,
    "feed_dense_default": False,
    "email_digest": True,
    "errors_only": False,
    "timezone": "America/Los_Angeles",
    "require_approval": {},
}


def _path(tenant_id: str):
    return heartbeat_store.tenant_root(tenant_id) / "prefs.json"


def read(tenant_id: str) -> dict[str, Any]:
    try:
        path = _path(tenant_id)
    except heartbeat_store.HeartbeatError:
        return dict(DEFAULTS)
    if not path.exists():
        return dict(DEFAULTS)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULTS)
    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in data.items() if k in DEFAULTS})
    # require_approval is a dict of pipeline -> bool
    ra = data.get("require_approval")
    if isinstance(ra, dict):
        merged["require_approval"] = {str(k): bool(v) for k, v in ra.items()}
    return merged


def write(tenant_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    current = read(tenant_id)
    for key, value in (updates or {}).items():
        if key in DEFAULTS:
            current[key] = value
    path = _path(tenant_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, indent=2), encoding="utf-8")
    return current


def set_require_approval(tenant_id: str, pipeline_id: str, on: bool) -> dict[str, Any]:
    current = read(tenant_id)
    ra = dict(current.get("require_approval") or {})
    ra[pipeline_id] = bool(on)
    current["require_approval"] = ra
    path = _path(tenant_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, indent=2), encoding="utf-8")
    return current
