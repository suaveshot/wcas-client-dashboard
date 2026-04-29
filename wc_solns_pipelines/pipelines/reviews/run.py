"""Generic per-tenant Reviews pipeline.

Run via:

    python -m wc_solns_pipelines.pipelines.reviews.run --tenant <tenant_id>

What it does on each run, in order:
  1. Build a TenantContext for the tenant. Bail with status=error heartbeat
     if the slug is invalid.
  2. If the tenant is paused (tenant_config.json:status == "paused"), push
     a success heartbeat with summary="paused" and exit 0.
  3. Resolve Google credentials + business.manage scope. Missing creds or
     scope -> error heartbeat, exit 0.
  4. Discover the tenant's first GBP account + first location via the
     management APIs. Single-location is the W3.x assumption; the
     location-picker UI ships with W4.
  5. Fetch the location's reviews (v4 legacy endpoint - the only one that
     exposes review text + reply hooks).
  6. Filter out reviews already drafted (by reviewId) - state lives in
     pipeline_state/reviews.json.
  7. For each new review with text, draft a reply via services.opus.chat
     grounded in the tenant's `voice` + `company` KB sections, then call
     dispatch.send. Approval-gated tenants get a queued draft; auto-send
     tenants will hit the OUTGOING_HANDLERS["reviews"] handler (which
     today is DISPATCH_DRY_RUN-gated; the real GBP reply call ships W4).
  8. Persist updated seen_review_ids (capped at 500) + last_check.
  9. Push the heartbeat with summary + an `events` array containing one
     {"kind":"review.posted","stars":N,...} entry per 5-star review so
     the dashboard's "five-star reviews" goal auto-bumps via
     dispatch.handle_heartbeat_events.

Exit code is always 0. Pipeline failures surface via the heartbeat's
status field, never via process exit, so cron entries don't get tagged
red and so AP-style watchdogs only flag truly broken runs.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dashboard_app.services import dispatch
from dashboard_app.services.opus import OpusBudgetExceeded, OpusUnavailable, chat
from wc_solns_pipelines.shared.push_heartbeat import push as push_heartbeat
from wc_solns_pipelines.shared.tenant_runtime import TenantContext, TenantNotFound

PIPELINE_ID = "reviews"
GBP_SCOPE = "https://www.googleapis.com/auth/business.manage"
SEEN_IDS_CAP = 500
DEFAULT_MAX_REVIEWS = 20
DEFAULT_TIMEOUT = 10.0

# Map GBP star strings to ints. UNSPECIFIED -> 0 (treated as "no rating").
_STAR_TO_INT: dict[str, int] = {
    "STAR_RATING_UNSPECIFIED": 0,
    "ONE": 1,
    "TWO": 2,
    "THREE": 3,
    "FOUR": 4,
    "FIVE": 5,
}

log = logging.getLogger("wcas.pipelines.reviews")


# ---------------------------------------------------------------------------
# GBP API
# ---------------------------------------------------------------------------


def _http_get_json(url: str, access_token: str, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    req = Request(
        url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
    )
    with urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return json.loads(body) if body else {}


def discover_location(access_token: str) -> tuple[str, str]:
    """Return (account_path, location_path) for the tenant's first GBP location.

    account_path looks like 'accounts/1234567890'.
    location_path looks like 'locations/5555'.

    Raises RuntimeError if no account or no location is reachable. The caller
    converts that into an error heartbeat.
    """
    acc = _http_get_json(
        "https://mybusinessaccountmanagement.googleapis.com/v1/accounts",
        access_token,
    )
    accounts = acc.get("accounts") or []
    if not accounts:
        raise RuntimeError("No GBP accounts visible to this credential")
    account_path = accounts[0].get("name", "")
    if not account_path:
        raise RuntimeError("First GBP account missing 'name' field")

    loc = _http_get_json(
        f"https://mybusinessbusinessinformation.googleapis.com/v1/{account_path}/locations"
        f"?readMask=name,title&pageSize=100",
        access_token,
    )
    locations = loc.get("locations") or []
    if not locations:
        raise RuntimeError(f"GBP account {account_path} has no locations")
    location_path = locations[0].get("name", "")
    if not location_path:
        raise RuntimeError("First GBP location missing 'name' field")

    return account_path, location_path


def fetch_reviews(
    access_token: str,
    account_path: str,
    location_path: str,
    *,
    page_size: int = 50,
) -> list[dict[str, Any]]:
    """Fetch latest reviews for the given GBP location. Returns the raw
    'reviews' array from the v4 response, newest-first. Returns [] on any
    network/parse error so the pipeline keeps moving."""
    url = (
        f"https://mybusiness.googleapis.com/v4/{account_path}/{location_path}/reviews"
        f"?pageSize={page_size}&orderBy=updateTime desc"
    )
    try:
        body = _http_get_json(url, access_token)
    except (HTTPError, URLError, OSError, json.JSONDecodeError) as exc:
        log.warning("GBP reviews fetch failed: %s", exc)
        return []
    reviews = body.get("reviews") or []
    return reviews if isinstance(reviews, list) else []


# ---------------------------------------------------------------------------
# draft generation
# ---------------------------------------------------------------------------


def _build_voice_system(ctx: TenantContext) -> str:
    """Stitch the tenant's voice + company KB sections + voice card into a
    single system prompt for the drafter."""
    parts: list[str] = []
    voice_kb = ctx.kb("voice")
    if voice_kb:
        parts.append("Voice (how this business sounds):\n" + voice_kb.strip())
    company_kb = ctx.kb("company")
    if company_kb:
        parts.append("Company context:\n" + company_kb.strip())
    voice_card = ctx.voice_card()
    if isinstance(voice_card, dict) and voice_card:
        parts.append("Voice card (structured):\n" + json.dumps(voice_card, indent=2))
    if not parts:
        return (
            "Reply in a warm, plain-language voice. Never corporate. "
            "Sound like a real person who runs the business."
        )
    parts.append(
        "Always reply in this voice. Reply text only - no preamble, no labels, "
        "no markdown. Under 250 characters. No emojis."
    )
    return "\n\n".join(parts)


def _fallback_reply(reviewer_name: str, stars: int) -> str:
    first = reviewer_name.split()[0] if reviewer_name else "there"
    if stars >= 4:
        return f"Thanks so much, {first}. Means a lot."
    return f"Thanks for the feedback, {first}. We'd love a chance to make it right."


def draft_reply(ctx: TenantContext, review: dict[str, Any]) -> str:
    """Generate a Claude reply draft for one review. Falls back to a short
    canned reply on any Anthropic error so we never silently drop a review."""
    reviewer = (review.get("reviewer") or {}).get("displayName") or "Customer"
    stars = _STAR_TO_INT.get(str(review.get("starRating") or ""), 0)
    comment = (review.get("comment") or "").strip()

    user_prompt = (
        f"A customer left a {stars}-star review.\n"
        f"Reviewer: {reviewer}\n"
        f"Review text: {comment or '(no text)'}\n\n"
        "Write the reply now."
    )

    try:
        result = chat(
            tenant_id=ctx.tenant_id,
            messages=[{"role": "user", "content": user_prompt}],
            system=_build_voice_system(ctx),
            max_tokens=400,
            temperature=0.4,
            kind="review_reply_draft",
            note=f"review_id={review.get('reviewId', '')[:24]}",
            cache_system=True,
        )
    except (OpusUnavailable, OpusBudgetExceeded) as exc:
        log.warning("Opus draft failed for review: %s; using fallback", exc)
        return _fallback_reply(reviewer, stars)
    except Exception as exc:  # noqa: BLE001 - drafter must never crash run
        log.warning("Opus draft errored for review: %s; using fallback", exc)
        return _fallback_reply(reviewer, stars)

    text = (result.text or "").strip()
    return text or _fallback_reply(reviewer, stars)


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------


def _build_event_for_review(review: dict[str, Any]) -> dict[str, Any] | None:
    """Build a heartbeat event for a 5-star review (so goals.bump_current
    fires through dispatch.handle_heartbeat_events). Lower ratings emit
    nothing - the goal predicate filters them anyway."""
    stars = _STAR_TO_INT.get(str(review.get("starRating") or ""), 0)
    if stars < 5:
        return None
    return {
        "kind": "review.posted",
        "stars": stars,
        "review_id": review.get("reviewId"),
    }


def _dispatch_one(
    tenant_id: str,
    review: dict[str, Any],
    body: str,
    account_path: str,
    location_path: str,
) -> dict[str, Any]:
    reviewer = (review.get("reviewer") or {}).get("displayName") or "Customer"
    stars = _STAR_TO_INT.get(str(review.get("starRating") or ""), 0)
    star_glyph = "*" * stars if stars else "?"
    return dispatch.send(
        tenant_id=tenant_id,
        pipeline_id=PIPELINE_ID,
        channel="gbp_review_reply",
        recipient_hint=reviewer,
        subject=f"Reply to {star_glyph} review from {reviewer}",
        body=body,
        metadata={
            "review_id": review.get("reviewId"),
            "account_path": account_path,
            "location_path": location_path,
            "stars": stars,
            "reviewer_name": reviewer,
            "original_comment": (review.get("comment") or "")[:1000],
            "review_create_time": review.get("createTime"),
            "review_update_time": review.get("updateTime"),
        },
    )


def run(
    tenant_id: str,
    *,
    max_reviews: int = DEFAULT_MAX_REVIEWS,
    dry_run: bool = False,
    fetch_reviews_fn=fetch_reviews,
    discover_location_fn=discover_location,
    draft_reply_fn=draft_reply,
    dispatch_fn=_dispatch_one,
    heartbeat_fn=push_heartbeat,
) -> int:
    """Programmatic entry point. The injected callables make the pipeline
    fully testable without touching GBP / Anthropic / the live dashboard."""

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
        log.info("Tenant %s paused; skipping reviews run", tenant_id)
        if not dry_run:
            heartbeat_fn(
                tenant_id=tenant_id,
                pipeline_id=PIPELINE_ID,
                status="success",
                summary="Paused; no reviews drafted.",
            )
        return 0

    if ctx.credentials("google") is None:
        if not dry_run:
            heartbeat_fn(
                tenant_id=tenant_id,
                pipeline_id=PIPELINE_ID,
                status="error",
                summary="Google account not connected.",
            )
        return 0

    if not ctx.has_scope("google", GBP_SCOPE):
        if not dry_run:
            heartbeat_fn(
                tenant_id=tenant_id,
                pipeline_id=PIPELINE_ID,
                status="error",
                summary="Google credential missing business.manage scope.",
            )
        return 0

    try:
        access_token = ctx.access_token("google")
    except Exception as exc:  # noqa: BLE001 - any token error is operational, not crashy
        log.warning("access_token failed for %s: %s", tenant_id, exc)
        if not dry_run:
            heartbeat_fn(
                tenant_id=tenant_id,
                pipeline_id=PIPELINE_ID,
                status="error",
                summary=f"Token refresh failed: {type(exc).__name__}",
            )
        return 0

    try:
        account_path, location_path = discover_location_fn(access_token)
    except Exception as exc:  # noqa: BLE001 - includes RuntimeError + URLError
        log.warning("location discovery failed for %s: %s", tenant_id, exc)
        if not dry_run:
            heartbeat_fn(
                tenant_id=tenant_id,
                pipeline_id=PIPELINE_ID,
                status="error",
                summary=f"GBP location discovery failed: {exc}",
            )
        return 0

    state = ctx.read_state(PIPELINE_ID)
    seen_ids: list[str] = list(state.get("seen_review_ids") or [])
    seen_set = set(seen_ids)

    reviews = fetch_reviews_fn(access_token, account_path, location_path)
    new_reviews = [r for r in reviews if r.get("reviewId") and r.get("reviewId") not in seen_set]
    new_reviews = new_reviews[:max_reviews]

    drafted = 0
    queued = 0
    delivered = 0
    failed = 0
    skipped_no_text = 0
    events: list[dict[str, Any]] = []

    for review in new_reviews:
        rid = review.get("reviewId")
        if not rid:
            continue

        ev = _build_event_for_review(review)
        if ev is not None:
            events.append(ev)

        # Reviews with no comment text get marked seen but not drafted - a
        # one-line "Thanks!" reply on a star-only review feels robotic. The
        # 5-star event still fires above so the goal still bumps.
        comment = (review.get("comment") or "").strip()
        if not comment:
            skipped_no_text += 1
            seen_ids.append(rid)
            continue

        body = draft_reply_fn(ctx, review)

        if dry_run:
            print(json.dumps({
                "review_id": rid,
                "stars": _STAR_TO_INT.get(str(review.get("starRating") or ""), 0),
                "reviewer": (review.get("reviewer") or {}).get("displayName"),
                "draft": body,
            }, indent=2))
            drafted += 1
            seen_ids.append(rid)
            continue

        outcome = dispatch_fn(tenant_id, review, body, account_path, location_path)
        action = outcome.get("action")
        if action == "queued":
            queued += 1
        elif action == "delivered":
            delivered += 1
        elif action == "skipped":
            # tenant got paused mid-run; bail without writing the rest
            log.info("dispatch reports paused mid-run; stopping early")
            break
        else:
            failed += 1
            log.warning(
                "dispatch %s for review %s: %s",
                action,
                rid,
                outcome.get("reason") or outcome,
            )

        drafted += 1
        seen_ids.append(rid)

    # Cap seen_ids so the state file doesn't grow unbounded for high-volume
    # tenants. 500 is roughly two years of reviews for a busy single-location
    # business; anything older has zero chance of re-appearing in fetch.
    if len(seen_ids) > SEEN_IDS_CAP:
        seen_ids = seen_ids[-SEEN_IDS_CAP:]

    ctx.write_state(
        PIPELINE_ID,
        {
            "seen_review_ids": seen_ids,
            "last_check": datetime.now(timezone.utc).isoformat(),
            "drafted_total": int(state.get("drafted_total") or 0) + drafted,
        },
    )

    summary = _build_summary(
        drafted=drafted,
        queued=queued,
        delivered=delivered,
        failed=failed,
        skipped_no_text=skipped_no_text,
        new_total=len(new_reviews),
    )
    # Error only when every dispatch failed; partial success stays "success".
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
    skipped_no_text: int,
    new_total: int,
) -> str:
    if new_total == 0:
        return "No new reviews."
    parts = [f"Drafted {drafted} of {new_total}"]
    if queued:
        parts.append(f"{queued} queued")
    if delivered:
        parts.append(f"{delivered} sent")
    if failed:
        parts.append(f"{failed} failed")
    if skipped_no_text:
        parts.append(f"{skipped_no_text} no-text")
    return "; ".join(parts) + "."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generic per-tenant Reviews pipeline (W3.x).",
    )
    parser.add_argument("--tenant", required=True, help="tenant_id slug")
    parser.add_argument(
        "--max",
        type=int,
        default=DEFAULT_MAX_REVIEWS,
        dest="max_reviews",
        help="Max reviews to draft this run (default: 20)",
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
        max_reviews=args.max_reviews,
        dry_run=args.dry_run,
    )


__all__ = [
    "PIPELINE_ID",
    "discover_location",
    "fetch_reviews",
    "draft_reply",
    "run",
    "main",
]


if __name__ == "__main__":
    sys.exit(main())
