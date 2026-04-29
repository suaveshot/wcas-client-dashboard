"""Generic per-tenant Sales pipeline.

Run via:

    python -m wc_solns_pipelines.pipelines.sales.run --tenant <tenant_id>

What it does on each run, in order:
  1. Build a TenantContext for the tenant. Bail with status=error heartbeat
     if the slug is invalid.
  2. If the tenant is paused (tenant_config.json:status == "paused"), push
     a success heartbeat with summary="Paused" and exit 0.
  3. Resolve the tenant's CRM mapping. Missing mapping or unsupported kind
     -> error heartbeat, exit 0.
  4. Construct the matching CRMProvider via the per-vendor `for_tenant`
     factory. Verify the result is an instance of CRMProvider Protocol; if
     not, error heartbeat and exit 0. This guards the audit lesson where a
     partial provider class shipped without the methods consumers were
     calling, silently AttributeError'ing in production for ten days.
  5. Inbound lead ingest: list_contacts(), filter to ones not in
     state["seen_contacts"], score them, persist into state.
  6. Reply detection: for each in-flight contact with scheduled_message_ids,
     check inbound messages and cancel any pending sends if the lead replied.
  7. Cold first-touch drafting: pick up to N untouched leads, generate a
     draft via opus.chat, route through dispatch.send. HubSpot tenants get
     email-only paths since HubSpot's CRMProvider has no native SMS.
  8. Persist state, push heartbeat. Exit 0 always.

Exit code is always 0. Failures surface via the heartbeat status, never via
process exit, so cron entries don't get tagged red and watchdogs only flag
truly broken runs.

V1 scope: ingest + reply-cancel + cold first-touch only. Win/loss capture,
multi-touch follow-up sequencing, estimate view tracking, and adaptive A/B
variant generation are deliberate v2 TODOs marked inline below.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Callable

from dashboard_app.services import dispatch
from dashboard_app.services.crm_provider import CRMProvider
from dashboard_app.services.ghl_provider import for_tenant as _ghl_for_tenant
from dashboard_app.services.hubspot_provider import (
    HubSpotProviderError,
    for_tenant as _hubspot_for_tenant,
)
from dashboard_app.services.opus import OpusBudgetExceeded, OpusUnavailable, chat
from dashboard_app.services.pipedrive_provider import for_tenant as _pipedrive_for_tenant
from wc_solns_pipelines.shared.push_heartbeat import push as push_heartbeat
from wc_solns_pipelines.shared.tenant_runtime import TenantContext, TenantNotFound

PIPELINE_ID = "sales"
SEEN_CONTACTS_CAP = 1000
DEFAULT_MAX_DRAFTS = 5
SUPPORTED_KINDS = ("ghl", "hubspot", "pipedrive")

log = logging.getLogger("wcas.pipelines.sales")


# ---------------------------------------------------------------------------
# provider construction
# ---------------------------------------------------------------------------


def _provider_kind(mapping: dict[str, Any] | None) -> str:
    """Pull the CRM kind out of crm_mapping.json. Looks at top-level "kind"
    or "crm" or "provider" so the dashboard side can pick whichever feels
    most natural without breaking the pipeline."""
    if not isinstance(mapping, dict):
        return ""
    for key in ("kind", "crm", "provider", "provider_kind"):
        v = mapping.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip().lower()
    return ""


def default_provider_fn(ctx: TenantContext) -> CRMProvider | None:
    """Default provider factory. Reads ctx.crm_mapping(), dispatches to the
    matching `for_tenant(...)` helper. Returns None when no mapping or no
    creds; the caller turns that into an error heartbeat."""
    mapping = ctx.crm_mapping()
    kind = _provider_kind(mapping)
    if not kind:
        return None
    if kind == "ghl":
        return _ghl_for_tenant(ctx.tenant_id)
    if kind == "hubspot":
        return _hubspot_for_tenant(ctx.tenant_id)
    if kind == "pipedrive":
        return _pipedrive_for_tenant(ctx.tenant_id)
    return None


# ---------------------------------------------------------------------------
# scoring + helpers
# ---------------------------------------------------------------------------


def _has_email(contact: dict[str, Any]) -> bool:
    email = contact.get("email") or ""
    return bool(isinstance(email, str) and email.strip())


def _has_phone(contact: dict[str, Any]) -> bool:
    phone = contact.get("phone") or ""
    return bool(isinstance(phone, str) and phone.strip())


def _contact_id(contact: dict[str, Any]) -> str:
    cid = contact.get("id") or contact.get("contactId") or contact.get("contact_id")
    return str(cid) if cid else ""


def score_contact(contact: dict[str, Any]) -> int:
    """Tenant-agnostic lead score. Higher means warmer.

    +2 if both email and phone present (we can multi-channel).
    +1 if only one channel present (we can still touch).
    +1 recent-add bonus when dateAdded is within the last 7 days.
    """
    score = 0
    has_email = _has_email(contact)
    has_phone = _has_phone(contact)
    if has_email and has_phone:
        score += 2
    elif has_email or has_phone:
        score += 1

    raw_added = (
        contact.get("dateAdded")
        or contact.get("date_added")
        or contact.get("createdAt")
        or contact.get("created_at")
    )
    if isinstance(raw_added, str) and raw_added.strip():
        try:
            ts = datetime.fromisoformat(raw_added.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age_days = (datetime.now(timezone.utc) - ts).total_seconds() / 86400.0
            if age_days <= 7.0:
                score += 1
        except (ValueError, TypeError):
            pass

    return score


# ---------------------------------------------------------------------------
# draft generation
# ---------------------------------------------------------------------------


def _build_voice_system(ctx: TenantContext) -> str:
    """Stitch the tenant's voice + company + services KB into a system
    prompt that grounds the cold-outreach drafter in the owner's voice."""
    parts: list[str] = []
    voice_kb = ctx.kb("voice")
    if voice_kb:
        parts.append("Voice (how this business sounds):\n" + voice_kb.strip())
    company_kb = ctx.kb("company")
    if company_kb:
        parts.append("Company context:\n" + company_kb.strip())
    services_kb = ctx.kb("services")
    if services_kb:
        parts.append("What this business sells:\n" + services_kb.strip())
    voice_card = ctx.voice_card()
    if isinstance(voice_card, dict) and voice_card:
        parts.append("Voice card (structured):\n" + json.dumps(voice_card, indent=2))
    if not parts:
        return (
            "Write in a warm, plain-language voice. Sound like a real person "
            "who runs the business. Never corporate, never templated."
        )
    parts.append(
        "Always write in this voice. Reply with the message body only, no "
        "preamble, no labels, no markdown. Under 350 characters. No emojis."
    )
    return "\n\n".join(parts)


