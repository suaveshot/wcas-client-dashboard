"""Shared dispatcher - the registry that closes audits/phase0_*::F1 across
/approvals, /recommendations, /goals, and /settings.

Pre-W3 each surface had its own enqueue path but no execute path. Owners
clicked Approve / Apply / Pause and saw a green toast that did nothing.
This module fixes that by introducing a single registry pattern (lifted
from `services.activation_tools.HANDLERS`) with four entry points:

  send(tenant_id, pipeline_id, ...)
        Pipeline-side. Honors:
          - tenant_config.json:status == "paused"  -> action="skipped"
          - tenant prefs.require_approval[pipeline_id] is True
                -> action="queued" via outgoing_queue.enqueue(...)
          - else hands off to the registered OUTGOING_HANDLERS[pipeline_id]
            for direct delivery -> action="delivered"

  deliver_approved(tenant_id, archive_entry)
        Post-/approvals-click. The owner already approved; honors pause
        only. Runs OUTGOING_HANDLERS[entry.pipeline_id]. On DispatchError
        the archived.jsonl entry is flipped to status=approved_send_failed
        via outgoing_queue.mark_send_failed(...).

  execute_rec(tenant_id, rec_id)
        /recommendations Apply. Looks up the rec in today's recs file,
        finds REC_HANDLERS[rec.proposed_tool], runs it. Unknown tool
        types return {queued_for_review: true} per the audit's honest-
        stub recommendation.

  handle_heartbeat_events(tenant_id, events)
        Goals F1. Pipelines may include an `events` array on the heartbeat
        payload of shape [{"kind": "lead.created", "count": 1}, ...].
        Maps event kinds to goal metrics and calls goals.bump_current.
        Backward-compatible: heartbeats without `events` are no-ops.

W3 ships the framework + one reference handler in each registry
(reviews/gbp_review_reply outgoing + review_reply_draft rec). The other
five outgoing pipelines and four rec types are honest no-ops; they fill
in alongside the per-pipeline tenant-ization work in W4-W7.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Callable

from . import (
    audit_log,
    goals,
    heartbeat_store,
    outgoing_queue,
    recs_store,
    tenant_prefs,
)

log = logging.getLogger("dashboard.dispatch")


class DispatchError(RuntimeError):
    """Raised by an outgoing or rec handler to signal a non-recoverable
    send/apply failure. The dispatcher catches this and (for outgoing)
    flips the archived entry to status=approved_send_failed."""


# ---------------------------------------------------------------------------
# gates
# ---------------------------------------------------------------------------


def _tenant_config(tenant_id: str) -> dict[str, Any]:
    try:
        path = heartbeat_store.tenant_root(tenant_id) / "tenant_config.json"
    except heartbeat_store.HeartbeatError:
        return {}
    if not path.exists():
        return {}
    try:
        import json

        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def is_paused(tenant_id: str) -> bool:
    """True when the owner has hit Pause Every Role (settings F1 kill switch)."""
    return _tenant_config(tenant_id).get("status") == "paused"


def requires_approval(tenant_id: str, pipeline_id: str) -> bool:
    """True when the per-pipeline Approve-Before-Send toggle is on
    (settings F3). Reads prefs.require_approval[pipeline_id]."""
    prefs = tenant_prefs.read(tenant_id)
    require_map = prefs.get("require_approval") or {}
    return bool(require_map.get(pipeline_id, False))


# ---------------------------------------------------------------------------
# reference handlers
# ---------------------------------------------------------------------------


def _send_review_reply(tenant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Outgoing handler for the reviews pipeline.

    Posts a reply to a Google Business Profile review. For W3 the real
    HTTP call is gated behind DISPATCH_DRY_RUN: in dry-run mode (local
    tests, dev sessions) we log the would-send and return success, so the
    full Approve -> dispatch.deliver_approved -> handler -> outcome wire
    is exercised end-to-end without hitting the real GBP API.

    The non-dry-run branch is intentionally minimal and ships unverified;
    the live GBP wire format gets locked + tested in W4 alongside the
    generic gbp/run.py and reviews/run.py pipelines. Per Sam's
    "test before first send" rule, real review-reply sends require a
    review pass before DISPATCH_DRY_RUN is unset on the VPS.
    """
    body = (payload.get("body") or "").strip()
    metadata = payload.get("metadata") or {}
    review_meta = metadata.get("review") if isinstance(metadata.get("review"), dict) else {}
    review_name = review_meta.get("name") or metadata.get("review_name")

    if os.getenv("DISPATCH_DRY_RUN", "false").strip().lower() in ("true", "1", "yes"):
        log.info(
            "DRY_RUN reviews.send tenant=%s body_len=%s review=%s",
            tenant_id,
            len(body),
            review_name or "(no review_name)",
        )
        return {
            "posted": True,
            "dry_run": True,
            "review_name": review_name,
            "body_len": len(body),
        }

    # Non-dry-run path is a placeholder; W4 wires the actual GBP call.
    raise DispatchError(
        "live GBP review-reply send not wired yet (W4); "
        "set DISPATCH_DRY_RUN=true for local testing"
    )


