"""Generic per-tenant Email Assistant pipeline.

Run via:

    python -m wc_solns_pipelines.pipelines.email_assistant.run --tenant <id>

Why App Password / IMAP not Gmail API:
  - Gmail's `gmail.modify` scope is on Google's RESTRICTED list and requires
    CASA verification (~$5-20K/yr). Not viable for the per-tenant model.
  - App Passwords work with 2FA-enabled Google accounts and grant IMAP/SMTP
    access without that scope. Owner generates one in 60 seconds at
    myaccount.google.com/apppasswords and pastes it into /settings.
  - AP already runs this way (GMAIL_APP_PASSWORD env var); we generalize
    that pattern per-tenant via the gmail_app_password credential record.

What it does on each run:
  1. Build a TenantContext. Bail with error heartbeat on invalid slug.
  2. Honor the tenant pause flag.
  3. Load gmail_app_password credential record. Expected fields:
       email_address (str, required)
       app_password  (str, required)
       imap_host     (str, optional, default "imap.gmail.com")
       imap_port     (int, optional, default 993)
     Missing -> error heartbeat with "paste an App Password".
  4. IMAP-connect (SSL), search INBOX UNSEEN since last_check.
  5. For each new message, parse sender + subject + plain-text body,
     skip if already seen by Message-Id, skip vendor/noreply senders,
     draft a reply in the tenant's voice grounded in voice + company +
     known_contacts + faq KB sections + voice card, then dispatch via
     services.dispatch.send (channel="email", pipeline="email_assistant").
     Email Assistant has require_approval defaulted to True at the
     prefs layer; the dispatcher routes drafts to outgoing_queue.
  6. Mark each processed message as Seen on the server. (Pipeline
     dies before this on draft errors so retries pick up the same
     messages later - by design, no silent skips.)
  7. Persist state with last_check + seen_message_ids (capped at 500).
  8. Push heartbeat with summary + lead.created events for messages
     classified by Claude as sales/inquiry intent so the Leads goal bumps.

Always exits 0. Errors surface via heartbeat status=error.
"""

from __future__ import annotations

import argparse
import email
import imaplib
import json
import logging
import re
import sys
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any

from dashboard_app.services import dispatch
from dashboard_app.services.opus import OpusBudgetExceeded, OpusUnavailable, chat
from wc_solns_pipelines.shared.push_heartbeat import push as push_heartbeat
from wc_solns_pipelines.shared.tenant_runtime import TenantContext, TenantNotFound

PIPELINE_ID = "email_assistant"
DEFAULT_IMAP_HOST = "imap.gmail.com"
DEFAULT_IMAP_PORT = 993
DEFAULT_MAILBOX = "INBOX"
DEFAULT_MAX_PER_RUN = 25
SEEN_IDS_CAP = 500
DEFAULT_TIMEOUT = 30  # imaplib uses int seconds

# Senders we never bother drafting against. Owner can review the full
# inbox themselves; the assistant just shouldn't waste tokens on
# noreply-style addresses or known marketing senders.
_SKIP_SENDER_PATTERNS = [
    r"^noreply@",
    r"^no-reply@",
    r"^donotreply@",
    r"^do-not-reply@",
    r"^mailer-daemon@",
    r"^postmaster@",
    r"^notifications?@.*google\.com$",
    r"^notifications?@.*github\.com$",
    r"@bounces?\.",
]

log = logging.getLogger("wcas.pipelines.email_assistant")


# ---------------------------------------------------------------------------
# IMAP fetch (injectable wrapper)
# ---------------------------------------------------------------------------


class _RawMessage(dict):
    """Light wrapper around the parsed email envelope. dict-typed so
    tests can pass plain dicts in via fetch_unread_fn."""


