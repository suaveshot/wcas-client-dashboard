"""
SMTP sender for transactional emails (magic links, Ask Sam notifications).

Uses Gmail SMTP with an App Password rather than the OAuth flow in
Americal Patrol's email assistant - these one-shot sends don't need
a refreshable credential, and the dashboard lives in a Docker
container with no browser to re-auth.

Two channels with separate credentials so client-facing magic links
ride a high-reputation mailbox while internal Sam alerts can come from
the WCAS-branded mailbox even before it's earned reputation:

    Channel "magic_link" (client-facing sign-in links)
        MAGIC_LINK_EMAIL_FROM        = americalpatrol@gmail.com
        MAGIC_LINK_GMAIL_APP_PASSWORD

    Channel "support" (default; alert_sam, Ask Sam notifications)
        SUPPORT_EMAIL_FROM           = westcoastautomationsolutions@gmail.com
        GMAIL_APP_PASSWORD

    SUPPORT_EMAIL_TO = sam@westcoastautomationsolutions.com (alert recipient)

Gmail SMTP authenticates as the FROM address and rewrites the From
header to that account, so each channel's password must belong to its
own mailbox. If MAGIC_LINK_* vars are unset the magic-link channel
falls back to the support credentials.

Templates are Jinja2 HTML + plain-text twins. MIME multipart/alternative
so clients that strip HTML still get a usable body.
"""

import logging
import os
import smtplib
import threading
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

log = logging.getLogger("dashboard.email")


class EmailSendError(RuntimeError):
    pass


def _credentials(channel: str) -> tuple[str, str]:
    """Resolve (sender, password) for a channel; magic_link falls back to support."""
    if channel == "magic_link":
        sender = os.getenv("MAGIC_LINK_EMAIL_FROM") or os.getenv("SUPPORT_EMAIL_FROM", "")
        password = os.getenv("MAGIC_LINK_GMAIL_APP_PASSWORD") or os.getenv("GMAIL_APP_PASSWORD", "")
        return sender, password
    return os.getenv("SUPPORT_EMAIL_FROM", ""), os.getenv("GMAIL_APP_PASSWORD", "")


def send_html(
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str,
    *,
    channel: str = "support",
) -> None:
    """Send a multipart/alternative email. Raises EmailSendError on failure.

    `channel="magic_link"` routes via the high-reputation client-facing
    mailbox. Default `"support"` routes via the internal alert mailbox.
    """
    sender, password = _credentials(channel)
    if not sender or not password:
        raise EmailSendError(f"sender or password missing for channel={channel!r}")

    msg = MIMEMultipart("alternative")
    msg["From"] = sender
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as smtp:
            smtp.login(sender, password)
            smtp.sendmail(sender, [to_email], msg.as_string())
    except (smtplib.SMTPException, OSError) as exc:
        log.exception("email send failed to=%s subject=%r channel=%s", to_email, subject, channel)
        raise EmailSendError(str(exc)) from exc


# ---------------------------------------------------------------------------
# Sam alerting - per-event-type per-tenant dedupe (5 min window)
# ---------------------------------------------------------------------------

_SAM_ALERT_WINDOW_SECONDS = 300
_sam_alert_last_sent: dict[tuple[str, str], float] = {}
_sam_alert_lock = threading.Lock()


def alert_sam(
    *,
    tenant_id: str,
    event_type: str,
    subject: str,
    body: str,
    force: bool = False,
) -> bool:
    """Send Sam a short notification email about a tenant activation event.

    Dedupes per (tenant_id, event_type) within a 5-minute window so a runaway
    tool loop can't pump the inbox. `force=True` skips dedupe (used for tests
    + the "complete" event which should always get through).

    Returns True when an email was actually sent, False when deduped or when
    SUPPORT_EMAIL_TO is unset.
    """
    recipient = (os.getenv("SUPPORT_EMAIL_TO") or "").strip()
    if not recipient:
        return False

    key = (tenant_id, event_type)
    now = time.monotonic()
    if not force:
        with _sam_alert_lock:
            last = _sam_alert_last_sent.get(key)
            if last is not None and (now - last) < _SAM_ALERT_WINDOW_SECONDS:
                return False
            _sam_alert_last_sent[key] = now
    else:
        with _sam_alert_lock:
            _sam_alert_last_sent[key] = now

    # Short, plain-text body; HTML is the same body wrapped in <pre>.
    text_body = body if body.endswith("\n") else body + "\n"
    html_body = (
        f"<html><body style=\"font-family:'DM Sans',system-ui,sans-serif;color:#0F2A44;\">"
        f"<pre style=\"white-space:pre-wrap;background:#F4EFE6;padding:16px 20px;"
        f"border-radius:8px;font-family:ui-monospace,Menlo,Consolas,monospace;\">"
        f"{body}</pre></body></html>"
    )
    try:
        send_html(
            to_email=recipient,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
        )
        return True
    except EmailSendError as exc:
        log.warning("alert_sam send failed tenant=%s event=%s: %s", tenant_id, event_type, exc)
        return False


def _reset_sam_alert_dedupe_for_tests() -> None:
    """Test helper - drop the dedupe map so consecutive tests don't interfere."""
    with _sam_alert_lock:
        _sam_alert_last_sent.clear()
