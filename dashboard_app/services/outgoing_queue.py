"""
Outgoing message queue with human approval.

Data layout:
    /opt/wc-solns/<tenant>/outgoing/pending.jsonl   (queue, rewritten on mutate)
    /opt/wc-solns/<tenant>/outgoing/archived.jsonl  (history, append-only)

Flow:
    enqueue(...) -> draft joins pending.jsonl. Guardrails run on the raw body.
                    If guardrails reject, the draft is NOT queued; the caller
                    learns the reason and can abort.
    approve(id)  -> draft moves from pending to archived with status=approved,
                    a receipt is written, and the caller gets the final body
                    to actually send.
    edit(id, new_body) -> guardrails re-run on the new body, then approve.
    skip(id, reason)   -> draft moves to archived with status=skipped.

Concurrency: the queue files are single-writer in practice (one container,
one process). We serialize mutations with a threading.Lock. If we later
scale to multiple workers, swap to a real queue (SQS / Redis streams).
"""

from __future__ import annotations

import json
import re
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import guardrails, heartbeat_store, receipts

_SAFE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_LOCK = threading.Lock()


class OutgoingError(RuntimeError):
    pass


def _outgoing_root(tenant_id: str) -> Path:
    return heartbeat_store.tenant_root(tenant_id) / "outgoing"


def _pending_path(tenant_id: str) -> Path:
    return _outgoing_root(tenant_id) / "pending.jsonl"


def _archive_path(tenant_id: str) -> Path:
    return _outgoing_root(tenant_id) / "archived.jsonl"


def _new_id(pipeline_id: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    suffix = secrets.token_hex(3)
    return f"draft-{pipeline_id}-{stamp}-{suffix}"


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


def _write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        "".join(json.dumps(r) + "\n" for r in rows),
        encoding="utf-8",
    )
    tmp.replace(path)


def _append_archive(tenant_id: str, entry: dict[str, Any]) -> None:
    path = _archive_path(tenant_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def enqueue(
    tenant_id: str,
    pipeline_id: str,
    channel: str,
    recipient_hint: str,
    subject: str,
    body: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Queue a draft for approval. Returns the stored entry (or raises if
    the guardrail blocks an outright rejection - vendor leaks etc.)."""
    if not _SAFE.match(pipeline_id or ""):
        raise OutgoingError("invalid pipeline_id")

    review = guardrails.review_outbound("draft_queued", body or "")
    if review.decision == "reject":
        raise OutgoingError("guardrail rejected draft: " + "; ".join(review.reasons))

    entry = {
        "id": _new_id(pipeline_id),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "pipeline_id": pipeline_id,
        "channel": (channel or "message")[:32],
        "recipient_hint": (recipient_hint or "")[:240],
        "subject": (subject or "")[:240],
        "body": review.content,
        "status": "pending",
        "guardrail_reasons": review.reasons,
    }
    if metadata:
        entry["metadata"] = metadata

    with _LOCK:
        rows = _read_jsonl(_pending_path(tenant_id))
        rows.append(entry)
        _write_jsonl_atomic(_pending_path(tenant_id), rows)
    return entry


def list_pending(tenant_id: str) -> list[dict[str, Any]]:
    """Oldest-first (FIFO view, urgency = age)."""
    try:
        rows = _read_jsonl(_pending_path(tenant_id))
    except heartbeat_store.HeartbeatError:
        return []
    rows.sort(key=lambda r: r.get("created_at", ""))
    return rows


def _pull_pending(tenant_id: str, draft_id: str) -> dict[str, Any] | None:
    """Remove a draft from pending.jsonl and return it. Caller handles archival."""
    with _LOCK:
        rows = _read_jsonl(_pending_path(tenant_id))
        idx = next((i for i, r in enumerate(rows) if r.get("id") == draft_id), -1)
        if idx < 0:
            return None
        entry = rows.pop(idx)
        _write_jsonl_atomic(_pending_path(tenant_id), rows)
        return entry


def _finalize(tenant_id: str, entry: dict[str, Any], status: str, reason: str | None = None) -> dict[str, Any]:
    entry["status"] = status
    entry["finalized_at"] = datetime.now(timezone.utc).isoformat()
    if reason:
        entry["skip_reason"] = reason[:240]
    _append_archive(tenant_id, entry)
    return entry


def approve(tenant_id: str, draft_id: str, edited_body: str | None = None) -> dict[str, Any]:
    """Approve (and optionally edit) a pending draft. Writes a receipt; caller
    is responsible for the actual network send. Returns the final entry so
    the caller can invoke the pipeline's send(...) with the approved body."""
    entry = _pull_pending(tenant_id, draft_id)
    if entry is None:
        raise OutgoingError("draft not found")

    final_body = entry["body"] if edited_body is None else edited_body
    review = guardrails.review_outbound("draft_sending", final_body)
    if review.decision == "reject":
        # Put the draft back so the owner can re-edit; don't lose it.
        with _LOCK:
            rows = _read_jsonl(_pending_path(tenant_id))
            entry["guardrail_reasons"] = review.reasons
            rows.insert(0, entry)
            _write_jsonl_atomic(_pending_path(tenant_id), rows)
        raise OutgoingError("guardrail rejected on approve: " + "; ".join(review.reasons))

    entry["body"] = review.content  # em-dash strip preserved
    status = "edited" if edited_body is not None else "approved"
    entry = _finalize(tenant_id, entry, status)

    # Persist to receipts (the drawer becomes the audit trail).
    receipts.append(
        tenant_id=tenant_id,
        pipeline_id=entry["pipeline_id"],
        channel=entry["channel"],
        recipient_hint=entry["recipient_hint"],
        subject=entry["subject"],
        body=entry["body"],
        guardrail_result=status,
        meta={"from": "approval_queue", "draft_id": entry["id"]},
    )
    return entry


def skip(tenant_id: str, draft_id: str, reason: str = "") -> dict[str, Any]:
    entry = _pull_pending(tenant_id, draft_id)
    if entry is None:
        raise OutgoingError("draft not found")
    _finalize(tenant_id, entry, "skipped", reason=reason)
    return entry


def summary(tenant_id: str) -> dict[str, int]:
    """Snapshot counts for the bell badge / admin view."""
    rows = list_pending(tenant_id)
    now = datetime.now(timezone.utc)
    green = amber = red = 0
    for r in rows:
        try:
            created = datetime.fromisoformat((r.get("created_at") or "").replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        hours = (now - created).total_seconds() / 3600
        if hours < 2:
            green += 1
        elif hours < 12:
            amber += 1
        else:
            red += 1
    return {"pending": len(rows), "green": green, "amber": amber, "red": red}