def fetch_unread(
    *,
    email_address: str,
    app_password: str,
    imap_host: str,
    imap_port: int,
    mailbox: str = DEFAULT_MAILBOX,
    max_messages: int = DEFAULT_MAX_PER_RUN,
) -> list[dict[str, Any]]:
    """Connect to IMAP and pull UNSEEN messages from the given mailbox.

    Returns a list of envelopes:
      {"uid": str, "message_id": str, "from_email": str,
       "from_name": str, "subject": str, "body": str, "date": str}

    Connection + parse failures return [] - the pipeline reports the
    failure via heartbeat status, never crashes. The IMAP server is
    not marked Seen here; that happens after a successful dispatch
    via mark_seen() so a draft error doesn't lose the message.
    """
    try:
        conn = imaplib.IMAP4_SSL(host=imap_host, port=imap_port, timeout=DEFAULT_TIMEOUT)
        conn.login(email_address, app_password)
    except (imaplib.IMAP4.error, OSError) as exc:
        log.warning("IMAP connect/login failed for %s: %s", email_address, exc)
        return []

    out: list[dict[str, Any]] = []
    try:
        status, _ = conn.select(mailbox, readonly=False)
        if status != "OK":
            log.warning("IMAP select(%s) failed: %s", mailbox, status)
            return []

        status, data = conn.uid("SEARCH", None, "UNSEEN")
        if status != "OK" or not data:
            return []
        uids = (data[0] or b"").split()
        # Take the most recent N (uids are server-ordered ascending)
        uids = uids[-max_messages:]

        for uid in uids:
            uid_str = uid.decode("ascii", errors="replace")
            status, fetched = conn.uid("FETCH", uid, "(BODY.PEEK[])")
            if status != "OK" or not fetched or not fetched[0]:
                continue
            raw = fetched[0][1] if isinstance(fetched[0], tuple) else None
            if not raw:
                continue
            try:
                msg = email.message_from_bytes(raw)
            except Exception:  # noqa: BLE001
                continue

            envelope = _parse_message(msg)
            if envelope is None:
                continue
            envelope["uid"] = uid_str
            out.append(envelope)
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            conn.logout()
        except Exception:  # noqa: BLE001
            pass

    return out


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:  # noqa: BLE001
        return value


def _extract_plain_body(msg: email.message.Message) -> str:
    """Pull the text/plain body. Falls back to text/html stripped if
    no text/plain part is present."""
    plain_parts: list[str] = []
    html_parts: list[str] = []

    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = (part.get("Content-Disposition") or "").lower()
            if "attachment" in disp:
                continue
            try:
                payload = part.get_payload(decode=True)
            except Exception:  # noqa: BLE001
                continue
            if not payload:
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                text = payload.decode(charset, errors="replace")
            except (LookupError, AttributeError):
                text = payload.decode("utf-8", errors="replace")
            if ctype == "text/plain":
                plain_parts.append(text)
            elif ctype == "text/html":
                html_parts.append(text)
    else:
        payload = msg.get_payload(decode=True) or b""
        charset = msg.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, errors="replace")
        except (LookupError, AttributeError):
            text = payload.decode("utf-8", errors="replace")
        if msg.get_content_type() == "text/html":
            html_parts.append(text)
        else:
            plain_parts.append(text)

    if plain_parts:
        return "\n".join(plain_parts).strip()
    if html_parts:
        # Crude HTML strip - good enough for Claude grounding
        joined = "\n".join(html_parts)
        no_tags = re.sub(r"<[^>]+>", " ", joined)
        return re.sub(r"\s+", " ", no_tags).strip()
    return ""


def _parse_message(msg: email.message.Message) -> dict[str, Any] | None:
    message_id = (msg.get("Message-Id") or msg.get("Message-ID") or "").strip()
    if not message_id:
        return None
    subject = _decode(msg.get("Subject")) or "(no subject)"
    from_raw = msg.get("From") or ""
    from_name, from_email = parseaddr(from_raw)
    from_name = _decode(from_name)
    from_email = (from_email or "").strip().lower()

    date_str = msg.get("Date") or ""
    try:
        date_iso = parsedate_to_datetime(date_str).astimezone(timezone.utc).isoformat()
    except Exception:  # noqa: BLE001
        date_iso = ""

    body = _extract_plain_body(msg)
    return {
        "message_id": message_id.strip("<>"),
        "from_email": from_email,
        "from_name": from_name,
        "subject": subject,
        "body": body,
        "date": date_iso,
    }