def _fallback_cold_draft(first_name: str) -> str:
    name = first_name or "there"
    return (
        f"Hi {name}, just wanted to introduce myself and say we'd love to "
        "help if you're ever looking for a hand. Open to a quick chat?"
    )


def _first_name(contact: dict[str, Any]) -> str:
    fn = (
        contact.get("firstName")
        or contact.get("first_name")
        or (contact.get("name") or "").split(" ")[0]
        or ""
    )
    return fn.strip() if isinstance(fn, str) else ""


def draft_cold_message(ctx: TenantContext, contact: dict[str, Any]) -> str:
    """Generate a Claude cold first-touch draft for one contact. Falls back
    to a short canned message on any Anthropic error so the pipeline keeps
    moving and the contact still gets a touch."""
    first = _first_name(contact)
    business = ""
    company_kb = ctx.kb("company") or ""
    if company_kb:
        first_line = company_kb.strip().splitlines()[0] if company_kb.strip() else ""
        business = first_line[:120]

    user_prompt = (
        f"Write a short cold first-touch outreach message to a new lead.\n"
        f"Lead first name: {first or '(unknown)'}\n"
        f"Reference business: {business or '(none)'}\n\n"
        "Write the message now."
    )

    try:
        result = chat(
            tenant_id=ctx.tenant_id,
            messages=[{"role": "user", "content": user_prompt}],
            system=_build_voice_system(ctx),
            max_tokens=400,
            temperature=0.5,
            kind="sales_cold_draft",
            note=f"contact_id={_contact_id(contact)[:24]}",
            cache_system=True,
        )
    except (OpusUnavailable, OpusBudgetExceeded) as exc:
        log.warning("Opus draft failed for cold contact: %s; using fallback", exc)
        return _fallback_cold_draft(first)
    except Exception as exc:  # noqa: BLE001 - drafter must never crash run
        log.warning("Opus draft errored for cold contact: %s; using fallback", exc)
        return _fallback_cold_draft(first)

    text = (result.text or "").strip()
    return text or _fallback_cold_draft(first)


