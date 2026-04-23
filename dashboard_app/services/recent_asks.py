"""
Recent ask history, per-tenant.

Every successful /api/ask_global response appends one line to
`/opt/wc-solns/<tenant>/recent_asks.jsonl`. The sidebar footer reads the
last 3 entries and renders them as clickable pills that re-open the
palette with that question.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from . import heartbeat_store

_MAX_KEEP = 30


def _path(tenant_id: str):
    return heartbeat_store.tenant_root(tenant_id) / "recent_asks.jsonl"


def append(tenant_id: str, question: str, cost_usd: float = 0.0) -> None:
    """Append one ask. Caps storage at _MAX_KEEP entries (rewrites file on trim)."""
    question = (question or "").strip()
    if not question:
        return
    try:
        path = _path(tenant_id)
    except heartbeat_store.HeartbeatError:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "question": question[:500],
        "cost_usd": round(cost_usd or 0.0, 6),
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
    _trim_if_needed(path)


def _trim_if_needed(path) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    if len(lines) <= _MAX_KEEP:
        return
    path.write_text("\n".join(lines[-_MAX_KEEP:]) + "\n", encoding="utf-8")


def recent(tenant_id: str, n: int = 3) -> list[dict[str, Any]]:
    try:
        path = _path(tenant_id)
    except heartbeat_store.HeartbeatError:
        return []
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    rows.sort(key=lambda r: r.get("ts", ""), reverse=True)
    return rows[:n]