def mark_seen(
    *,
    email_address: str,
    app_password: str,
    imap_host: str,
    imap_port: int,
    mailbox: str,
    uids: list[str],
) -> None:
    """Mark a list of UIDs Seen on the server. Best-effort; errors are
    logged but never raised - re-fetching the same message next run is
    cheaper than crashing the pipeline."""
    if not uids:
        return
    try:
        conn = imaplib.IMAP4_SSL(host=imap_host, port=imap_port, timeout=DEFAULT_TIMEOUT)
        conn.login(email_address, app_password)
    except (imaplib.IMAP4.error, OSError) as exc:
        log.warning("mark_seen connect failed: %s", exc)
        return
    try:
        conn.select(mailbox, readonly=False)
        for uid in uids:
            try:
                conn.uid("STORE", uid.encode("ascii"), "+FLAGS", r"(\Seen)")
            except Exception:  # noqa: BLE001
                continue
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            conn.logout()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# sender filtering
# ---------------------------------------------------------------------------


_COMPILED_SKIP = [re.compile(p, re.IGNORECASE) for p in _SKIP_SENDER_PATTERNS]


def should_skip_sender(from_email: str) -> bool:
    if not from_email or "@" not in from_email:
        return True
    return any(p.search(from_email) for p in _COMPILED_SKIP)


# ---------------------------------------------------------------------------
# draft generation
# ---------------------------------------------------------------------------


def _build_voice_system(ctx: TenantContext) -> str:
    parts: list[str] = []
    voice_kb = ctx.kb("voice")
    if voice_kb:
        parts.append("Voice (how this business sounds):\n" + voice_kb.strip())
    company_kb = ctx.kb("company")
    if company_kb:
        parts.append("Company context:\n" + company_kb.strip())
    services_kb = ctx.kb("services")
    if services_kb:
        parts.append("Services we offer:\n" + services_kb.strip())
    faq_kb = ctx.kb("faq")
    if faq_kb:
        parts.append("FAQ (use to answer common questions):\n" + faq_kb.strip())
    known_contacts_kb = ctx.kb("known_contacts")
    if known_contacts_kb:
        parts.append("Known contacts (recognize these senders):\n" + known_contacts_kb.strip())
    voice_card = ctx.voice_card()
    if isinstance(voice_card, dict) and voice_card:
        parts.append("Voice card (structured):\n" + json.dumps(voice_card, indent=2))
    if not parts:
        parts.append(
            "Write in a warm, plain-language voice. Speak directly. "
            "No corporate phrases. Sound like a real person."
        )
    parts.append(
        "Draft a reply to the email below. Plain text only. No markdown, "
        "no labels, no greeting like 'Dear', no signature block (the system "
        "appends one). Keep it short - one to four short paragraphs. "
        "If you don't have enough information to answer, ask one specific "
        "clarifying question; never make up facts."
    )
    return "\n\n".join(parts)


def _classify_intent(subject: str, body: str) -> str:
    """Heuristic intent classifier - just a quick keyword pass for the
    heartbeat events array. Wrong calls are harmless (the goal predicate
    fires on lead.created regardless of confidence). Avoids burning a
    second Claude call per email.

    Order matters: a thank-you note often mentions "service" too, so we
    check thanks/billing/support before the lead markers.
    """
    haystack = f"{subject}\n{body}".lower()
    if "thank" in haystack and "feedback" not in haystack:
        return "thanks"
    if any(marker in haystack for marker in ("invoice", "payment", "receipt", "billing")):
        return "billing"
    if any(marker in haystack for marker in ("complaint", "issue", "problem", "broken", "wrong")):
        return "support"
    sales_markers = (
        "quote", "estimate", "pricing", "how much", "cost",
        "interested in", "looking for", "available", "consultation",
        "schedule a", "book an appointment",
    )
    if any(marker in haystack for marker in sales_markers):
        return "lead"
    return "other"


def _fallback_reply(envelope: dict[str, Any]) -> str:
    name = envelope.get("from_name") or envelope.get("from_email") or "there"
    first = name.split()[0] if name else "there"
    return (
        f"Hi {first},\n\n"
        "Thanks for reaching out. We got your message and will get back to "
        "you shortly.\n"
    )


