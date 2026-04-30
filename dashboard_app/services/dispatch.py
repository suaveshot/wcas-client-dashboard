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
import smtplib
from datetime import datetime, timezone
from email.mime.text import MIMEText
from typing import Any, Callable

import httpx

from . import (
    audit_log,
    credentials,
    crm_mapping,
    ghl_provider,
    goals,
    heartbeat_store,
    hubspot_provider,
    outgoing_queue,
    pipedrive_provider,
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


_GBP_BASE_URL = "https://mybusiness.googleapis.com/v4"


def _dry_run() -> bool:
    return os.getenv("DISPATCH_DRY_RUN", "false").strip().lower() in ("true", "1", "yes")


def _post_gbp(url: str, *, token: str, json_body: dict[str, Any]) -> Any:
    """Module-level POST helper so tests can monkeypatch the network call
    without touching httpx globally. Returns an httpx.Response-shaped object."""
    return httpx.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=json_body,
        timeout=30.0,
    )


def _build_review_name(metadata: dict[str, Any]) -> str:
    """Reconstruct the GBP review resource name from pipeline metadata.

    The reviews pipeline stores account_path + location_path + review_id; the
    API expects accounts/{a}/locations/{l}/reviews/{r}. If the queue entry
    already carries an explicit review_name (e.g. from a hand-built draft),
    that wins.
    """
    location_path = (metadata.get("location_path") or "").strip().strip("/")
    review_id = (metadata.get("review_id") or "").strip()
    if location_path and review_id:
        return f"{location_path}/reviews/{review_id}"
    return ""


