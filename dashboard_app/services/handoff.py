"""Activation handoff letter.

Built for the W6 mark_activation_complete moment: when the orchestrator
flips a tenant to activated, this composes a one-page "what happens this
week" letter and emails it to the owner.

The deliverable is intentionally HTML + plain-text twins (not a real PDF
yet) so we ship dependency-free. `render()` returns both bodies; the
email sender already supports multipart. Adding a real PDF binding later
is a one-line swap because every caller goes through `send_handoff()`.

The letter answers three questions the owner asked during activation:

  1. What did we just turn on? (List the enabled automations from the
     tenant_automations + automation_catalog combo.)
  2. What happens this week? (One line per automation explaining when
     it next fires and what the owner will see.)
  3. Who do I ping if something looks off? (Sam, with email + phone.)

Tone: short, founder-to-founder, zero corporate symmetry. No emoji, no
em-dashes, follows the WCAS relief framing.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from html import escape
from typing import Any

from . import (
    automation_catalog,
    email_sender,
    tenant_automations,
    tenant_kb,
)

log = logging.getLogger("dashboard.handoff")


# Per-automation "what happens this week" copy. Falls back to a generic
# line for any automation we haven't written specific copy for yet.
_THIS_WEEK_BY_ID: dict[str, str] = {
    "gbp": "We post a fresh Google Business Profile update every Monday.",
    "seo": "Your weekly SEO digest lands in your inbox every Monday.",
    "reviews": "Every new Google review gets a draft reply within 4 hours.",
    "blog": "A new SEO-driven blog post publishes the first Monday of the month.",
    "social": "We post to your social channels Tuesday, Thursday, Saturday.",
    "email_assistant": "Inbound owner-email replies are drafted within 15 minutes.",
    "chat_widget": "The chat widget answers visitor questions 24/7.",
    "voice_ai": "After-hours calls are answered by a voice agent in your tone.",
    "seo_recs": "A weekly recommendations digest queues up Monday morning.",
}


def _fmt_when(now: datetime | None = None) -> str:
    n = now or datetime.now(timezone.utc)
    return n.strftime("%B %d, %Y")


def _enabled_with_catalog(tenant_id: str) -> list[automation_catalog.Automation]:
    """Resolve the tenant's enabled automation IDs into Automation entries.
    Skips IDs that aren't in the catalog (e.g., legacy heartbeats)."""
    out: list[automation_catalog.Automation] = []
    for aid in tenant_automations.enabled_ids(tenant_id):
        entry = automation_catalog.get(aid)
        if entry is not None:
            out.append(entry)
    return out


def render(
    *,
    tenant_id: str,
    owner_name: str,
    business_name: str | None = None,
    now: datetime | None = None,
) -> tuple[str, str, str]:
    """Render the handoff letter.

    Returns (subject, html_body, text_body). Raises ValueError on missing
    required fields - this is a deliberate failure so we never email a
    blank letter.
    """
    if not tenant_id or not owner_name:
        raise ValueError("tenant_id and owner_name are required")

    biz = business_name or owner_name
    when = _fmt_when(now)
    enabled = _enabled_with_catalog(tenant_id)

    if not enabled:
        # Pull from KB as a fallback so a tenant without an
        # automations.json (e.g., AP) still gets a meaningful letter.
        kb_lines = ["(no automations enabled yet)"]
    else:
        kb_lines = [f"{a.name} - {_THIS_WEEK_BY_ID.get(a.id, 'runs on its scheduled cadence')}"
                    for a in enabled]

    subject = f"What we just turned on for {biz}"

    # ---- HTML body (one-page letter) ---------------------------------
    items_html = "\n".join(
        f'      <li><strong>{escape(a.name)}</strong> - '
        f'{escape(_THIS_WEEK_BY_ID.get(a.id, "runs on its scheduled cadence."))}</li>'
        for a in enabled
    ) or '      <li>No automations enabled yet. We will follow up before any system fires.</li>'

    html_body = f"""<!doctype html>
<html><body style="font-family:'DM Sans',system-ui,sans-serif;color:#0F2A44;line-height:1.55;max-width:640px;margin:24px auto;padding:24px;">
  <p style="color:#84715A;font-size:13px;margin:0 0 4px;">Activation complete - {escape(when)}</p>
  <h1 style="font-family:'DM Serif Display',Georgia,serif;font-size:28px;margin:0 0 16px;">
    Hi {escape(owner_name.split()[0])},
  </h1>
  <p>Your roles are connected. Here is what we just turned on for {escape(biz)}, and what happens this week so you can sleep on it.</p>

  <h2 style="font-family:'DM Serif Display',Georgia,serif;font-size:20px;margin:28px 0 8px;">What is now running</h2>
  <ul style="padding-left:20px;margin:0 0 20px;">
{items_html}
  </ul>

  <h2 style="font-family:'DM Serif Display',Georgia,serif;font-size:20px;margin:28px 0 8px;">What you will see this week</h2>
  <ul style="padding-left:20px;margin:0 0 20px;">
    <li>Each role checks in with a heartbeat as it runs. Your dashboard rings turn green on first run.</li>
    <li>If anything errors, the dashboard surfaces it before we do. You will not have to chase status.</li>
    <li>Review drafts, blog posts, and replies sit in your dashboard for one click of approval.</li>
  </ul>

  <h2 style="font-family:'DM Serif Display',Georgia,serif;font-size:20px;margin:28px 0 8px;">If something looks off</h2>
  <p>Reply to this email or text Sam directly at (562) 968-4474. Real human, fast turnaround.</p>

  <p style="color:#84715A;font-size:13px;margin-top:32px;">Sam Alarcon<br>WestCoast Automation Solutions</p>
</body></html>"""

    # ---- Plain text twin --------------------------------------------
    items_txt = "\n".join(f"  - {line}" for line in kb_lines)
    text_body = f"""Activation complete - {when}

Hi {owner_name.split()[0]},

Your roles are connected. Here is what we just turned on for {biz},
and what happens this week so you can sleep on it.

WHAT IS NOW RUNNING
{items_txt}

WHAT YOU WILL SEE THIS WEEK
  - Each role checks in with a heartbeat as it runs. Your dashboard
    rings turn green on first run.
  - If anything errors, the dashboard surfaces it before we do. You
    will not have to chase status.
  - Review drafts, blog posts, and replies sit in your dashboard for
    one click of approval.

IF SOMETHING LOOKS OFF
Reply to this email or text Sam directly at (562) 968-4474.

Sam Alarcon
WestCoast Automation Solutions
"""

    return subject, html_body, text_body


def send_handoff(
    *,
    tenant_id: str,
    owner_name: str,
    owner_email: str,
    business_name: str | None = None,
    now: datetime | None = None,
    sender: Any | None = None,
) -> bool:
    """Email the handoff letter to the owner.

    Returns True on send, False on missing email or send failure (logged).
    `sender` defaults to email_sender.send_html; tests inject a fake.
    """
    if not owner_email or "@" not in owner_email:
        log.warning("handoff: owner_email missing for tenant %s", tenant_id)
        return False

    subject, html_body, text_body = render(
        tenant_id=tenant_id,
        owner_name=owner_name,
        business_name=business_name,
        now=now,
    )
    send_fn = sender if sender is not None else email_sender.send_html
    try:
        send_fn(
            to_email=owner_email,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
            channel="support",
        )
        return True
    except email_sender.EmailSendError as exc:
        log.warning("handoff: send failed tenant=%s: %s", tenant_id, exc)
        return False


__all__ = ["render", "send_handoff"]