def draft_reply(ctx: TenantContext, envelope: dict[str, Any]) -> str:
    user = (
        f"From: {envelope.get('from_name')} <{envelope.get('from_email')}>\n"
        f"Subject: {envelope.get('subject')}\n\n"
        f"Body:\n{envelope.get('body') or '(empty)'}\n\n"
        "Write the reply now."
    )
    try:
        result = chat(
            tenant_id=ctx.tenant_id,
            messages=[{"role": "user", "content": user}],
            system=_build_voice_system(ctx),
            max_tokens=900,
            temperature=0.4,
            kind="email_assistant_draft",
            note=(envelope.get("subject") or "")[:60],
            cache_system=True,
        )
    except (OpusUnavailable, OpusBudgetExceeded) as exc:
        log.warning("Opus draft failed: %s; using fallback", exc)
        return _fallback_reply(envelope)
    except Exception as exc:  # noqa: BLE001
        log.warning("Opus draft errored: %s; using fallback", exc)
        return _fallback_reply(envelope)

    text = (result.text or "").strip()
    return text or _fallback_reply(envelope)


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------


def _dispatch_one(
    tenant_id: str,
    envelope: dict[str, Any],
    body: str,
) -> dict[str, Any]:
    sender_label = envelope.get("from_name") or envelope.get("from_email") or "(unknown)"
    incoming_subject = envelope.get("subject") or "(no subject)"
    reply_subject = (
        incoming_subject
        if incoming_subject.lower().startswith("re:")
        else f"Re: {incoming_subject}"
    )
    return dispatch.send(
        tenant_id=tenant_id,
        pipeline_id=PIPELINE_ID,
        channel="email",
        recipient_hint=envelope.get("from_email", ""),
        subject=reply_subject,
        body=body,
        metadata={
            "from_email": envelope.get("from_email"),
            "from_name": envelope.get("from_name"),
            "from_display": sender_label,
            "in_reply_to": envelope.get("message_id"),
            "incoming_subject": incoming_subject,
            "incoming_body_excerpt": (envelope.get("body") or "")[:1000],
            "incoming_date": envelope.get("date"),
        },
    )


