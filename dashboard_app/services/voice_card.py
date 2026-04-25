"""
Per-tenant voice card persistence.

The Activation Orchestrator agent extracts a 'voice profile' from the
client's website during turn 1 (fetch_site_facts -> Opus extraction).
That profile lives here as structured JSON the wizard UI can render
as a side-by-side comparison panel:

    /opt/wc-solns/<tenant_id>/state_snapshot/voice_card.json

Shape:
    {
      "card_id": "vc_<8-char-uuid>",
      "generated_at": "2026-04-25T03:14:01+00:00",
      "traits": ["warm", "family-oriented", "bilingual"],
      "generic_sample": "Hi! Don't forget your appointment tomorrow.",
      "voice_sample": "Hola familia, tomorrow's class is at 6 sharp.",
      "sample_context": "re-engagement reminder text",
      "source_pages": ["https://garciafolklorico.com/about"],
      "accepted": false,
      "accepted_at": null
    }

Single payload per tenant (overwrite on accept/edit). The tenant_kb
voice.md file is the human-readable mirror; this file is the panel
data the UI renders.
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
    return root / "voice_card.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def save(
    tenant_id: str,
    *,
    traits: list[str],
    generic_sample: str,
    voice_sample: str,
    sample_context: str = "",
    source_pages: list[str] | None = None,
) -> dict[str, Any]:
    """Persist a fresh voice card. Returns the stored payload (with card_id)."""
    payload: dict[str, Any] = {
        "card_id": "vc_" + secrets.token_hex(4),
        "generated_at": _now_iso(),
        "traits": [str(t).strip() for t in (traits or []) if str(t).strip()][:6],
        "generic_sample": (generic_sample or "").strip(),
        "voice_sample": (voice_sample or "").strip(),
        "sample_context": (sample_context or "").strip(),
        "source_pages": [str(p) for p in (source_pages or [])][:5],
        "accepted": False,
        "accepted_at": None,
    }
    path = _path(tenant_id)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return payload


def load(tenant_id: str) -> dict[str, Any] | None:
    """Return the stored card or None if no card has been generated."""
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
    card_id: str,
    edits: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Apply optional owner edits, flip accepted=True, persist. Returns the
    updated payload or None if no card exists / card_id mismatch."""
    current = load(tenant_id)
    if not current or current.get("card_id") != card_id:
        return None
    edits = edits or {}
    if "traits" in edits and isinstance(edits["traits"], list):
        current["traits"] = [str(t).strip() for t in edits["traits"] if str(t).strip()][:6]
    if "voice_sample" in edits and isinstance(edits["voice_sample"], str):
        current["voice_sample"] = edits["voice_sample"].strip()
    current["accepted"] = True
    current["accepted_at"] = _now_iso()
    path = _path(tenant_id)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(current, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return current
