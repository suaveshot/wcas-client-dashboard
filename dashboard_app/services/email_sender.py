"""
SMTP sender for transactional emails (magic links, Ask Sam notifications).

Uses Gmail SMTP with an App Password rather than the OAuth flow in
Americal Patrol's email assistant - these one-shot sends don't need
a refreshable credential, and the dashboard lives in a Docker
container with no browser to re-auth.

Set in .env:
    SUPPORT_EMAIL_FROM   = americalpatrol@gmail.com
    GMAIL_APP_PASSWORD   = 16-char Gmail app password

Templates are Jinja2 HTML + plain-text twins. MIME multipart/alternative
so clients that strip HTML still get a usable body.
"""

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

log = logging.getLogger("dashboard.email")


class EmailSendError(RuntimeError):
    pass


def send_html(to_email: str, subject: str, html_body: str, text_body: str) -> None:
    """Send a multipart/alternative email. Raises EmailSendError on failure."""
    sender = os.getenv("SUPPORT_EMAIL_FROM", "")
    password = os.getenv("GMAIL_APP_PASSWORD", "")
    if not sender or not password:
        raise EmailSendError("SUPPORT_EMAIL_FROM or GMAIL_APP_PASSWORD missing")

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
        log.exception("email send failed to=%s subject=%r", to_email, subject)
        raise EmailSendError(str(exc)) from exc