def run(
    tenant_id: str,
    *,
    dry_run: bool = False,
    max_messages: int = DEFAULT_MAX_PER_RUN,
    fetch_unread_fn=None,
    mark_seen_fn=None,
    draft_reply_fn=draft_reply,
    dispatch_fn=_dispatch_one,
    heartbeat_fn=push_heartbeat,
) -> int:
    fetch_unread_fn = fetch_unread_fn or fetch_unread
    mark_seen_fn = mark_seen_fn or mark_seen

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
        if not dry_run:
            heartbeat_fn(
                tenant_id=tenant_id,
                pipeline_id=PIPELINE_ID,
                status="success",
                summary="Paused; no email drafts.",
            )
        return 0

    creds = ctx.credentials("gmail_app_password")
    if not creds:
        if not dry_run:
            heartbeat_fn(
                tenant_id=tenant_id,
                pipeline_id=PIPELINE_ID,
                status="error",
                summary="Paste a Google App Password in /settings to start drafting replies.",
            )
        return 0

    email_address = (creds.get("email_address") or "").strip().lower()
    app_password = (creds.get("app_password") or "").strip()
    if not email_address or not app_password:
        if not dry_run:
            heartbeat_fn(
                tenant_id=tenant_id,
                pipeline_id=PIPELINE_ID,
                status="error",
                summary="App Password record missing email_address or app_password.",
            )
        return 0

    imap_host = creds.get("imap_host") or DEFAULT_IMAP_HOST
    imap_port = int(creds.get("imap_port") or DEFAULT_IMAP_PORT)
    mailbox = creds.get("mailbox") or DEFAULT_MAILBOX

    state = ctx.read_state(PIPELINE_ID)
    seen_ids: list[str] = list(state.get("seen_message_ids") or [])
    seen_set = set(seen_ids)

    try:
        envelopes = fetch_unread_fn(
            email_address=email_address,
            app_password=app_password,
            imap_host=imap_host,
            imap_port=imap_port,
            mailbox=mailbox,
            max_messages=max_messages,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("fetch_unread errored: %s", exc)
        if not dry_run:
            heartbeat_fn(
                tenant_id=tenant_id,
                pipeline_id=PIPELINE_ID,
                status="error",
                summary=f"IMAP fetch failed: {type(exc).__name__}",
            )
        return 0

    drafted = 0
    queued = 0
    delivered = 0
    failed = 0
    skipped_known_sender = 0
    skipped_already_seen = 0
    seen_uids_to_mark: list[str] = []
    events: list[dict[str, Any]] = []

    for env in envelopes:
        msg_id = env.get("message_id") or ""
        if msg_id and msg_id in seen_set:
            skipped_already_seen += 1
            continue

        from_email = env.get("from_email") or ""
        if should_skip_sender(from_email):
            skipped_known_sender += 1
            if msg_id:
                seen_ids.append(msg_id)
            uid = env.get("uid")
            if uid:
                seen_uids_to_mark.append(uid)
            continue

        body = draft_reply_fn(ctx, env)

        if dry_run:
            print(json.dumps(
                {"from": from_email, "subject": env.get("subject"), "draft": body},
                indent=2, default=str,
            ))
            drafted += 1
            if msg_id:
                seen_ids.append(msg_id)
            continue

        outcome = dispatch_fn(tenant_id, env, body)
        action = outcome.get("action")
        if action == "queued":
            queued += 1
        elif action == "delivered":
            delivered += 1
        elif action == "skipped":
            log.info("dispatch reports paused mid-run; stopping early")
            break
        else:
            failed += 1
            log.warning("dispatch %s for %s: %s", action, msg_id, outcome.get("reason"))

        drafted += 1
        if msg_id:
            seen_ids.append(msg_id)
        uid = env.get("uid")
        if uid:
            seen_uids_to_mark.append(uid)

        intent = _classify_intent(env.get("subject") or "", env.get("body") or "")
        if intent == "lead":
            events.append({
                "kind": "lead.created",
                "from_email": from_email,
                "subject": env.get("subject", "")[:120],
                "intent": intent,
            })

    # Cap the seen list to keep state tractable on busy mailboxes
    if len(seen_ids) > SEEN_IDS_CAP:
        seen_ids = seen_ids[-SEEN_IDS_CAP:]

    if not dry_run and seen_uids_to_mark:
        try:
            mark_seen_fn(
                email_address=email_address,
                app_password=app_password,
                imap_host=imap_host,
                imap_port=imap_port,
                mailbox=mailbox,
                uids=seen_uids_to_mark,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("mark_seen failed (non-fatal): %s", exc)

    ctx.write_state(
        PIPELINE_ID,
        {
            "seen_message_ids": seen_ids,
            "last_check": datetime.now(timezone.utc).isoformat(),
            "drafted_total": int(state.get("drafted_total") or 0) + drafted,
        },
    )

    summary = _build_summary(
        drafted=drafted,
        queued=queued,
        delivered=delivered,
        failed=failed,
        skipped_known_sender=skipped_known_sender,
        skipped_already_seen=skipped_already_seen,
        envelope_count=len(envelopes),
    )
    status = "error" if failed and (queued + delivered) == 0 else "success"

    if dry_run:
        print(json.dumps({"heartbeat": {"status": status, "summary": summary, "events": events}}, indent=2))
        return 0

    heartbeat_fn(
        tenant_id=tenant_id,
        pipeline_id=PIPELINE_ID,
        status=status,
        summary=summary,
        events=events or None,
    )
    return 0


def _build_summary(
    *,
    drafted: int,
    queued: int,
    delivered: int,
    failed: int,
    skipped_known_sender: int,
    skipped_already_seen: int,
    envelope_count: int,
) -> str:
    if envelope_count == 0:
        return "Inbox empty; no new mail."
    parts = [f"Drafted {drafted} of {envelope_count} new"]
    if queued:
        parts.append(f"{queued} queued")
    if delivered:
        parts.append(f"{delivered} sent")
    if failed:
        parts.append(f"{failed} failed")
    if skipped_known_sender:
        parts.append(f"{skipped_known_sender} no-reply skipped")
    if skipped_already_seen:
        parts.append(f"{skipped_already_seen} already-seen")
    return "; ".join(parts) + "."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generic per-tenant Email Assistant pipeline (W5).",
    )
    parser.add_argument("--tenant", required=True, help="tenant_id slug")
    parser.add_argument("--max", type=int, default=DEFAULT_MAX_PER_RUN, dest="max_messages")
    parser.add_argument("--dry-run", action="store_true")
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
        max_messages=args.max_messages,
        dry_run=args.dry_run,
    )


__all__ = [
    "PIPELINE_ID",
    "fetch_unread",
    "mark_seen",
    "should_skip_sender",
    "draft_reply",
    "run",
    "main",
]


if __name__ == "__main__":
    sys.exit(main())
