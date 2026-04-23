"""
Notification composer for the bell badge + notification drawer.

Sources:
    * decisions.jsonl unread entries (tenant acknowledges via notifications_read.json)
    * currently-erroring pipelines
    * pending approvals older than 4 hours (stale drafts)

Persistence for read-state: `/opt/wc-solns/<tenant>/notifications_read.json`
    shape: {"last_seen_ts": "<iso>"}

The bell simply reads the unread count. Marking-read bumps last_seen_ts to
now(); any decision/error/draft older than that is considered "seen."
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from . import activity_feed, heartbeat_store, outgoing_queue


def _read_state_path(tenant_id: str):
    return heartbeat_store.tenant_root(tenant_id) / "notifications_read.json"


def _last_seen(tenant_id: str) -> str:
    try:
        path = _read_state_path(tenant_id)
    except heartbeat_store.HeartbeatError:
        return ""
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return data.get("last_seen_ts", "") or ""


def mark_all_read(tenant_id: str) -> None:
    try:
        path = _read_state_path(tenant_id)
    except heartbeat_store.HeartbeatError:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"last_seen_ts": datetime.now(timezone.utc).isoformat()}),
        encoding="utf-8",
    )


def _compose_entries(tenant_id: str, since_ts: str, limit: int) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []

    # Unread decisions
    try:
        rows = activity_feed._decision_rows(tenant_id, max_rows=limit * 2)
    except heartbeat_store.HeartbeatError:
        rows = []
    for r in rows:
        if since_ts and r.get("_sort_ts", "") <= since_ts:
            continue
        entries.append({
            "kind": "decision",
            "title": r.get("action", ""),
            "timestamp": r.get("_sort_ts", "") or r.get("relative", ""),
            "link": r.get("link"),
        })

    # Erroring pipelines
    try:
        snaps = heartbeat_store.read_all(tenant_id)
    except heartbeat_store.HeartbeatError:
        snaps = []
    for snap in snaps:
        payload = snap.get("payload") or {}
        if (payload.get("status") or "").lower() == "error":
            entries.append({
                "kind": "error",
                "title": f"{snap.get('pipeline_id', 'unknown')} is erroring",
                "timestamp": payload.get("last_run") or snap.get("received_at", ""),
                "link": f"/roles/{(snap.get('pipeline_id','') or '').replace('_', '-')}",
            })

    # Stale pending approvals (older than 4 hours)
    try:
        pending = outgoing_queue.list_pending(tenant_id)
    except heartbeat_store.HeartbeatError:
        pending = []
    now = datetime.now(timezone.utc)
    for d in pending:
        try:
            created = datetime.fromisoformat((d.get("created_at") or "").replace("Z", "+00:00"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            hours = (now - created).total_seconds() / 3600
        except (ValueError, TypeError):
            continue
        if hours >= 4:
            entries.append({
                "kind": "approval_stale",
                "title": f"Draft awaiting approval ({int(hours)}h): {d.get('subject') or d.get('pipeline_id')}",
                "timestamp": d.get("created_at", ""),
                "link": "/approvals",
            })

    entries.sort(key=lambda e: e.get("timestamp") or "", reverse=True)
    return entries[:limit]


def count(tenant_id: str) -> int:
    since = _last_seen(tenant_id)
    entries = _compose_entries(tenant_id, since_ts=since, limit=99)
    # Decisions before last_seen already excluded; errors/approval_stale are
    # treated as always-unread (they're state, not history).
    return len(entries)


def list_for_bell(tenant_id: str, limit: int = 10) -> dict[str, Any]:
    since = _last_seen(tenant_id)
    entries = _compose_entries(tenant_id, since_ts=since, limit=limit)
    total = count(tenant_id)
    return {"count": total, "entries": entries, "last_seen": since}
