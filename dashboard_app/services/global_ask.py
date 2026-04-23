"""
Global "ask your business" context composer.

One Opus 4.7 call against the entire tenant workspace, no RAG, no chunking.
Everything the tenant has generated since activation fits comfortably inside
the 1M-context window; we compose it into a single structured prompt.

Context sections (in order):
    1. All heartbeat snapshots (one block per pipeline, capped summary + log)
    2. Recent decisions from decisions.jsonl (last 50)
    3. Goals (if goals.json exists)
    4. Brand (if brand.json exists)
    5. KB markdown (if /kb/*.md exists)
    6. Receipts summary (aggregated counts per pipeline; not full bodies)

Each section has a budget; we never load more than the budget even if the
source has more. This keeps prompt size predictable and cache-friendly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import activity_feed, heartbeat_store

_HEARTBEAT_PAYLOAD_CAP = 2000  # chars per pipeline
_LOG_TAIL_CAP = 1500  # chars per pipeline's log
_DECISIONS_CAP = 50
_KB_FILE_CAP = 4000  # chars per KB file
_KB_FILES_CAP = 8  # max KB files to include


_SYSTEM_PROMPT = """You are Larry, a senior automation analyst embedded in a small-shop owner-operator's dashboard. You answer any question the owner asks about their business using only the evidence in context.

