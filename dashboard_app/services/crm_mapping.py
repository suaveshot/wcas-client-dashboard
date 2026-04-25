"""
Per-tenant CRM mapping persistence.

After the agent calls fetch_airtable_schema and proposes how to map
the tenant's CRM to a WCAS automation playbook, the result lives at:

    /opt/wc-solns/<tenant_id>/state_snapshot/crm_mapping.json

Shape:
    {
      "mapping_id": "cm_<8-char-uuid>",
      "generated_at": "2026-04-25T03:14:01+00:00",
      "base_id": "appXXX",
      "table_name": "Bookings",
      "field_mapping": {
        "first_name": "Student Name",
        "last_engagement": "Last Class Date",
        "contact_email": "Email"
      },
      "segments": [
        {"slug": "active", "label": "Active students", "count": 47, "sample_names": ["Maria S.", "..."]},
        {"slug": "inactive_30d", "label": "Inactive 30+ days", "count": 12, "sample_names": ["Maria Sanchez", "..."]},
        {"slug": "brand_new", "label": "Brand new this month", "count": 3, "sample_names": ["..."]}
      ],
      "proposed_actions": [
        {"segment": "inactive_30d", "playbook": "re_engagement", "automation": "email_assistant"},
        {"segment": "brand_new", "playbook": "welcome_series", "automation": "email_assistant"}
      ],
      "accepted": false,
      "accepted_at": null
    }

Sample names are scrubbed by the airtable_schema layer before they
reach this file. The simulation endpoint reads the FIRST sample name
of the inactive_30d segment to build the deterministic demo email.
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import heartbeat_store


def _path(tenant_id: str) -> Path:
    root = heartbeat_store.tenant_root(tenant_id) / "state_snapshot"
    root.mkdir(parents=True, exist_ok=True)
    return root / "crm_mapping.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def save(
    tenant_id: str,
    *,
    base_id: str,
    table_name: str,
    field_mapping: dict[str, str],
    segments: list[dict[str, Any]],
    proposed_actions: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Persist a fresh CRM mapping. Returns the stored payload."""
    cleaned_segments: list[dict[str, Any]] = []
    for seg in segments or []:
        if not isinstance(seg, dict):
            continue
        cleaned_segments.append({
            "slug": str(seg.get("slug", "")).strip()[:40],
            "label": str(seg.get("label", "")).strip()[:80],
            "count": int(seg.get("count", 0) or 0),
            "sample_names": [str(n).strip()[:80] for n in (seg.get("sample_names") or [])][:5],
        })
    payload: dict[str, Any] = {
        "mapping_id": "cm_" + secrets.token_hex(4),
        "generated_at": _now_iso(),
        "base_id": str(base_id or "")[:32],
        "table_name": str(table_name or "")[:80],
        "field_mapping": {str(k): str(v) for k, v in (field_mapping or {}).items()},
        "segments": cleaned_segments,
        "proposed_actions": [
            {
                "segment": str(a.get("segment", "")).strip(),
                "playbook": str(a.get("playbook", "")).strip(),
                "automation": str(a.get("automation", "")).strip(),
            }
            for a in (proposed_actions or [])
            if isinstance(a, dict)
        ],
        "accepted": False,
        "accepted_at": None,
    }
    path = _path(tenant_id)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return payload


def load(tenant_id: str) -> dict[str, Any] | None:
    try:
        path = _path(tenant_id)
    except heartbeat_store.HeartbeatError:
        return None
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def mark_accepted(
    tenant_id: str,
    *,
    mapping_id: str,
    edits: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Apply optional owner edits, flip accepted=True, persist. Returns the
    updated payload or None if no mapping exists / mapping_id mismatch."""
    current = load(tenant_id)
    if not current or current.get("mapping_id") != mapping_id:
        return None
    edits = edits or {}
    if "field_mapping" in edits and isinstance(edits["field_mapping"], dict):
        current["field_mapping"] = {str(k): str(v) for k, v in edits["field_mapping"].items()}
    current["accepted"] = True
    current["accepted_at"] = _now_iso()
    path = _path(tenant_id)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(current, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return current


def first_inactive_for_simulation(tenant_id: str) -> dict[str, Any] | None:
    """Return the first named record of the inactive_30d segment, with the
    days_inactive estimate the simulation endpoint passes to the prompt.

    Deterministic so the demo is repeatable: picks segment[slug='inactive_30d']
    and returns its first sample_name.
    """
    payload = load(tenant_id)
    if not payload:
        return None
    for seg in payload.get("segments", []):
        if seg.get("slug") != "inactive_30d":
            continue
        names = seg.get("sample_names") or []
        if not names:
            return None
        return {"name": names[0], "days_inactive": 37, "segment": seg}
    return None
