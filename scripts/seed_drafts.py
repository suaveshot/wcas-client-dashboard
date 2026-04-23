"""
Seed demo drafts for a tenant's approval queue.

Usage:
    TENANT_ROOT=/opt/wc-solns python scripts/seed_drafts.py <tenant_id>

Writes 5-7 realistic pending drafts with staggered created_at so the
urgency dots span green / amber / red.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone

from dashboard_app.services import heartbeat_store, outgoing_queue


DRAFTS = [
    {
        "pipeline_id": "sales_pipeline",
        "channel": "email",
        "recipient_hint": "jane.doe@acmehvac.com",
        "subject": "Follow-up on our Oxnard quote",
        "body": (
            "Hi Jane,\n\nWanted to circle back on the security coverage "
            "proposal we sent last week. I'm in Oxnard this Thursday "
            "and happy to stop by for 15 minutes if that helps you "
            "compare against the other bid.\n\nNo pressure - just don't "
            "want it to fall off your plate.\n\nSam"
        ),
        "age_hours": 0.5,
    },
    {
        "pipeline_id": "sales_pipeline",
        "channel": "email",
        "recipient_hint": "mike@portoftomorrow.net",
        "subject": "Port security coverage - draft two",
        "body": (
            "Hi Mike,\n\nHere's a revised draft with the night-shift "
            "coverage you asked for. Two officers on-site from 8pm to "
            "6am, plus mobile patrol through the dock gate every "
            "90 minutes. Keeping the armed coverage optional until we "
            "get a better read on incident trends.\n\nWant to walk "
            "through this on Friday?\n\nSam"
        ),
        "age_hours": 3.0,
    },
    {
        "pipeline_id": "reviews",
        "channel": "reply",
        "recipient_hint": "Google review by Christine M.",
        "subject": "3-star review reply",
        "body": (
            "Hi Christine,\n\nThanks for the honest feedback. You're "
            "right that the patrol schedule got shifted on the 15th "
            "without a heads-up - that's on us, and I've already "
            "asked my dispatch team to text the PM every time the "
            "rotation changes mid-week.\n\nIf there's anything else "
            "bothering you, please text me directly at (805) 555-"
            "0134 and I'll fix it.\n\nSam"
        ),
        "age_hours": 5.0,
    },
    {
        "pipeline_id": "social",
        "channel": "post",
        "recipient_hint": "Facebook page",
        "subject": "Weekly safety tip",
        "body": (
            "Friendly reminder for property managers: daylight savings "
            "weekend is a high-opportunity window for package theft. "
            "If you've got a property without a locked front lobby, "
            "ask your on-site officer to patrol the mailbox area every "
            "hour from 3pm to 7pm through Monday. Small thing; cuts "
            "incidents roughly in half based on last year's logs."
        ),
        "age_hours": 9.0,
    },
    {
        "pipeline_id": "chat_widget",
        "channel": "chat",
        "recipient_hint": "visitor e841",
        "subject": "New inquiry - Camarillo retail center",
        "body": (
            "Thanks for reaching out. Yes, we cover Camarillo retail - "
            "we have officers at a similar-sized center in Ventura, "
            "so we can talk specifics. A 10-minute walkthrough with "
            "our supervisor is usually the fastest path to a real "
            "quote. Here's my booking link: "
            "https://cal.com/americalpatrol/intro."
        ),
        "age_hours": 13.5,
    },
    {
        "pipeline_id": "blog",
        "channel": "post",
        "recipient_hint": "americalpatrol.com/blog",
        "subject": "Post draft: 5 patrol myths debunked",
        "body": (
            "Draft post:\n\nTitle: Five things property managers "
            "still believe about private patrol that aren't true\n\n"
            "1. More lights mean fewer incidents - not always.\n"
            "2. Armed coverage is always better - not for most "
            "retail sites.\n"
            "3. Cameras replace a human patrol - they catch incidents, "
            "they don't prevent them.\n"
            "4. Private security is expensive - compared to what?\n"
            "5. All patrol companies are the same - the logbook "
            "tells the real story.\n\n[Expand each section]"
        ),
        "age_hours": 18.0,
    },
]


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python scripts/seed_drafts.py <tenant_id>", file=sys.stderr)
        return 2
    tenant_id = sys.argv[1]

    # Prime the pending.jsonl with staggered created_at timestamps.
    # Normal enqueue() uses "now"; for a visible urgency spread, we write
    # directly with backdated timestamps.
    try:
        root = heartbeat_store.tenant_root(tenant_id) / "outgoing"
    except heartbeat_store.HeartbeatError:
        print(f"invalid tenant_id: {tenant_id}", file=sys.stderr)
        return 2
    root.mkdir(parents=True, exist_ok=True)
    pending = root / "pending.jsonl"

    existing = []
    if pending.exists():
        for line in pending.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    existing.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    now = datetime.now(timezone.utc)
    for d in DRAFTS:
        entry = {
            "id": f"draft-{d['pipeline_id']}-{now.strftime('%Y%m%dT%H%M%S')}-{abs(hash(d['subject'])) % 0xffffff:06x}",
            "created_at": (now - timedelta(hours=d["age_hours"])).isoformat(),
            "pipeline_id": d["pipeline_id"],
            "channel": d["channel"],
            "recipient_hint": d["recipient_hint"],
            "subject": d["subject"],
            "body": d["body"],
            "status": "pending",
            "guardrail_reasons": [],
        }
        existing.append(entry)
        print(f"seeded draft {entry['id']}")

    pending.write_text(
        "".join(json.dumps(r) + "\n" for r in existing),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