Rules:
- Two to four sentences. Plain English. No jargon. No em dashes. Never mention the name of any AI vendor.
- Always cite specific pipelines, timestamps, or numbers from the evidence.
- If the evidence does not answer the question, say so clearly and suggest one concrete next step the owner could take.
- Never invent numbers. Never make promises about future performance.
- You can reference decisions the owner made (from the decision log), goals they set, and what their automations did.
- Think of yourself as an agency account manager who read everything before the meeting, not a chatbot.
"""


def _read_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _compose_heartbeat_block(snap: dict[str, Any]) -> str:
    pid = snap.get("pipeline_id", "unknown")
    payload = snap.get("payload") or {}
    received_at = snap.get("received_at", "")
    status = payload.get("status", "unknown")
    last_run = payload.get("last_run", "")
    summary = (payload.get("summary") or "")[:600]
    state_summary = payload.get("state_summary") or {}
    log_tail = (payload.get("log_tail") or "")[:_LOG_TAIL_CAP]

    state_lines = []
    if isinstance(state_summary, dict):
        for k, v in list(state_summary.items())[:10]:
            if isinstance(v, (str, int, float, bool)):
                state_lines.append(f"  {k}: {v}")

    block = [
        f"### Pipeline: {pid}",
        f"Status: {status}",
        f"Last run: {last_run}",
        f"Received at: {received_at}",
    ]
    if summary:
        block.append(f"Summary: {summary}")
    if state_lines:
        block.append("State:")
        block.extend(state_lines)
    if log_tail:
        block.append("Recent log:")
        block.append(log_tail)
    text = "\n".join(block)
    if len(text) > _HEARTBEAT_PAYLOAD_CAP + _LOG_TAIL_CAP + 600:
        text = text[: _HEARTBEAT_PAYLOAD_CAP + _LOG_TAIL_CAP + 600]
    return text


def _compose_decisions_block(tenant_id: str) -> str:
    try:
        rows = activity_feed._decision_rows(tenant_id, max_rows=_DECISIONS_CAP)
    except heartbeat_store.HeartbeatError:
        return ""
    if not rows:
        return "(no owner decisions recorded yet)"
    lines = []
    for r in rows:
        ts = r.get("relative") or r.get("time") or ""
        txt = r.get("action", "")
        if txt:
            lines.append(f"- [{ts}] {txt}")
    return "\n".join(lines)


def _compose_goals_block(tenant_id: str) -> str:
    try:
        root = heartbeat_store.tenant_root(tenant_id)
    except heartbeat_store.HeartbeatError:
        return "(no goals pinned)"
    goals = _read_json_if_exists(root / "goals.json")
    if not goals or not goals.get("goals"):
        return "(no goals pinned; owner hasn't set any yet)"
    lines = []
    for g in goals.get("goals", [])[:3]:
        title = g.get("title", "untitled")
        metric = g.get("metric", "")
        target = g.get("target", "")
        timeframe = g.get("timeframe", "")
        lines.append(f"- {title} ({metric} target {target}, {timeframe})")
    return "\n".join(lines)


def _compose_brand_block(tenant_id: str) -> str:
    try:
        root = heartbeat_store.tenant_root(tenant_id)
    except heartbeat_store.HeartbeatError:
        return ""
    brand = _read_json_if_exists(root / "brand.json")
    if not brand:
        return ""
    name = brand.get("company_name") or brand.get("name") or ""
    tone = brand.get("tone") or ""
    lines = []
    if name:
        lines.append(f"Company: {name}")
    if tone:
        lines.append(f"Voice/tone: {tone}")
    return "\n".join(lines)


def _compose_kb_block(tenant_id: str) -> str:
    try:
        root = heartbeat_store.tenant_root(tenant_id)
    except heartbeat_store.HeartbeatError:
        return ""
    kb_dir = root / "kb"
    if not kb_dir.exists():
        return ""
    files = sorted(kb_dir.glob("*.md"))[:_KB_FILES_CAP]
    parts: list[str] = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")[:_KB_FILE_CAP]
        except OSError:
            continue
        parts.append(f"#### {f.name}\n{text}")
    return "\n\n".join(parts)


def _compose_receipts_summary(tenant_id: str) -> str:
    """Aggregate receipt counts per pipeline without loading full bodies."""
    try:
        root = heartbeat_store.tenant_root(tenant_id)
    except heartbeat_store.HeartbeatError:
        return ""
    receipts_dir = root / "receipts"
    if not receipts_dir.exists():
        return ""
    lines = []
    for pipeline_dir in sorted(receipts_dir.iterdir()):
        if not pipeline_dir.is_dir():
            continue
        total = 0
        for daily in pipeline_dir.glob("*.jsonl"):
            try:
                total += sum(1 for _ in daily.open("r", encoding="utf-8"))
            except OSError:
                continue
        if total:
            lines.append(f"- {pipeline_dir.name}: {total} outbound sends on record")
    return "\n".join(lines)


def compose_context(tenant_id: str) -> dict[str, Any]:
    """Compose the structured evidence context for a global ask.

    Returns a dict with `prompt` (the user message content) and `sources`
    (structured source-chip metadata for the UI).
    """
    snaps = []
    try:
        snaps = heartbeat_store.read_all(tenant_id)
    except heartbeat_store.HeartbeatError:
        snaps = []

    sections: list[str] = []
    sources: list[dict[str, Any]] = []

    if snaps:
        sections.append("## Pipeline telemetry\n")
        for snap in snaps:
            sections.append(_compose_heartbeat_block(snap))
            sources.append({
                "source": "pipeline",
                "label": snap.get("pipeline_id", "unknown"),
                "timestamp": snap.get("received_at", ""),
            })
    else:
        sections.append("## Pipeline telemetry\n(no heartbeats received yet)")

    sections.append("\n## Recent owner decisions\n" + _compose_decisions_block(tenant_id))

    goals_text = _compose_goals_block(tenant_id)
    sections.append("\n## Pinned goals\n" + goals_text)
    if "(no goals pinned)" not in goals_text:
        sources.append({"source": "goals", "label": "goals.json", "timestamp": ""})

    brand_text = _compose_brand_block(tenant_id)
    if brand_text:
        sections.append("\n## Brand voice\n" + brand_text)

    kb_text = _compose_kb_block(tenant_id)
    if kb_text:
        sections.append("\n## Knowledge base\n" + kb_text)
        sources.append({"source": "kb", "label": "knowledge base", "timestamp": ""})

    receipts_text = _compose_receipts_summary(tenant_id)
    if receipts_text:
        sections.append("\n## Receipts summary\n" + receipts_text)

    prompt = "\n".join(sections)
    return {"prompt": prompt, "sources": sources}


def system_prompt() -> str:
    return _SYSTEM_PROMPT