def _send_review_reply(tenant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Outgoing handler for the reviews pipeline.

    Posts a reply to a Google Business Profile review via
    `POST /v4/{review_name}/reply`. DISPATCH_DRY_RUN gates the live call so
    the test-before-first-send gate (CLAUDE.md) can exercise the full
    Approve -> deliver_approved -> handler wire without touching Google.

    Token is fetched fresh on every send via credentials.access_token, which
    refreshes through the 50-min cache. Drafts can sit in the queue for hours
    or days between enqueue and approve, so caching the token at enqueue time
    would be a footgun.
    """
    body = (payload.get("body") or "").strip()
    metadata = payload.get("metadata") or {}
    review_meta = metadata.get("review") if isinstance(metadata.get("review"), dict) else {}
    review_name = (
        review_meta.get("name")
        or metadata.get("review_name")
        or _build_review_name(metadata)
    )

    if _dry_run():
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

    if not body:
        raise DispatchError("review reply body is empty")
    if not review_name:
        raise DispatchError(
            "review reply missing review name "
            "(need metadata.review.name, metadata.review_name, "
            "or location_path + review_id)"
        )

    try:
        token = credentials.access_token(tenant_id, "google")
    except (credentials.CredentialError, credentials.ProviderExchangeError) as exc:
        raise DispatchError(f"google access_token unavailable: {exc}") from exc

    url = f"{_GBP_BASE_URL}/{review_name}/reply"
    try:
        resp = _post_gbp(url, token=token, json_body={"comment": body})
    except httpx.HTTPError as exc:
        raise DispatchError(f"GBP request failed: {exc}") from exc

    status = getattr(resp, "status_code", 0)
    if status >= 400:
        text = (getattr(resp, "text", "") or "")[:300]
        raise DispatchError(f"GBP reply rejected: HTTP {status}: {text}")

    return {"posted": True, "review_name": review_name, "status_code": status}


def _send_gbp_post(tenant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Outgoing handler for the gbp pipeline.

    Posts a localPost (Google's "What's New" surface) to the tenant's GBP
    location via `POST /v4/{location_path}/localPosts`. DRY_RUN gated like
    reviews; same fresh-token-on-every-send pattern.
    """
    body = (payload.get("body") or "").strip()
    metadata = payload.get("metadata") or {}
    location_path = (metadata.get("location_path") or "").strip().strip("/")
    post_kind = (metadata.get("post_kind") or "STANDARD").strip().upper() or "STANDARD"
    language = (metadata.get("language_code") or "en-US").strip() or "en-US"

    if _dry_run():
        log.info(
            "DRY_RUN gbp.send tenant=%s body_len=%s location=%s",
            tenant_id,
            len(body),
            location_path or "(no location)",
        )
        return {
            "posted": True,
            "dry_run": True,
            "location_path": location_path,
            "body_len": len(body),
        }

    if not body:
        raise DispatchError("gbp post body is empty")
    if not location_path:
        raise DispatchError("gbp post missing metadata.location_path")

    try:
        token = credentials.access_token(tenant_id, "google")
    except (credentials.CredentialError, credentials.ProviderExchangeError) as exc:
        raise DispatchError(f"google access_token unavailable: {exc}") from exc

    url = f"{_GBP_BASE_URL}/{location_path}/localPosts"
    json_body: dict[str, Any] = {
        "languageCode": language,
        "summary": body,
        "topicType": post_kind,
    }
    try:
        resp = _post_gbp(url, token=token, json_body=json_body)
    except httpx.HTTPError as exc:
        raise DispatchError(f"GBP request failed: {exc}") from exc

    status = getattr(resp, "status_code", 0)
    if status >= 400:
        text = (getattr(resp, "text", "") or "")[:300]
        raise DispatchError(f"GBP localPost rejected: HTTP {status}: {text}")

    return {"posted": True, "location_path": location_path, "status_code": status}


_SALES_SUPPORTED_KINDS = ("ghl", "hubspot", "pipedrive")


def _resolve_sales_kind(tenant_id: str) -> str:
    """Return the CRM kind for a tenant, mirroring the sales pipeline's
    `_provider_kind` so /approvals routes through the same provider the
    pipeline drafted with. Empty string when no mapping exists or no kind
    field is set."""
    mapping = crm_mapping.load(tenant_id) or {}
    for key in ("kind", "crm", "provider", "provider_kind"):
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return ""


def _send_sales(tenant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Outgoing handler for the sales pipeline.

    Resolves the tenant's CRM (GHL / HubSpot / Pipedrive) via
    crm_mapping.json, builds the matching provider, and routes to
    send_email or send_sms based on the queue entry's channel. Each
    provider's typed error class is converted to DispatchError so the
    archived entry flips to approved_send_failed cleanly.

    Per the W6 partial-method-surface lesson
    (lessons/mistake_provider_abstraction_incomplete_method_surface.md),
    every supported provider's vendor-error class is caught explicitly.
    HubSpotProvider.send_sms and PipedriveProvider.send_email/send_sms
    raise by design when the vendor lacks the operation; those raise into
    DispatchError instead of silently no-oping.
    """
    metadata = payload.get("metadata") or {}
    contact_id = str(metadata.get("contact_id") or "").strip()
    channel = (payload.get("channel") or "").strip().lower()
    body = payload.get("body") or ""
    subject = payload.get("subject") or ""

    is_email = "email" in channel
    is_sms = "sms" in channel or "text" in channel

    if _dry_run():
        log.info(
            "DRY_RUN sales.send tenant=%s channel=%s contact=%s body_len=%s",
            tenant_id,
            channel or "(no channel)",
            contact_id or "(no contact)",
            len(body),
        )
        return {
            "sent": True,
            "dry_run": True,
            "channel": channel,
            "contact_id": contact_id,
        }

    if not contact_id:
        raise DispatchError("sales draft missing metadata.contact_id")
    if not body.strip():
        raise DispatchError("sales draft body is empty")
    if not (is_email or is_sms):
        raise DispatchError(
            f"sales channel must indicate email or sms; got {channel!r}"
        )

    kind = _resolve_sales_kind(tenant_id)
    if not kind:
        raise DispatchError(
            "no CRM mapping configured for tenant "
            "(crm_mapping.json missing kind/crm/provider field)"
        )
    if kind not in _SALES_SUPPORTED_KINDS:
        raise DispatchError(
            f"unsupported CRM kind {kind!r}; "
            f"expected one of {_SALES_SUPPORTED_KINDS}"
        )

    if kind == "ghl":
        provider = ghl_provider.for_tenant(tenant_id)
        provider_error: type[Exception] = ghl_provider.GHLProviderError
    elif kind == "hubspot":
        provider = hubspot_provider.for_tenant(tenant_id)
        provider_error = hubspot_provider.HubSpotProviderError
    else:
        provider = pipedrive_provider.for_tenant(tenant_id)
        provider_error = pipedrive_provider.PipedriveProviderError

    if provider is None:
        raise DispatchError(f"no {kind} credentials stored for tenant")

    try:
        if is_email:
            message_id = provider.send_email(
                contact_id,
                subject or "(no subject)",
                body,
            )
        else:
            message_id = provider.send_sms(contact_id, body)
    except provider_error as exc:
        raise DispatchError(str(exc)) from exc

    return {
        "sent": True,
        "kind": kind,
        "channel": channel,
        "contact_id": contact_id,
        "message_id": message_id,
    }


def _smtp_send(
    host: str,
    port: int,
    *,
    username: str,
    password: str,
    msg: MIMEText,
) -> None:
    """Module-level SMTP helper so tests can monkeypatch without spinning up
    a real server. Real path uses SMTPS on 465; STARTTLS on 587 is left for
    a future tenant whose provider doesn't support implicit TLS."""
    with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
        smtp.login(username, password)
        smtp.send_message(msg)


def _send_email_assistant(tenant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Outgoing handler for the email_assistant pipeline.

    Sends a reply via SMTP using the tenant's stored Gmail App Password.
    Sets In-Reply-To + References headers from metadata.in_reply_to so the
    reply threads with the incoming email correctly. DRY_RUN gated; the
    live path requires gmail_app_password credentials with both
    email_address and app_password fields.
    """
    body = (payload.get("body") or "").strip()
    subject = payload.get("subject") or "(no subject)"
    metadata = payload.get("metadata") or {}
    to_addr = str(
        metadata.get("from_email")
        or payload.get("recipient_hint")
        or ""
    ).strip()
    in_reply_to = str(
        metadata.get("in_reply_to")
        or metadata.get("message_id")
        or ""
    ).strip()

    if _dry_run():
        log.info(
            "DRY_RUN email_assistant.send tenant=%s to=%s body_len=%s",
            tenant_id,
            to_addr or "(no addr)",
            len(body),
        )
        return {
            "sent": True,
            "dry_run": True,
            "to": to_addr,
            "body_len": len(body),
        }

    if not body:
        raise DispatchError("email_assistant body is empty")
    if not to_addr or "@" not in to_addr:
        raise DispatchError(
            f"email_assistant missing valid recipient (got {to_addr!r})"
        )

    creds = credentials.load(tenant_id, "gmail_app_password")
    if not creds:
        raise DispatchError("no gmail_app_password credentials stored for tenant")
    sender = str(creds.get("email_address") or "").strip()
    password = str(creds.get("app_password") or "")
    if not sender or not password:
        raise DispatchError(
            "gmail_app_password credential missing email_address or app_password"
        )

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to_addr
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to

    try:
        _smtp_send(
            "smtp.gmail.com",
            465,
            username=sender,
            password=password,
            msg=msg,
        )
    except (smtplib.SMTPException, OSError) as exc:
        raise DispatchError(f"SMTP send failed: {exc}") from exc

    return {"sent": True, "to": to_addr, "from": sender}


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

# Maps pipeline_id -> outgoing handler. Four roles are wired to live
# delivery paths (DRY_RUN gated per CLAUDE.md "test before first send"):
#
#   reviews         -> POST /v4/{review.name}/reply (GBP)
#   gbp             -> POST /v4/{location_path}/localPosts (GBP What's New)
#   sales           -> CRMProvider.send_email/send_sms (GHL/HubSpot/Pipedrive)
#   email_assistant -> SMTP via tenant Gmail App Password
#
# The remaining onboarding roles (blog, social, seo, chat_widget) intentionally
# stay unwired; /approvals will return {ok:False, reason:"no_dispatcher"} for
# them. Wiring those in lands alongside their generic run.py pipelines.
OUTGOING_HANDLERS: dict[str, Callable[[str, dict[str, Any]], dict[str, Any]]] = {
    "reviews": _send_review_reply,
    "gbp": _send_gbp_post,
    "sales": _send_sales,
    "email_assistant": _send_email_assistant,
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
