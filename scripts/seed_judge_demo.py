"""
Seed the judge_demo tenant: a fully populated dashboard a hackathon judge
can land on and explore without doing any onboarding.

Tenant: riverbend_barbershop (display "Riverbend Barbershop")
Owner:  Tony Reyes
Roster: the 7 WCAS automations (gbp, seo, reviews, email_assistant,
        chat_widget, blog, social), all activated, all running.
Period: roughly the last 6 months of activity (receipts, recs, drafts).

Idempotent: re-running wipes the tenant directory first, then rebuilds.

Usage:
    TENANT_ROOT=/opt/wc-solns python scripts/seed_judge_demo.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dashboard_app.services import (
    activation_state,
    goals,
    heartbeat_store,
    outgoing_queue,
    receipts,
    recs_store,
    roster,
)


TENANT_ID = "riverbend_barbershop"
NOW = datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _wipe_tenant() -> None:
    root = heartbeat_store.tenant_root(TENANT_ID)
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)


def _seed_activation_complete() -> None:
    slugs = roster.role_slugs()
    activation_state.bulk_advance(TENANT_ID, slugs, "first_run")
    activation_state.mark_complete(
        TENANT_ID,
        note="Seeded judge demo tenant. All 7 roles fully activated.",
    )


def _seed_heartbeats() -> None:
    """One snapshot per role. run_count compounds over 6 months so hero
    stats compute a meaningful Weeks Saved number."""
    plan = [
        ("gbp",             180,  90,  "Posted weekly update + replied to 2 questions"),
        ("seo",              26, 240,  "Weekly SERP scan: 4 movers, 1 new keyword opportunity"),
        ("reviews",         210,  45,  "Replied to 1 new 5-star review on Google"),
        ("email_assistant", 412,  20,  "Drafted 3 client replies; 2 auto-sent, 1 queued"),
        ("chat_widget",     308,  10,  "Handled 4 widget conversations; 2 booked appointments"),
        ("blog",             24, 600,  "Drafted next post: 'How often should you really get a fade?'"),
        ("social",          180, 120,  "Scheduled this week's reels + carousel"),
    ]
    for slug, run_count, minutes_ago, last_action in plan:
        last_run = NOW - timedelta(minutes=minutes_ago)
        payload = {
            "status": "ok",
            "run_count": run_count,
            "last_run": _iso(last_run),
            "last_action": last_action,
            "errors_24h": 0,
        }
        heartbeat_store.write_snapshot(TENANT_ID, slug, payload)


# Receipts span Oct 2025 -> April 2026, weighted toward the recent month so
# the drawer is dense at the top. (subject, body, channel, recipient_hint).
_REVIEW_REPLIES = [
    ("Google review by Marisol R.", "5-star review reply",
     "Thanks for the love, Marisol. We'll let Eduardo know you noticed the new straight-razor finish. See you in three weeks."),
    ("Google review by Kevin T.", "5-star review reply",
     "Glad the kid liked the chair, Kevin. We keep the booster ready for first-timers; bring him back anytime."),
    ("Google review by Anna B.", "4-star review reply",
     "Appreciate the honest note, Anna. You're right that the wait went long Saturday. Booking a slot online skips the queue every time."),
    ("Google review by Daniel P.", "5-star review reply",
     "Means a lot, Daniel. The skin-fade with the scissor work on top has become Marco's favorite to do. Pass it on if you can."),
    ("Google review by Lisa H.", "5-star review reply",
     "Thanks Lisa. Rico will smile when he sees this. Tell your husband the bourbon is still on the house for repeat visits."),
]

_EMAIL_REPLIES = [
    ("nora.k@gmail.com", "Re: Reschedule for Saturday",
     "Hi Nora,\n\nNo problem at all. I moved you to Saturday at 11:15 with Rico. You'll get a confirmation text 2 hours before. See you then.\n\nTony"),
    ("james.beltran@hotmail.com", "Re: Beard trim availability",
     "Hi James,\n\nWe have an opening this Thursday at 4:30 with Eduardo, and a couple Friday morning slots. Reply with what works and I'll lock it in.\n\nTony"),
    ("maria.l@yahoo.com", "Re: First-time visit - shop policy",
     "Hi Maria,\n\nFirst cut is 20% off and includes a hot-towel finish. We accept walk-ins but Saturdays book up fast - the online link is on our site if you want to lock a time.\n\nLooking forward to meeting you,\nTony"),
    ("derek.santos@me.com", "Re: Bachelor party group cuts",
     "Hi Derek,\n\nGroup of 6 on a Saturday morning is doable if we open the back chair. Block is $360 flat (six cuts + drinks). I can hold March 8 at 9:00am - need a 50% deposit by Wednesday.\n\nTony"),
    ("ivan.tran@gmail.com", "Re: Did I leave my AirPods?",
     "Hi Ivan,\n\nYep, found them under chair 2 right after you left. They're in the front drawer with your name on them. Anytime this week."),
]

_BLOG_RECEIPTS = [
    ("substack/riverbend", "Published: Why Your Fade Looks Different at Home",
     "Posted to substack + cross-published to Instagram. 1,240 word essay on lighting, growth direction, and why a fresh cut never holds up to a bathroom mirror. Internal link to booking page."),
    ("substack/riverbend", "Published: A Field Guide to Beard Oils Under $25",
     "Posted Sunday morning. Comparison table of 8 brands we've tried at the shop. Affiliate disclaimer in the footer. Three customers reposted it within 24 hours."),
    ("substack/riverbend", "Published: Ten Years on Main Street",
     "Anniversary post. Founded 2016, three barbers, 14,000+ cuts to date. Quotes from Eduardo and Rico. Got picked up by the neighborhood Facebook group, +43 new email subscribers in 48 hours."),
]

_SOCIAL_RECEIPTS = [
    ("instagram", "Reel: 30-second pompadour transformation",
     "Posted Tuesday 6pm. Pre-cut shot, 3 cut intervals, finished style. Audio: 'Linger' by The Cranberries. Caption ties to booking link in bio. 11K views in first day."),
    ("instagram", "Carousel: Before/after gallery, March cuts",
     "10-slide carousel of the month's best work. All client-approved before posting. Tagged each barber. Saved by 184 accounts."),
    ("instagram", "Story highlight: Tools we actually use",
     "Wahl Magic Clip, Andis Pro Foil, the Babyliss FX trimmer. Plain caption, no affiliate spam. Replies came in asking where we buy them; saved replies queued for the email assistant."),
    ("facebook", "Post: We're closed Memorial Day",
     "Short closure notice with a photo of the team grilling out front last year. Comment section turned into customer banter. We replied to 7 questions about reopening."),
]

_CHAT_RECEIPTS = [
    ("visitor 4f81 - mobile", "Chat reply: Walk-in availability today?",
     "Hey! We've got a slot at 2:15 with Marco, and another at 4:30 with Eduardo. I can hold one for you - what's your name and number?"),
    ("visitor a14d - desktop", "Chat reply: Pricing for kids' cuts",
     "Kids 12 and under are $22 (adults are $35-45 depending on style + barber). First-time families get the kid's cut free with a paid adult cut. Want me to text you Saturday's open slots?"),
    ("visitor 9c22 - mobile", "Chat reply: Do you do straight razor shaves?",
     "Yes - Eduardo and Rico both do them, full hot-towel treatment with witch hazel finish. $45 for the shave, $65 if you want it bundled with a cut. Bookable on the site or just text the shop."),
    ("visitor f10a - mobile", "Chat reply: Ear waxing??",
     "Ha, yes - $12, takes 90 seconds, no appointment needed. Pop in any time we're open."),
    ("visitor 7e29 - mobile", "Chat reply: Did the prices go up",
     "Cuts went from $35 to $38 in February (first bump in 4 years - just keeping up with the lease). Beard trims and straight razors held flat. The loyalty card still saves you a free cut every 10 visits."),
]

_GBP_RECEIPTS = [
    ("Google Business profile", "Posted: Spring hours update",
     "Open 9-7 Tue-Fri, 8-6 Sat, closed Sun-Mon. Posted with three new shop photos and a CTA to book online. Got 84 profile views in the first 6 hours."),
    ("Google Business profile", "Q&A reply: Do you take walk-ins?",
     "We do, though Saturdays after 11am are usually a 30-45 minute wait. Online booking opens 14 days out and is the most reliable way to skip the line."),
    ("Google Business profile", "Posted: Eduardo's anniversary",
     "8 years at the shop this week. Posted a portrait shot + a clip from him talking about why he picked barbering. Cross-shared to IG."),
]

_SEO_RECEIPTS = [
    ("internal report", "Weekly SEO: ranking gains for 'barbershop near me' + 3 service queries",
     "We moved from #6 to #4 for 'barbershop near me' in the Riverbend zip. 'Hot towel shave' jumped #14 -> #7. Two posts indexed this week. Recommend adding a 'beard trim' page; we have keyword volume but no dedicated landing."),
    ("internal report", "Weekly SEO: Core Web Vitals all green",
     "LCP 1.2s, CLS 0.01, INP 84ms. Image lazy-loading on the gallery cleared the last yellow flag. No issues to address."),
    ("internal report", "Weekly SEO: backlink check + competitor pulse",
     "Picked up 2 new backlinks (neighborhood blog + a local podcast directory). Two competitors lost rankings on 'kids haircut'; window to publish a kids-cut guide is open."),
]


def _scattered_ts(start: datetime, end: datetime, idx: int, total: int) -> str:
    """Spread idx evenly across [start, end] with a hash-stable jitter."""
    span = (end - start).total_seconds()
    pos = idx / max(total - 1, 1)
    seconds = pos * span
    return _iso(start + timedelta(seconds=seconds))


def _seed_receipts() -> None:
    """Receipts spread Oct 2025 - now, weighted toward recent."""
    six_months_ago = NOW - timedelta(days=183)
    one_month_ago = NOW - timedelta(days=30)

    # Recent month: dense, every role.
    recent_groups = [
        ("reviews", "reply", _REVIEW_REPLIES),
        ("email_assistant", "email", _EMAIL_REPLIES),
        ("chat_widget", "chat", _CHAT_RECEIPTS),
        ("gbp", "post", _GBP_RECEIPTS),
        ("social", "post", _SOCIAL_RECEIPTS),
        ("blog", "post", _BLOG_RECEIPTS),
        ("seo", "report", _SEO_RECEIPTS),
    ]
    for pipeline_id, channel, items in recent_groups:
        for i, (recipient_hint, subject, body) in enumerate(items):
            ts = _scattered_ts(one_month_ago, NOW, i, len(items))
            receipts.append(
                tenant_id=TENANT_ID,
                pipeline_id=pipeline_id,
                channel=channel,
                recipient_hint=recipient_hint,
                subject=subject,
                body=body,
                cost_usd=0.0003,
                ts=ts,
            )

    # Older history: lighter spread across 5 prior months. Reuses the same
    # body library; the older ts values give the timeline depth without
    # forcing 200+ unique copy lines.
    history_groups = [
        ("reviews", "reply", _REVIEW_REPLIES[:3]),
        ("email_assistant", "email", _EMAIL_REPLIES[:3]),
        ("chat_widget", "chat", _CHAT_RECEIPTS[:2]),
        ("social", "post", _SOCIAL_RECEIPTS[:2]),
    ]
    for pipeline_id, channel, items in history_groups:
        for i, (recipient_hint, subject, body) in enumerate(items):
            ts = _scattered_ts(six_months_ago, one_month_ago, i, len(items))
            receipts.append(
                tenant_id=TENANT_ID,
                pipeline_id=pipeline_id,
                channel=channel,
                recipient_hint=recipient_hint,
                subject=subject,
                body=body,
                cost_usd=0.0002,
                ts=ts,
            )


_DRAFTS = [
    ("email_assistant", "email", "carlos.menendez@gmail.com",
     "Re: Father-son cuts before vacation",
     "Hi Carlos,\n\nWe can fit both of you in Friday at 5:30. Marco for you and Rico for Mateo - he's great with kids and used to do family cuts at his last shop.\n\nWant me to lock that in?\n\nTony"),
    ("reviews", "reply", "Google review by Sarah W. - 3 stars",
     "3-star review reply",
     "Hi Sarah,\n\nAppreciate the honest read. You're right that the chair felt rushed last visit; I'd had a no-show that morning and I think I tried to make up the time on the next cut. That's on me. Next visit is on the house if you'll give us another shot."),
    ("email_assistant", "email", "promotions@local-chamber.org",
     "Re: Sponsorship for the spring 5K",
     "Hi Janet,\n\nHappy to sponsor at the bronze tier ($250) again this year. The shop will host a free water + razor-emergency-kit station at mile 2 if that helps. Need anything from us by what date?\n\nTony"),
    ("chat_widget", "chat", "visitor c83b - mobile",
     "Chat reply: Group rate question",
     "Yep, groups of 4+ get 10% off if booked together. Fridays after 5pm have the most flexibility on the back chair. Want me to text you a quote?"),
    ("social", "post", "instagram",
     "Caption draft: National Barber's Day post",
     "Caption draft for Sept 1: 'Eduardo's been cutting hair since before some of you were born. Rico learned in his uncle's shop in Oaxaca. Marco started here as a sweep-up kid in 2018. Three barbers, one chair each, and a story behind every fade. Happy Barber's Day from the Riverbend crew.' Carousel: 3 portraits + a wide shop shot."),
]


def _seed_drafts() -> None:
    """5 pending drafts in the approvals queue, spanning urgency."""
    for pipeline_id, channel, recipient, subject, body in _DRAFTS:
        outgoing_queue.enqueue(
            tenant_id=TENANT_ID,
            pipeline_id=pipeline_id,
            channel=channel,
            recipient_hint=recipient,
            subject=subject,
            body=body,
        )


_RECS = [
    {
        "id": "rec_review_tone",
        "goal": "GROW REVIEWS",
        "headline": "Your last 3 review replies all start with \"Thanks\". Mix in a customer-specific opener instead.",
        "reason": (
            "Review replies that name the barber or the specific service get 2-3x more replies "
            "and reads (per the last 60 days of profile analytics). The current template is fine; "
            "varying the first line makes the shop feel less canned to a Google searcher comparing "
            "options."
        ),
        "draft": False,
    },
    {
        "id": "rec_chat_handoff",
        "goal": "BOOK MORE",
        "headline": "Chat conversations that ask 'what's the wait?' aren't being handed off to the booking link.",
        "reason": (
            "12 of 28 widget conversations in the last 30 days asked about wait times. Only 4 "
            "ended with a booked slot. The chat assistant is answering accurately but not closing. "
            "Recommend updating its system prompt to always offer the 'reserve a chair' link when "
            "a wait-time question comes in."
        ),
        "draft": False,
    },
    {
        "id": "rec_blog_kids",
        "goal": "RANK HIGHER",
        "headline": "You rank for 'kids haircut Riverbend' on page 2 with no dedicated page.",
        "reason": (
            "Two competitors lost ground on this query last week. Publishing a single 800-word page "
            "on kids' cuts - the booster chair, the loyalty card, parent-in-the-room policy - would "
            "likely move you to page 1 within 4 weeks. The blog assistant has a draft outline ready."
        ),
        "draft": False,
    },
]


def _seed_recs() -> None:
    recs_store.write_today(
        TENANT_ID,
        recs=_RECS,
        model="claude-opus-4-7",
        usd=0.041,
        input_tokens=18_240,
        output_tokens=614,
    )


def _seed_goals() -> None:
    goals.add(
        TENANT_ID,
        title="Hit 200 5-star Google reviews by July",
        metric="reviews",
        target=200,
        timeframe="90d",
    )


def main() -> int:
    print(f"seeding tenant: {TENANT_ID}")
    _wipe_tenant()
    _seed_activation_complete()
    _seed_heartbeats()
    _seed_receipts()
    _seed_drafts()
    _seed_recs()
    _seed_goals()
    print(f"  activation: complete")
    print(f"  heartbeats: 7 roles (all ok)")
    print(f"  receipts:   ~{(5+5+5+3+4+3+3) + (3+3+2+2)} across 6 months")
    print(f"  drafts:     {len(_DRAFTS)} pending in /approvals")
    print(f"  recs:       {len(_RECS)} in today's file")
    print(f"  goals:      1 pinned")
    print("done. Tenant root:", heartbeat_store.tenant_root(TENANT_ID))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
