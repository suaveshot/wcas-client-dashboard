"""
Receipts store - the actual text of every auto-sent message.

Layout:
    /opt/wc-solns/<tenant>/receipts/<pipeline_id>/<yyyy-mm-dd>.jsonl

One row per outbound send, append-only. Each row shape:

    {
        "id": "<pipeline>-<iso_ts>-<rand>",
        "ts": "2026-04-22T14:30:00+00:00",
        "pipeline_id": "sales_pipeline",
        "channel": "email|sms|post|message",
        "recipient_hint": "jane@example.com" or "+15551234567",
        "subject": "...",
        "body": "...full outbound content...",
        "bytes": 874,
        "cost_usd": 0.0002,
        "guardrail_result": "approve|revise",
        "meta": {...pipeline-specific...}
    }

Design notes:
  * Body is stored raw, not scrubbed. The recipient already received this exact
    text; redacting it here would make the receipts drawer useless as a trust
    tool. Privacy mode masks display via `.ap-priv` spans in the drawer UI.
  * pipeline_id and date must match [a-z0-9_-]+ / YYYY-MM-DD to prevent path
    traversal.
  * We never delete receipts on write. A retention cron can prune old daily
    files later; for hackathon scope, keep everything.
"""

from __future__ import annotations

import json
import re
import secrets
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from . import heartbeat_store

_SAFE_PIPELINE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_SAFE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _receipts_root(tenant_id: str) -> Path:
    return heartbeat_store.tenant_root(tenant_id) / "receipts"


def _pipeline_dir(tenant_id: str, pipeline_id: str) -> Path:
    if not _SAFE_PIPELINE.match(pipeline_id or ""):
        raise ValueError("invalid pipeline_id")
    return _receipts_root(tenant_id) / pipeline_id


def _today_file(tenant_id: str, pipeline_id: str) -> Path:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return _pipeline_dir(tenant_id, pipeline_id) / f"{today}.jsonl"


def _new_id(pipeline_id: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    suffix = secrets.token_hex(3)
    return f"{pipeline_id}-{stamp}-{suffix}"


def append(
    tenant_id: str,
    pipeline_id: str,
    channel: str,
    recipient_hint: str,
    subject: str,
    body: str,
    cost_usd: float = 0.0,
    guardrail_result: str = "approve",
    meta: dict[str, Any] | None = None,
    ts: str | None = None,
) -> str:
    """Append one receipt row. Returns the generated id."""
    if not pipeline_id:
        raise ValueError("pipeline_id required")
    body = body or ""
    subject = (subject or "")[:240]
    recipient_hint = (recipient_hint or "")[:240]
    channel = (channel or "message")[:32]

    entry = {
        "id": _new_id(pipeline_id),
        "ts": ts or datetime.now(timezone.utc).isoformat(),
        "pipeline_id": pipeline_id,
        "channel": channel,
        "recipient_hint": recipient_hint,
        "subject": subject,
        "body": body,
        "bytes": len(body.encode("utf-8")),
        "cost_usd": round(cost_usd or 0.0, 6),
        "guardrail_result": guardrail_result,
    }
    if meta:
        entry["meta"] = meta

    target = _today_file(tenant_id, pipeline_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
    return entry["id"]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return rows


def list_for_pipeline(tenant_id: str, pipeline_id: str, limit: int = 25) -> list[dict[str, Any]]:
    """Most-recent-first receipts for one pipeline, across all daily files."""
    try:
        p_dir = _pipeline_dir(tenant_id, pipeline_id)
    except (ValueError, heartbeat_store.HeartbeatError):
        return []
    if not p_dir.exists():
        return []
    files = sorted(p_dir.glob("*.jsonl"), reverse=True)
    rows: list[dict[str, Any]] = []
    for f in files:
        if not _SAFE_DATE.match(f.stem):
            continue
        rows.extend(_read_jsonl(f))
        if len(rows) >= limit * 2:
            break
    rows.sort(key=lambda r: r.get("ts", ""), reverse=True)
    return rows[:limit]


def list_all(tenant_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """Most-recent-first receipts across every pipeline."""
    try:
        root = _receipts_root(tenant_id)
    except heartbeat_store.HeartbeatError:
        return []
    if not root.exists():
        return []
    rows: list[dict[str, Any]] = []
    for p_dir in root.iterdir():
        if not p_dir.is_dir():
            continue
        if not _SAFE_PIPELINE.match(p_dir.name):
            continue
        for f in sorted(p_dir.glob("*.jsonl"), reverse=True)[:3]:  # last 3 days/pipeline
            rows.extend(_read_jsonl(f))
    rows.sort(key=lambda r: r.get("ts", ""), reverse=True)
    return rows[:limit]
