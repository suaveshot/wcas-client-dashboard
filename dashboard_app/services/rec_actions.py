"""
Per-tenant rec action history.

Companion to api/recs.py - reads back the JSONL file the API endpoint
writes when an owner clicks Apply or Dismiss on a recommendation card.
Used by the home + recommendations surfaces to filter out recs the owner
has already acted on so the dashboard stays clean across page loads.

Layout:
    /opt/wc-solns/<tenant_id>/rec_actions.jsonl

Each line:
    {"ts": "...", "rec_id": "...", "action": "apply|dismiss"}
"""

from __future__ import annotations

import json
import logging
from typing import Set

from . import heartbeat_store

log = logging.getLogger("dashboard.rec_actions")


def _path(tenant_id: str):
    return heartbeat_store.tenant_root(tenant_id) / "rec_actions.jsonl"


def acted_ids(tenant_id: str) -> Set[str]:
    """Set of rec_ids that have an apply or dismiss entry on file.

    Failures (file missing, malformed line) are swallowed and treated as
    empty - the dashboard never breaks because the action log is corrupt.
    """
    try:
        path = _path(tenant_id)
    except heartbeat_store.HeartbeatError:
        return set()
    if not path.exists():
        return set()
    out: Set[str] = set()
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rec_id = row.get("rec_id")
                if isinstance(rec_id, str) and rec_id:
                    out.add(rec_id)
    except OSError:
        log.warning("rec_actions read failed for tenant=%s", tenant_id)
    return out


def filter_unacted(tenant_id: str, recs: list[dict]) -> list[dict]:
    """Return recs minus any whose id is in the action history."""
    if not recs:
        return recs
    acted = acted_ids(tenant_id)
    if not acted:
        return list(recs)
    return [r for r in recs if r.get("id") not in acted]