def _rec_review_reply_draft(tenant_id: str, rec: dict[str, Any]) -> dict[str, Any]:
    """Apply handler for recs of proposed_tool=review_reply_draft.

    Materializes the rec into a draft in the outgoing queue so it shows
    up under /approvals. The owner reviews the draft; clicking Approve
    on /approvals invokes _send_review_reply via deliver_approved.
    """
    review = rec.get("review") if isinstance(rec.get("review"), dict) else {}
    reviewer = (review.get("reviewer") or "").strip()
    body = (rec.get("draft_body") or "").strip() or "Thank you for your feedback."
    subject = f"Reply to {reviewer}'s review" if reviewer else "Review reply"

    entry = outgoing_queue.enqueue(
        tenant_id=tenant_id,
        pipeline_id="reviews",
        channel="gbp_review_reply",
        recipient_hint=reviewer or "(no reviewer)",
        subject=subject,
        body=body,
        metadata={
            "rec_id": rec.get("id"),
            "review": review,
            "source": "recommendation_apply",
        },
    )
    return {"draft_id": entry["id"], "queued_to": "outgoing", "pipeline_id": "reviews"}


# ---------------------------------------------------------------------------
# registries
# ---------------------------------------------------------------------------

# Maps pipeline_id -> outgoing handler. Reference handler ships for
# `reviews`; the other six onboarding roles are added in W4-W7 alongside
# their generic run.py pipelines.
OUTGOING_HANDLERS: dict[str, Callable[[str, dict[str, Any]], dict[str, Any]]] = {
    "reviews": _send_review_reply,
}

# Maps rec.proposed_tool -> rec handler. Reference handler ships for
# `review_reply_draft`. Unknown types return queued_for_review per the
# audit's honest-stub recommendation; Sam can hand-execute them via /admin.
REC_HANDLERS: dict[str, Callable[[str, dict[str, Any]], dict[str, Any]]] = {
    "review_reply_draft": _rec_review_reply_draft,
}


# ---------------------------------------------------------------------------
# entry points
# ---------------------------------------------------------------------------


