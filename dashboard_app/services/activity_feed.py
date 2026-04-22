"""
Transparency activity feed composer.

Derives the rows the home template's `feed` loop renders from the two
sources the dashboard actually has access to server-side:

  1. Heartbeat snapshots from `services.heartbeat_store`
     (one row per pipeline's most recent run)
  2. Dashboard decisions written to
     `/opt/wc-solns/<tenant>/decisions.jsonl` by
     `api/attention.py`, Apply/Dismiss flows, and any future
     auto-action the agent takes on the tenant's behalf.

Empty state is intentional: a brand-new tenant sees one honest row
telling them the feed wakes up when data flows, rather than an empty
container that looks broken.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import heartbeat_store

_ICON_FALLBACK = "M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"

_PIPELINE_ICONS = {
    "patrol": "M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
    "morning_reports": "M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
    "seo": "M3 3h18v18H3zM16 11l-4 4-4-4M12 15V3",
    "blog": "M4 4h16v4H4zM4 12h16v4H4zM4 20h16",
    "sales_pipeline": "M4 4h16v4H4zM4 12h16v4H4zM4 20h16",
    "reviews": "M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z",
    "ads": "M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83",
    "chat_widget": "M21 11.5a8.38 8.38 0 0 1-9 8.5 8.5 8.5 0 0 1-7.6-4.5L3 21l1.5-3.4A8.38 8.38 0 0 1 3 11.5 8.5 8.5 0 0 1 11.5 3 8.38 8.38 0 0 1 21 11.5z",
    "gbp": "M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z",
    "social": "M22 12h-4l-3 9L9 3l-3 9H2",
    "watchdog": "M12 2 2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5",
}

_ROLE_DISPLAY = {
    "patrol": "Morning Reports",
    "morning_reports": "Morning Reports",
    "sales_pipeline": "Sales Pipeline",
    "seo": "SEO",
    "blog": "Blog Posts",
    "gbp": "Google Business",
    "social": "Social Posts",
    "reviews": "Reviews",
    "qbr": "QBR Generator",
    "chat_widget": "Chat Widget",
    "ads": "Ads",
    "watchdog": "Watchdog",
}


def _role_display(pipeline_id: str) -> str:
    return _ROLE_DISPLAY.get(pipeline_id, pipeline_id.replace("_", " ").title())


def _icon_for(pipeline_id: str) -> str:
    return _PIPELINE_ICONS.get(pipeline_id, _ICON_FALLBACK)


def _parse_iso(iso_ts: str) -> datetime | None:
    if not iso_ts:
        return None
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _humanize(dt: datetime | None) -> tuple[str, str]:
    """Return (clock_time, relative) for a timestamp."""
    if not dt:
        return "", "unknown"
    local = dt.astimezone()
    # Portable (Windows strftime lacks %-I): format with leading zero and strip.
    try:
        clock = local.strftime("%I:%M %p").lstrip("0")
    except ValueError:
        clock = local.strftime("%H:%M")
    delta = datetime.now(timezone.utc) - dt
    secs = int(delta.total_seconds())
    if secs < 60:
        rel = "just now"
    elif secs < 3600:
        rel = f"{secs // 60} min ago"
    elif secs < 86400:
        rel = f"{secs // 3600}h ago"
    else:
        days = secs // 86400
        rel = f"{days} day{'s' if days != 1 else ''} ago"
    return clock, rel


def _action_text_from_heartbeat(status: str, summary: str, role_name: str) -> str:
    """Client-friendly sentence for a pipeline run."""
    summary = (summary or "").strip()
    s = (status or "").lower()
    if summary:
        # Trim to one sentence for feed row hygiene.
        short = summary.split(".")[0].strip()
        if len(short) > 140:
            short = short[:137].rstrip() + "..."
        if s == "error":
            return f"Ran into a problem: {short}."
        if s == "paused":
            return f"Is paused. {short}."
        return f"{short}."
    if s == "error":
        return "Ran and hit an error. Open it to see the log."
    if s == "paused":
        return "Is paused."
    if s == "ok":
        return "Completed its run."
    return "Completed its run."


def _row_from_heartbeat(snap: dict[str, Any]) -> dict[str, Any] | None:
    pid = snap.get("pipeline_id", "")
    if not pid:
        return None
    payload = snap.get("payload") or {}
    role_name = _role_display(pid)
    run_ts = _parse_iso(payload.get("last_run") or snap.get("received_at", ""))
    clock, rel = _humanize(run_ts)
    return {
        "time": clock or "",
        "role": role_name,
        "role_slug": pid.replace("_", "-"),
        "icon_path": _icon_for(pid),
        "action": _action_text_from_heartbeat(payload.get("status", ""), payload.get("summary", ""), role_name),
        "link": None,
        "link_text": None,
        "relative": rel,
        "_sort_ts": (run_ts or datetime.fromtimestamp(0, tz=timezone.utc)).isoformat(),
    }


def _decisions_path(tenant_id: str) -> Path:
    return heartbeat_store.tenant_root(tenant_id) / "decisions.jsonl"


def append_decision(tenant_id: str, actor: str, kind: str, text: str, link: str | None = None) -> None:
    """Called by attention / apply flows to log a user-facing decision."""
    path = _decisions_path(tenant_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tenant_id": tenant_id,
        "actor": actor,
        "kind": kind,
        "text": text,
    }
    if link:
        entry["link"] = link
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def _decision_rows(tenant_id: str, max_rows: int) -> list[dict[str, Any]]:
    path = _decisions_path(tenant_id)
    if not path.exists():
        return []
    lines: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    lines.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    lines.sort(key=lambda row: row.get("ts", ""), reverse=True)
    rows: list[dict[str, Any]] = []
    for entry in lines[:max_rows]:
        ts = _parse_iso(entry.get("ts", ""))
        clock, rel = _humanize(ts)
        rows.append({
            "time": clock or "",
            "role": "Dashboard",
            "role_slug": "dashboard",
            "icon_path": "M9 11l3 3L22 4M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11",
            "action": entry.get("text", ""),
            "link": entry.get("link"),
            "link_text": "View" if entry.get("link") else None,
            "relative": rel,
            "_sort_ts": entry.get("ts", ""),
        })
    return rows


def build(tenant_id: str, max_rows: int = 12) -> list[dict[str, Any]]:
    """Return the feed rows for the home surface, newest first."""
    rows: list[dict[str, Any]] = []
    try:
        snaps = heartbeat_store.read_all(tenant_id)
    except heartbeat_store.HeartbeatError:
        snaps = []

    for snap in snaps:
        row = _row_from_heartbeat(snap)
        if row:
            rows.append(row)

    try:
        rows.extend(_decision_rows(tenant_id, max_rows=max_rows))
    except heartbeat_store.HeartbeatError:
        pass

    rows.sort(key=lambda r: r.get("_sort_ts", ""), reverse=True)
    trimmed = []
    for r in rows[:max_rows]:
        r.pop("_sort_ts", None)
        trimmed.append(r)

    if trimmed:
        return trimmed

    return [{
        "time": "",
        "role": "Dashboard",
        "role_slug": "dashboard",
        "icon_path": _ICON_FALLBACK,
        "action": "Your activity feed wakes up on the first heartbeat. New events land here within seconds of each run.",
        "link": None,
        "link_text": None,
        "relative": "waiting",
    }]
