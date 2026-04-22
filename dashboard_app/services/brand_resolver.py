"""
Per-tenant brand override.

Platformization Seed #2: tenants can drop `brand.json` into their
/opt/wc-solns/<tenant>/ directory to override the WCAS defaults.
Anything missing falls through to the defaults. The result gets
served as CSS custom properties the home template injects inline.

This is a read-only resolver; theme editing UI ships post-hackathon.
"""

import json
from pathlib import Path
from typing import Any

from . import heartbeat_store  # for tenant_root()

_DEFAULTS: dict[str, Any] = {
    "primary_color": "#E97B2E",
    "accent_color": "#E97B2E",
    "ink": "#121212",
    "sand": "#FBFAF7",
    "font_display": "DM Serif Display",
    "font_body": "DM Sans",
    "logo_url": None,
    "display_name": None,
}

_ALLOWED_KEYS = set(_DEFAULTS.keys())


def _load_override(tenant_id: str) -> dict[str, Any]:
    try:
        root = heartbeat_store.tenant_root(tenant_id)
    except heartbeat_store.HeartbeatError:
        return {}
    path = root / "brand.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if k in _ALLOWED_KEYS and v is not None}


def resolve(tenant_id: str) -> dict[str, Any]:
    merged = dict(_DEFAULTS)
    merged.update(_load_override(tenant_id))
    return merged


def as_css_vars(tenant_id: str) -> str:
    brand = resolve(tenant_id)
    lines = []
    if brand.get("primary_color"):
        lines.append(f"--accent: {brand['primary_color']};")
    if brand.get("ink"):
        lines.append(f"--ink: {brand['ink']};")
    if brand.get("sand"):
        lines.append(f"--sand: {brand['sand']};")
    return " ".join(lines)