# ---------------------------------------------------------------------------
# reply detection + scheduled-message cancel
# ---------------------------------------------------------------------------


def _msg_inbound_after(msg: dict[str, Any], cutoff_iso: str | None) -> bool:
    """True when the message looks inbound (lead-to-business) AND its
    timestamp is newer than `cutoff_iso`. Tolerant of provider field shape
    differences."""
    direction = str(msg.get("direction") or "").lower()
    if direction not in ("inbound", "in", "received"):
        return False
    if not cutoff_iso:
        return True
    raw_ts = (
        msg.get("dateAdded")
        or msg.get("date_added")
        or msg.get("createdAt")
        or msg.get("created_at")
        or msg.get("timestamp")
    )
    if not isinstance(raw_ts, str) or not raw_ts.strip():
        # No timestamp -> assume newer to err toward cancel-on-reply safety.
        return True
    try:
        ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        cutoff = datetime.fromisoformat(cutoff_iso.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return True
    return ts >= cutoff


def cancel_scheduled_on_reply(
    provider: CRMProvider,
    contact_id: str,
    scheduled_message_ids: list[str],
    scheduled_after: str | None,
) -> int:
    """If the contact replied since `scheduled_after`, cancel each pending
    scheduled message id and return the count of successful cancels.
    Returns 0 when no inbound reply was detected."""
    if not contact_id or not scheduled_message_ids:
        return 0
    try:
        conversations = provider.search_conversations(contact_id)
    except Exception as exc:  # noqa: BLE001 - vendor errors must not crash run
        log.warning("search_conversations failed for %s: %s", contact_id, exc)
        return 0

    replied = False
    for conv in conversations or []:
        conv_id = conv.get("id") or conv.get("conversationId") or conv.get("conversation_id")
        if not conv_id:
            continue
        try:
            msgs = provider.get_conversation_messages(str(conv_id))
        except Exception as exc:  # noqa: BLE001
            log.warning("get_conversation_messages failed for %s: %s", conv_id, exc)
            continue
        for msg in msgs or []:
            if _msg_inbound_after(msg, scheduled_after):
                replied = True
                break
        if replied:
            break

    if not replied:
        return 0

    cancelled = 0
    for msg_id in scheduled_message_ids:
        try:
            ok = provider.cancel_scheduled_message(str(msg_id))
        except Exception as exc:  # noqa: BLE001
            log.warning("cancel_scheduled_message failed for %s: %s", msg_id, exc)
            continue
        if ok:
            cancelled += 1
    return cancelled


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------


def _dispatch_one(
    tenant_id: str,
    contact: dict[str, Any],
    body: str,
    *,
    channel: str,
) -> dict[str, Any]:
    cid = _contact_id(contact)
    first = _first_name(contact)
    recipient = first or contact.get("email") or contact.get("phone") or "(lead)"
    subject = "Quick hello" if channel.endswith("email") else ""
    return dispatch.send(
        tenant_id=tenant_id,
        pipeline_id=PIPELINE_ID,
        channel=channel,
        recipient_hint=str(recipient)[:80],
        subject=subject,
        body=body,
        metadata={
            "contact_id": cid,
            "first_name": first,
            "email": contact.get("email"),
            "phone": contact.get("phone"),
            "score": score_contact(contact),
            "touch": "cold_first",
        },
    )


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------


def _pick_channel(contact: dict[str, Any], *, kind: str) -> str | None:
    """Pick the outbound channel for this contact, honoring HubSpot's
    no-SMS limitation. Returns None when no usable channel exists."""
    has_email = _has_email(contact)
    has_phone = _has_phone(contact)
    if kind == "hubspot":
        # HubSpotProvider.send_sms raises HubSpotProviderError. Force email.
        return "sales_cold_email" if has_email else None
    if has_email:
        return "sales_cold_email"
    if has_phone:
        return "sales_cold_sms"
    return None


def run(
    tenant_id: str,
    *,
    max_drafts: int = DEFAULT_MAX_DRAFTS,
    dry_run: bool = False,
    provider_fn: Callable[[TenantContext], CRMProvider | None] | None = None,
    draft_message_fn: Callable[[TenantContext, dict[str, Any]], str] = draft_cold_message,
    dispatch_fn: Callable[..., dict[str, Any]] = _dispatch_one,
    heartbeat_fn: Callable[..., int] = push_heartbeat,
) -> int:
    """Programmatic entry point. The injected callables make the pipeline
    fully testable without touching CRMs, Anthropic, or the live dashboard.
    """

    factory = provider_fn or default_provider_fn

    try:
        ctx = TenantContext(tenant_id)
    except TenantNotFound as exc:
        log.error("Tenant not found: %s", exc)
        if not dry_run:
            heartbeat_fn(
                tenant_id=tenant_id,
                pipeline_id=PIPELINE_ID,
                status="error",
                summary=f"Invalid tenant: {exc}",
            )
        return 0

    if ctx.is_paused:
        log.info("Tenant %s paused; skipping sales run", tenant_id)
        if not dry_run:
            heartbeat_fn(
                tenant_id=tenant_id,
                pipeline_id=PIPELINE_ID,
                status="success",
                summary="Paused; no sales touches sent.",
            )
        return 0

    mapping = ctx.crm_mapping()
    kind = _provider_kind(mapping)
    if not mapping or not kind:
        if not dry_run:
            heartbeat_fn(
                tenant_id=tenant_id,
                pipeline_id=PIPELINE_ID,
                status="error",
                summary="CRM not configured.",
            )
        return 0
    if kind not in SUPPORTED_KINDS:
        if not dry_run:
            heartbeat_fn(
                tenant_id=tenant_id,
                pipeline_id=PIPELINE_ID,
                status="error",
                summary=f"Unsupported CRM kind: {kind}",
            )
        return 0

    try:
        provider = factory(ctx)
    except Exception as exc:  # noqa: BLE001 - construction errors must not crash run
        log.warning("provider construction failed for %s: %s", tenant_id, exc)
        if not dry_run:
            heartbeat_fn(
                tenant_id=tenant_id,
                pipeline_id=PIPELINE_ID,
                status="error",
                summary=f"CRM provider construction failed: {type(exc).__name__}",
            )
        return 0

    if provider is None:
        if not dry_run:
            heartbeat_fn(
                tenant_id=tenant_id,
                pipeline_id=PIPELINE_ID,
                status="error",
                summary=f"CRM credentials missing for {kind}.",
            )
        return 0

    # Lesson: mistake_provider_abstraction_incomplete_method_surface.md.
    # Fail loud here rather than AttributeError'ing later mid-tick.
    if not isinstance(provider, CRMProvider):
        if not dry_run:
            heartbeat_fn(
                tenant_id=tenant_id,
                pipeline_id=PIPELINE_ID,
                status="error",
                summary="CRM provider does not satisfy CRMProvider Protocol.",
            )
        return 0

    state = ctx.read_state(PIPELINE_ID)
    seen_contacts: dict[str, dict[str, Any]] = dict(state.get("seen_contacts") or {})

    # ── 1. Reply detection + scheduled-message cancel ─────────────────────
    cancelled_total = 0
    for cid, entry in list(seen_contacts.items()):
        scheduled = entry.get("scheduled_message_ids") or []
        if not scheduled:
            continue
        scheduled_after = entry.get("last_scheduled_at")
        try:
            cancelled = cancel_scheduled_on_reply(
                provider, cid, list(scheduled), scheduled_after
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("reply detect raised for %s: %s", cid, exc)
            cancelled = 0
        if cancelled:
            cancelled_total += cancelled
            entry["scheduled_message_ids"] = []
            entry["replied"] = True
            entry["replied_at"] = datetime.now(timezone.utc).isoformat()

    # ── 2. Inbound lead ingest ────────────────────────────────────────────
    try:
        contacts = provider.list_contacts() or []
    except Exception as exc:  # noqa: BLE001 - vendor errors must not crash run
        log.warning("list_contacts failed for %s: %s", tenant_id, exc)
        # Persist any reply-cancel mutations made above before bailing,
        # otherwise next tick re-cancels messages we already cancelled.
        if cancelled_total:
            ctx.write_state(
                PIPELINE_ID,
                {
                    "seen_contacts": _cap_seen(seen_contacts),
                    "last_check": datetime.now(timezone.utc).isoformat(),
                    "drafted_total": int(state.get("drafted_total") or 0),
                },
            )
        if not dry_run:
            heartbeat_fn(
                tenant_id=tenant_id,
                pipeline_id=PIPELINE_ID,
                status="error",
                summary=f"list_contacts failed: {type(exc).__name__}",
            )
        return 0

    new_contacts: list[dict[str, Any]] = []
    for contact in contacts:
        cid = _contact_id(contact)
        if not cid or cid in seen_contacts:
            continue
        new_contacts.append(contact)
        seen_contacts[cid] = {
            "first_seen_at": datetime.now(timezone.utc).isoformat(),
            "score": score_contact(contact),
            "touched": False,
            "scheduled_message_ids": [],
        }

    if not new_contacts and cancelled_total == 0:
        # No new leads AND no replies - quick exit with the standard summary.
        ctx.write_state(
            PIPELINE_ID,
            {
                "seen_contacts": _cap_seen(seen_contacts),
                "last_check": datetime.now(timezone.utc).isoformat(),
                "drafted_total": int(state.get("drafted_total") or 0),
            },
        )
        summary = "No new leads."
        if dry_run:
            print(json.dumps({"heartbeat": {"status": "success", "summary": summary}}, indent=2))
            return 0
        heartbeat_fn(
            tenant_id=tenant_id,
            pipeline_id=PIPELINE_ID,
            status="success",
            summary=summary,
        )
        return 0

    # ── 3. Cold first-touch drafting ──────────────────────────────────────
    # Sort by score desc so highest-quality leads get drafts first when the
    # cap is reached.
    candidates = sorted(
        new_contacts, key=lambda c: score_contact(c), reverse=True
    )[:max_drafts]

    drafted = 0
    queued = 0
    delivered = 0
    failed = 0
    skipped_no_channel = 0

    for contact in candidates:
        cid = _contact_id(contact)
        if not cid:
            continue

        channel = _pick_channel(contact, kind=kind)
        if channel is None:
            skipped_no_channel += 1
            continue

        body = draft_message_fn(ctx, contact)
        if not body:
            # Fallback empty draft -> skip rather than send a blank message.
            failed += 1
            continue

        if dry_run:
            print(json.dumps({
                "contact_id": cid,
                "first_name": _first_name(contact),
                "channel": channel,
                "score": score_contact(contact),
                "draft": body,
            }, indent=2))
            drafted += 1
            seen_contacts[cid]["touched"] = True
            seen_contacts[cid]["last_touched_at"] = datetime.now(timezone.utc).isoformat()
            continue

        try:
            outcome = dispatch_fn(tenant_id, contact, body, channel=channel)
        except HubSpotProviderError as exc:
            # Defensive: dispatcher should never reach send_sms for HubSpot
            # because _pick_channel forces email above, but if a custom
            # dispatcher is injected we surface the skip cleanly.
            log.warning("HubSpot send raised: %s; skipping contact %s", exc, cid)
            skipped_no_channel += 1
            continue
        except Exception as exc:  # noqa: BLE001
            log.warning("dispatch raised for %s: %s", cid, exc)
            failed += 1
            continue

        action = outcome.get("action")
        if action == "queued":
            queued += 1
            seen_contacts[cid]["touched"] = True
            seen_contacts[cid]["last_draft_id"] = outcome.get("draft_id")
            seen_contacts[cid]["last_touched_at"] = datetime.now(timezone.utc).isoformat()
        elif action == "delivered":
            delivered += 1
            seen_contacts[cid]["touched"] = True
            seen_contacts[cid]["last_touched_at"] = datetime.now(timezone.utc).isoformat()
        elif action == "skipped":
            log.info("dispatch reports paused mid-run; stopping early")
            break
        else:
            failed += 1
            log.warning(
                "dispatch %s for contact %s: %s",
                action,
                cid,
                outcome.get("reason") or outcome,
            )
            continue
        drafted += 1

    # TODO(v2): Multi-touch follow-up sequencing (touches 2/3/4).
    # TODO(v2): Win/loss outcome capture via has_viewed_estimate +
    #           update_opportunity_stage when the lead replies positively.
    # TODO(v2): Adaptive A/B variant generation - pick the better-performing
    #           subject line / opener per channel after enough touches.
    # TODO(v2): Estimate view tracking - poll has_viewed_estimate for
    #           outstanding proposals and trigger a same-day nudge.

    new_state = {
        "seen_contacts": _cap_seen(seen_contacts),
        "last_check": datetime.now(timezone.utc).isoformat(),
        "drafted_total": int(state.get("drafted_total") or 0) + drafted,
    }
    ctx.write_state(PIPELINE_ID, new_state)

    summary = _build_summary(
        new_total=len(new_contacts),
        drafted=drafted,
        queued=queued,
        delivered=delivered,
        failed=failed,
        skipped_no_channel=skipped_no_channel,
        cancelled=cancelled_total,
    )
    status = "error" if failed and (queued + delivered) == 0 and drafted == 0 else "success"

    if dry_run:
        print(json.dumps({"heartbeat": {"status": status, "summary": summary}}, indent=2))
        return 0

    heartbeat_fn(
        tenant_id=tenant_id,
        pipeline_id=PIPELINE_ID,
        status=status,
        summary=summary,
    )
    return 0


def _cap_seen(seen: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Cap the seen_contacts dict at SEEN_CONTACTS_CAP entries by dropping
    the oldest first_seen_at first. Prevents unbounded growth on
    high-volume tenants."""
    if len(seen) <= SEEN_CONTACTS_CAP:
        return seen
    items = sorted(
        seen.items(),
        key=lambda kv: kv[1].get("first_seen_at", ""),
    )
    return dict(items[-SEEN_CONTACTS_CAP:])


def _build_summary(
    *,
    new_total: int,
    drafted: int,
    queued: int,
    delivered: int,
    failed: int,
    skipped_no_channel: int,
    cancelled: int,
) -> str:
    parts: list[str] = []
    if new_total:
        parts.append(f"Drafted {drafted} of {new_total}")
    else:
        parts.append("Drafted 0 new")
    if queued:
        parts.append(f"{queued} queued")
    if delivered:
        parts.append(f"{delivered} sent")
    if failed:
        parts.append(f"{failed} failed")
    if skipped_no_channel:
        parts.append(f"{skipped_no_channel} no-channel")
    if cancelled:
        parts.append(f"{cancelled} cancelled on reply")
    return "; ".join(parts) + "."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generic per-tenant Sales pipeline (V1).",
    )
    parser.add_argument("--tenant", required=True, help="tenant_id slug")
    parser.add_argument(
        "--max",
        type=int,
        default=DEFAULT_MAX_DRAFTS,
        dest="max_drafts",
        help="Max cold drafts to generate this run (default: 5)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print drafts + heartbeat payload, do not dispatch or POST.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    return run(
        tenant_id=args.tenant,
        max_drafts=args.max_drafts,
        dry_run=args.dry_run,
    )


__all__ = [
    "PIPELINE_ID",
    "SUPPORTED_KINDS",
    "default_provider_fn",
    "draft_cold_message",
    "score_contact",
    "cancel_scheduled_on_reply",
    "run",
    "main",
]


if __name__ == "__main__":
    sys.exit(main())
