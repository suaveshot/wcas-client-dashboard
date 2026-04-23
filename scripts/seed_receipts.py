"""
Seed demo receipts for a tenant.

Usage:
    TENANT_ROOT=/opt/wc-solns python scripts/seed_receipts.py <tenant_id>

Writes 12 realistic receipts across 4 AP pipelines (patrol, sales_pipeline,
reviews, chat_widget) so the receipts drawer has content immediately for
demo. Run once per tenant after activation; subsequent real runs will
append on top of these.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

from dashboard_app.services import receipts


DEMO_RECEIPTS = [
    {
        "pipeline_id": "patrol",
        "channel": "email",
        "recipient_hint": "pm-harborlights@example.com",
        "subject": "Daily Activity Report - Harbor Lights, 2026-04-22",
        "body": (
            "Hi team,\n\nOvernight patrol completed at Harbor Lights. "
            "Three standard perimeter passes, two vehicle checks, and "
            "one gate inspection - all clear. No incidents logged.\n\n"
            "Next patrol: tonight at 10:00 PM.\n\nAmerical Patrol"
        ),
        "cost_usd": 0.0,
    },
    {
        "pipeline_id": "patrol",
        "channel": "email",
        "recipient_hint": "pm-manhattan@example.com",
        "subject": "Daily Activity Report - Manhattan Plaza, 2026-04-22",
        "body": (
            "Good morning,\n\nManhattan Plaza patrol complete. All doors "
            "secure, lobby cameras functioning, two suspicious activity "
            "logs filed for the north entrance. Attachment has the full "
            "timeline.\n\nAmerical Patrol"
        ),
        "cost_usd": 0.0,
    },
    {
        "pipeline_id": "sales_pipeline",
        "channel": "email",
        "recipient_hint": "jane.doe@acmehvac.com",
        "subject": "Quick follow-up on the Oxnard office security quote",
        "body": (
            "Hi Jane,\n\nChecking in on the security patrol quote we sent "
            "last week. Happy to jump on a 15-min call this week if that "
            "helps - or I can answer quick questions over email.\n\n"
            "No pressure either way, just wanted to make sure it didn't "
            "get lost in your inbox.\n\nBest,\nSam"
        ),
        "cost_usd": 0.0003,
    },
    {
        "pipeline_id": "sales_pipeline",
        "channel": "email",
        "recipient_hint": "mike@portoftomorrow.net",
        "subject": "Re: Port security coverage",
        "body": (
            "Hi Mike,\n\nThanks for sending the site map. Our nearest "
            "patrol team runs a 3-minute response from the gate you "
            "marked. I'll ping you Wednesday with draft coverage "
            "options.\n\nSam"
        ),
        "cost_usd": 0.0003,
    },
    {
        "pipeline_id": "reviews",
        "channel": "reply",
        "recipient_hint": "Google review by Maria G.",
        "subject": "5-star review reply",
        "body": (
            "Thank you so much, Maria. Our team loves hearing this. "
            "We'll pass the note along to Carlos and Javier - they "
            "genuinely take pride in the overnight rounds. Grateful "
            "you took the time to share."
        ),
        "cost_usd": 0.0002,
    },
    {
        "pipeline_id": "reviews",
        "channel": "reply",
        "recipient_hint": "Google review by David K.",
        "subject": "5-star review reply",
        "body": (
            "Thanks, David. Appreciate you taking a moment. If there's "
            "ever anything we can do better on the patrol schedule, "
            "just text or call the line - we pick up 24/7."
        ),
        "cost_usd": 0.0002,
    },
    {
        "pipeline_id": "chat_widget",
        "channel": "chat",
        "recipient_hint": "visitor 7f2a",
        "subject": "New inquiry - Ventura warehouse",
        "body": (
            "Thanks for reaching out. Yes, we cover Ventura - we patrol "
            "three properties in the Montalvo area already. The quickest "
            "way to get a quote is a 10-minute walkthrough with one of "
            "our supervisors. Here's my booking link: "
            "https://cal.com/americalpatrol/intro."
        ),
        "cost_usd": 0.0002,
    },
    {
        "pipeline_id": "chat_widget",
        "channel": "chat",
        "recipient_hint": "visitor a91c",
        "subject": "Pricing question",
        "body": (
            "Pricing depends on frequency, property size, and whether "
            "you need armed or unarmed coverage. The best fit comes from "
            "a quick walkthrough; I can put you on Sam's schedule this "
            "week. Would Wednesday afternoon work?"
        ),
        "cost_usd": 0.0002,
    },
]


def _staggered_ts(i: int) -> str:
    base = datetime.now(timezone.utc) - timedelta(hours=i * 3)
    return base.isoformat()


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python scripts/seed_receipts.py <tenant_id>", file=sys.stderr)
        return 2
    tenant_id = sys.argv[1]
    for i, r in enumerate(DEMO_RECEIPTS):
        rid = receipts.append(
            tenant_id=tenant_id,
            pipeline_id=r["pipeline_id"],
            channel=r["channel"],
            recipient_hint=r["recipient_hint"],
            subject=r["subject"],
            body=r["body"],
            cost_usd=r.get("cost_usd", 0.0),
            guardrail_result="approve",
            ts=_staggered_ts(i),
        )
        print(f"seeded {r['pipeline_id']} {rid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