def send(
    tenant_id: str,
    pipeline_id: str,
    *,
    channel: str,
    recipient_hint: str,
    subject: str,
    body: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pipeline-side dispatch entry point. Resolves pause + require_approval
    gates and either skips, queues for approval, or delivers directly.

    Returns one of:
      {action: "skipped",       reason: "tenant_paused", ...}
      {action: "queued",        draft_id: ...}
      {action: "delivered",     handler: pipeline_id, result: {...}}
      {action: "no_dispatcher", pipeline_id: ...}
      {action: "failed",        reason: "...", pipeline_id: ...}
    """
    if is_paused(tenant_id):
        audit_log.record(
            tenant_id=tenant_id,
            event="dispatch_skipped_paused",
            ok=True,
            pipeline_id=pipeline_id,
        )
        return {"action": "skipped", "reason": "tenant_paused", "pipeline_id": pipeline_id}

    if requires_approval(tenant_id, pipeline_id):
        try:
            entry = outgoing_queue.enqueue(
                tenant_id=tenant_id,
                pipeline_id=pipeline_id,
                channel=channel,
                recipient_hint=recipient_hint,
                subject=subject,
                body=body,
                metadata=metadata or {},
            )
        except outgoing_queue.OutgoingError as exc:
            audit_log.record(
                tenant_id=tenant_id,
                event="dispatch_queue_failed",
                ok=False,
                pipeline_id=pipeline_id,
                error=str(exc),
            )
            return {"action": "failed", "reason": str(exc), "pipeline_id": pipeline_id}
        audit_log.record(
            tenant_id=tenant_id,
            event="dispatch_queued",
            ok=True,
            pipeline_id=pipeline_id,
            draft_id=entry["id"],
        )
        return {"action": "queued", "draft_id": entry["id"], "pipeline_id": pipeline_id}

    handler = OUTGOING_HANDLERS.get(pipeline_id)
    if handler is None:
        audit_log.record(
            tenant_id=tenant_id,
            event="dispatch_no_handler",
            ok=False,
            pipeline_id=pipeline_id,
        )
        return {"action": "no_dispatcher", "pipeline_id": pipeline_id}

    payload = {
        "channel": channel,
        "recipient_hint": recipient_hint,
        "subject": subject,
        "body": body,
        "metadata": metadata or {},
    }
    try:
        result = handler(tenant_id, payload)
    except DispatchError as exc:
        audit_log.record(
            tenant_id=tenant_id,
            event="dispatch_failed",
            ok=False,
            pipeline_id=pipeline_id,
            error=str(exc),
        )
        return {"action": "failed", "reason": str(exc), "pipeline_id": pipeline_id}
    except Exception as exc:  # noqa: BLE001 - handler bug must not crash the pipeline
        log.exception("dispatcher handler raised tenant=%s pipeline=%s", tenant_id, pipeline_id)
        audit_log.record(
            tenant_id=tenant_id,
            event="dispatch_failed",
            ok=False,
            pipeline_id=pipeline_id,
            error=f"internal: {exc.__class__.__name__}",
        )
        return {
            "action": "failed",
            "reason": f"internal error: {exc.__class__.__name__}",
            "pipeline_id": pipeline_id,
        }

    audit_log.record(
        tenant_id=tenant_id,
        event="dispatch_delivered",
        ok=True,
        pipeline_id=pipeline_id,
    )
    return {"action": "delivered", "handler": pipeline_id, "result": result}


def deliver_approved(tenant_id: str, archive_entry: dict[str, Any]) -> dict[str, Any]:
    """Post-/approvals-click delivery. Honors pause but skips the
    require_approval gate (the owner already approved).

    On DispatchError the archived.jsonl entry's status is flipped to
    approved_send_failed so the audit trail records the failure and
    the future Send Failures UI (approvals F12) can surface it.

    Returns:
      {ok: True,  status: "delivered", result: {...}}
      {ok: False, reason: "tenant_paused" | "no_dispatcher" | "<error>", ...}
    """
    pipeline_id = archive_entry.get("pipeline_id") or ""
    draft_id = archive_entry.get("id") or ""

    if is_paused(tenant_id):
        audit_log.record(
            tenant_id=tenant_id,
            event="deliver_skipped_paused",
            ok=True,
            pipeline_id=pipeline_id,
            draft_id=draft_id,
        )
        return {
            "ok": False,
            "reason": "tenant_paused",
            "pipeline_id": pipeline_id,
            "draft_id": draft_id,
        }

    handler = OUTGOING_HANDLERS.get(pipeline_id)
    if handler is None:
        audit_log.record(
            tenant_id=tenant_id,
            event="deliver_no_handler",
            ok=False,
            pipeline_id=pipeline_id,
            draft_id=draft_id,
        )
        return {
            "ok": False,
            "reason": "no_dispatcher",
            "pipeline_id": pipeline_id,
            "draft_id": draft_id,
        }

    payload = {
        "channel": archive_entry.get("channel", ""),
        "recipient_hint": archive_entry.get("recipient_hint", ""),
        "subject": archive_entry.get("subject", ""),
        "body": archive_entry.get("body", ""),
        "metadata": archive_entry.get("metadata") or {},
    }
    try:
        result = handler(tenant_id, payload)
    except DispatchError as exc:
        outgoing_queue.mark_send_failed(tenant_id, draft_id, str(exc))
        audit_log.record(
            tenant_id=tenant_id,
            event="deliver_failed",
            ok=False,
            pipeline_id=pipeline_id,
            draft_id=draft_id,
            error=str(exc),
        )
        return {
            "ok": False,
            "reason": str(exc),
            "pipeline_id": pipeline_id,
            "draft_id": draft_id,
        }
    except Exception as exc:  # noqa: BLE001 - handler bug must not crash the API
        log.exception(
            "approved-deliver handler raised tenant=%s pipeline=%s draft=%s",
            tenant_id,
            pipeline_id,
            draft_id,
        )
        message = f"internal: {exc.__class__.__name__}"
        outgoing_queue.mark_send_failed(tenant_id, draft_id, message)
        audit_log.record(
            tenant_id=tenant_id,
            event="deliver_failed",
            ok=False,
            pipeline_id=pipeline_id,
            draft_id=draft_id,
            error=message,
        )
        return {
            "ok": False,
            "reason": message,
            "pipeline_id": pipeline_id,
            "draft_id": draft_id,
        }

    audit_log.record(
        tenant_id=tenant_id,
        event="deliver_delivered",
        ok=True,
        pipeline_id=pipeline_id,
        draft_id=draft_id,
    )
    return {
        "ok": True,
        "status": "delivered",
        "pipeline_id": pipeline_id,
        "draft_id": draft_id,
        "result": result,
    }


def _load_rec(tenant_id: str, rec_id: str) -> dict[str, Any] | None:
    payload = recs_store.read_latest(tenant_id)
    if not payload:
        return None
    for rec in payload.get("recs") or []:
        if isinstance(rec, dict) and rec.get("id") == rec_id:
            return rec
    return None


def execute_rec(tenant_id: str, rec_id: str) -> dict[str, Any]:
    """/recommendations Apply path. Looks up the rec, finds its handler
    by proposed_tool, runs it. Unknown tools fall back to a queued-for-
    review outcome so Sam can hand-execute them - honest stub per audits
    /phase0_recommendations.md::F1.

    Returns:
      {ok: True,  outcome: {...}}
      {ok: False, reason: "tenant_paused" | "rec_not_found" | "<error>"}
    """
    if is_paused(tenant_id):
        return {"ok": False, "reason": "tenant_paused"}

    rec = _load_rec(tenant_id, rec_id)
    if rec is None:
        return {"ok": False, "reason": "rec_not_found"}

    proposed_tool = (rec.get("proposed_tool") or "").strip()
    handler = REC_HANDLERS.get(proposed_tool)
    if handler is None:
        audit_log.record(
            tenant_id=tenant_id,
            event="rec_apply_queued_for_review",
            ok=True,
            rec_id=rec_id,
            proposed_tool=proposed_tool or "(unset)",
        )
        return {
            "ok": True,
            "outcome": {
                "queued_for_review": True,
                "reason": f"no executor for proposed_tool={proposed_tool!r}",
            },
        }

    try:
        outcome = handler(tenant_id, rec)
    except DispatchError as exc:
        audit_log.record(
            tenant_id=tenant_id,
            event="rec_apply_failed",
            ok=False,
            rec_id=rec_id,
            proposed_tool=proposed_tool,
            error=str(exc),
        )
        return {"ok": False, "reason": str(exc)}
    except Exception as exc:  # noqa: BLE001 - keep the API alive
        log.exception("rec handler raised tenant=%s rec=%s", tenant_id, rec_id)
        message = f"internal: {exc.__class__.__name__}"
        audit_log.record(
            tenant_id=tenant_id,
            event="rec_apply_failed",
            ok=False,
            rec_id=rec_id,
            proposed_tool=proposed_tool,
            error=message,
        )
        return {"ok": False, "reason": message}

    audit_log.record(
        tenant_id=tenant_id,
        event="rec_apply_delivered",
        ok=True,
        rec_id=rec_id,
        proposed_tool=proposed_tool,
    )
    return {"ok": True, "outcome": outcome}


# ---------------------------------------------------------------------------
# heartbeat events -> goals
# ---------------------------------------------------------------------------


# Maps event kind -> (goal metric, predicate(event) -> bool)
_EVENT_TO_METRIC: dict[str, tuple[str, Callable[[dict[str, Any]], bool]]] = {
    "lead.created": ("leads", lambda _e: True),
    "review.posted": ("reviews", lambda e: int(e.get("stars") or 0) >= 5),
}


def handle_heartbeat_events(tenant_id: str, events: list[dict[str, Any]] | None) -> None:
    """Drain a heartbeat's `events` array and bump matching goals.

    Tolerant of malformed input. Pipelines without events emit a missing
    or empty array; that's a no-op. If goals are pinned but no event
    matches their metric, also a no-op.

    Never raises - heartbeat ingest must keep working even if a single
    event is malformed.
    """
    if not events:
        return

    try:
        data = goals.read(tenant_id)
    except Exception:  # noqa: BLE001 - never break heartbeat ingest
        log.exception("goals.read failed during heartbeat ingest tenant=%s", tenant_id)
        return

    pinned = data.get("goals") or []
    if not pinned:
        return

    # Map metric -> first matching goal id (matches MAX_GOALS=3 cap; if a
    # tenant ever has multiple goals on the same metric, oldest wins -
    # bump_current itself is harmless for a single goal so this is fine).
    by_metric: dict[str, str] = {}
    for g in pinned:
        metric = g.get("metric")
        gid = g.get("id")
        if isinstance(metric, str) and isinstance(gid, str) and metric not in by_metric:
            by_metric[metric] = gid

    for ev in events:
        if not isinstance(ev, dict):
            continue
        kind = (ev.get("kind") or "").strip()
        spec = _EVENT_TO_METRIC.get(kind)
        if spec is None:
            continue
        metric, predicate = spec
        try:
            if not predicate(ev):
                continue
        except Exception:  # noqa: BLE001 - bad event field shouldn't break ingest
            continue
        gid = by_metric.get(metric)
        if not gid:
            continue
        try:
            count_raw = ev.get("count", 1)
            count = float(count_raw)
        except (TypeError, ValueError):
            count = 1.0
        try:
            goals.bump_current(tenant_id, gid, count)
        except Exception:  # noqa: BLE001
            log.exception(
                "goals.bump_current failed tenant=%s goal=%s kind=%s",
                tenant_id,
                gid,
                kind,
            )
            continue
        audit_log.record(
            tenant_id=tenant_id,
            event="goal_bumped",
            ok=True,
            metric=metric,
            kind=kind,
            count=count,
            goal_id=gid,
        )


__all__ = [
    "DispatchError",
    "OUTGOING_HANDLERS",
    "REC_HANDLERS",
    "deliver_approved",
    "execute_rec",
    "handle_heartbeat_events",
    "is_paused",
    "requires_approval",
    "send",
]


# Suppress linter warning about the unused datetime/timezone imports below
# when DispatchError is the only export reaching for them. The audit_log
# already imports datetime in callers; we keep the import here for future
# expansion (deliver_approved's failure timestamp is recorded in the queue).
_ = datetime, timezone
