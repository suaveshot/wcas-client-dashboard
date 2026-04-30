"""
Heartbeat receiver storage.

Each PC-side pipeline calls `push_heartbeat.py` at the end of its run,
which POSTs a small JSON payload to `/api/heartbeat`. We persist those
payloads under the tenant's private directory so the dashboard can
read them back as live telemetry without re-running anything on the PC.

Layout:
    /opt/wc-solns/<tenant_id>/state_snapshot/<pipeline_id>.json

We overwrite on each push; only the most recent snapshot matters for
the grid. History is in the PC-side log files; if we need it server-
side later, we append to a rotating JSONL next to each snapshot.

Two invariants enforced here:
  1. pipeline_id must be a safe filename slug ([a-z0-9_-]+)
  2. tenant_id must be resolvable via Airtable Clients.Tenant ID
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SAFE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class HeartbeatError(ValueError):
    pass


def tenant_root(tenant_id: str) -> Path:
    if not _SAFE.match(tenant_id or ""):
        raise HeartbeatError("invalid tenant_id")
    base = os.getenv("TENANT_ROOT", "/opt/wc-solns")
    return Path(base) / tenant_id


def write_snapshot(tenant_id: str, pipeline_id: str, payload: dict[str, Any]) -> Path:
    if not _SAFE.match(pipeline_id or ""):
        raise HeartbeatError("invalid pipeline_id")
    root = tenant_root(tenant_id) / "state_snapshot"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{pipeline_id}.json"
    stored = {
        "pipeline_id": pipeline_id,
        "tenant_id": tenant_id,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(stored, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def read_all(tenant_id: str) -> list[dict[str, Any]]:
    """All stored snapshots for a tenant, most recent first.

    Other services (voice_card, crm_mapping) co-locate their JSON under
    state_snapshot/. Filter on the heartbeat shape so those files are not
    miscounted as pipeline heartbeats - which would otherwise flip a
    cold-start tenant out of the cold-start branch the moment they save
    a voice card or CRM mapping during activation.
    """
    root = tenant_root(tenant_id) / "state_snapshot"
    if not root.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in root.glob("*.json"):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(row, dict):
            continue
        pid = row.get("pipeline_id")
        if not isinstance(pid, str) or not pid:
            continue
        rows.append(row)
    rows.sort(key=lambda r: r.get("received_at", ""), reverse=True)
    return rows
