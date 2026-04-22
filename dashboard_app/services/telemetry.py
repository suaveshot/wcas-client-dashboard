"""
Per-tenant telemetry reader.

Reads the state snapshots pushed in by PC-side pipelines (see
services.heartbeat_store) and returns a normalized list the
dashboard and JSON API can both render. For tenants who haven't
pushed anything yet, we return an empty list and let the UI show
its brand-specific empty state ("Your first run arrives tomorrow at
7am.").

One level up from here (api.pipelines) turns this into the /api/
response; the Home template reads its own Jinja context (still mock
in Day 2) and will cut over to this source in Day 3 when real pinned-
roles + hero stats land.
"""

from typing import Any

from . import heartbeat_store


def pipelines_for(tenant_id: str) -> list[dict[str, Any]]:
    """Return [{pipeline_id, status, last_run, summary, age_seconds}, ...]."""
    rows = heartbeat_store.read_all(tenant_id)
    result: list[dict[str, Any]] = []
    for row in rows:
        payload = row.get("payload") or {}
        result.append({
            "pipeline_id": row.get("pipeline_id", "unknown"),
            "status": payload.get("status", "unknown"),
            "last_run": payload.get("last_run") or row.get("received_at", ""),
            "summary": payload.get("summary", ""),
            "received_at": row.get("received_at", ""),
        })
    return result
