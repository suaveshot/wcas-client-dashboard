"""Generic per-tenant Google Business Profile pipeline.

Run via:

    python -m wc_solns_pipelines.pipelines.gbp.run --tenant <tenant_id>

What it does on each run:
  1. Build a TenantContext. Bail with error heartbeat on invalid slug.
  2. Honor the tenant pause flag.
  3. Resolve Google credentials + business.manage scope.
  4. Discover the tenant's first GBP account + location (single-location
     assumption today; location-picker UI ships post-W4 alongside admin).
  5. Pick the next post topic from the tenant's pipeline_state rotation.
     The rotation seed comes from KB section "services" (split into
     short topic stubs) or, if absent, the default rotation defined here.
  6. Draft a "What's New" GBP post via services.opus.chat grounded in
     the tenant's voice + company KB sections. GBP post body cap:
     1500 chars; we target ~600 to stay readable.
  7. Dispatch through services.dispatch.send (channel="gbp_post",
     pipeline_id="gbp"). When prefs.require_approval[gbp] is on the
     post lands in the outgoing queue for owner approval; otherwise
     it routes to OUTGOING_HANDLERS["gbp"] (handler ships W4+ when
     the publish-side rolls out; today it returns no_dispatcher and
     the heartbeat reflects that).
  8. Persist state (topic_index, last_post, posts_published).
  9. Push heartbeat with summary.

Always exits 0; errors surface via heartbeat status=error.
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

PIPELINE_ID = "gbp"
GBP_SCOPE = "https://www.googleapis.com/auth/business.manage"
GBP_POST_MAX_CHARS = 1500
TARGET_POST_CHARS = 600
DEFAULT_TIMEOUT = 10.0

# Default topic rotation when a tenant doesn't have services.md or its
# own topic list. Keeps content varied across the year.
DEFAULT_TOPICS: list[str] = [
    "Spotlight a service we offer and why customers choose it",
    "Share a behind-the-scenes look at how we do the work",
    "Highlight a recent customer outcome (anonymized if needed)",
    "Answer the single most common question we hear",
    "Share a seasonal tip relevant to what we do",
    "Talk about the team and how long we've been around",
    "Walk through a typical first-visit / first-call experience",
    "Bust a common myth about our category",
]

log = logging.getLogger("wcas.pipelines.gbp")


# ---------------------------------------------------------------------------
# GBP discovery (re-uses the pattern from reviews/run.py without import-cycling)
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
    """Return (account_path, location_path) for the tenant's first location.

    See reviews/run.py::discover_location - same contract; duplicated here
    so each pipeline is self-contained without a shared GBP wrapper that
    would tempt premature consolidation. If a third pipeline needs this,
    extract to wc_solns_pipelines/shared/gbp.py.
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


# ---------------------------------------------------------------------------
# topic rotation
# ---------------------------------------------------------------------------


def _topics_from_services_kb(kb_text: str) -> list[str]:
    """Light extraction: each non-empty bullet/line becomes a topic stub.
    Keeps things resilient against varied KB formatting; if the section
    is prose we just take the first 6 sentences."""
    lines = [
        line.strip().lstrip("-*0123456789. ").strip()
        for line in kb_text.splitlines()
    ]
    bullets = [line for line in lines if line and len(line) > 10]
    if bullets:
        return [f"Spotlight: {b[:140]}" for b in bullets[:12]]
    # Fall back to sentences
    sentences = [s.strip() for s in kb_text.replace("\n", " ").split(".") if len(s.strip()) > 20]
    return [f"Spotlight: {s[:140]}" for s in sentences[:6]]


def topics_for_tenant(ctx: TenantContext) -> list[str]:
    """Resolve the post topic list for a tenant. Order of preference:
    1. KB "services" section -> topic stubs derived from each line/bullet
    2. Default rotation
    """
    services_kb = ctx.kb("services")
    if services_kb:
        derived = _topics_from_services_kb(services_kb)
        if derived:
            return derived
    return list(DEFAULT_TOPICS)


def pick_next_topic(topics: list[str], state: dict[str, Any]) -> tuple[str, int]:
    if not topics:
        return ("Share something useful with our customers", 0)
    idx = int(state.get("topic_index") or 0) % len(topics)
    return (topics[idx], (idx + 1) % len(topics))


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
    voice_card = ctx.voice_card()
    if isinstance(voice_card, dict) and voice_card:
        parts.append("Voice card (structured):\n" + json.dumps(voice_card, indent=2))
    if not parts:
        return (
            "Write in a warm, plain-language voice. Never corporate. "
            "Sound like a real person who runs the business."
        )
    parts.append(
        f"Always write in this voice. Plain text only - no markdown, no emojis, "
        f"no hashtags. Target around {TARGET_POST_CHARS} characters; "
        f"hard ceiling {GBP_POST_MAX_CHARS}. End on a soft, helpful note."
    )
    return "\n\n".join(parts)


def _fallback_post(topic: str) -> str:
    return (
        f"This week we're thinking about: {topic}. "
        "If you've been wondering whether we can help with something specific, "
        "give us a call - we like talking through what people actually need."
    )


def draft_post(ctx: TenantContext, topic: str) -> str:
    """Generate a 'What's New' post draft via Claude. Returns the body text.
    Falls back to a short canned post on any Anthropic error so we never
    silently skip a week."""
    user = (
        f"Write this week's Google Business Profile 'What's New' post. "
        f"Topic prompt: {topic}\n\n"
        "Write only the body text, nothing else. No labels."
    )
    try:
        result = chat(
            tenant_id=ctx.tenant_id,
            messages=[{"role": "user", "content": user}],
            system=_build_voice_system(ctx),
            max_tokens=600,
            temperature=0.5,
            kind="gbp_post_draft",
            note=topic[:60],
            cache_system=True,
        )
    except (OpusUnavailable, OpusBudgetExceeded) as exc:
        log.warning("Opus draft failed for GBP post: %s; using fallback", exc)
        return _fallback_post(topic)
    except Exception as exc:  # noqa: BLE001 - drafter must never crash run
        log.warning("Opus draft errored for GBP post: %s; using fallback", exc)
        return _fallback_post(topic)

    text = (result.text or "").strip()
    if not text:
        return _fallback_post(topic)
    if len(text) > GBP_POST_MAX_CHARS:
        text = text[: GBP_POST_MAX_CHARS - 1].rstrip() + "."
    return text


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------


def _dispatch_post(
    tenant_id: str,
    topic: str,
    body: str,
    account_path: str,
    location_path: str,
) -> dict[str, Any]:
    return dispatch.send(
        tenant_id=tenant_id,
        pipeline_id=PIPELINE_ID,
        channel="gbp_post",
        recipient_hint=location_path or "GBP location",
        subject=f"GBP post: {topic[:80]}",
        body=body,
        metadata={
            "topic": topic,
            "account_path": account_path,
            "location_path": location_path,
            "post_kind": "STANDARD",  # Google's term for "What's New"
        },
    )


def run(
    tenant_id: str,
    *,
    dry_run: bool = False,
    discover_location_fn=discover_location,
    draft_post_fn=draft_post,
    dispatch_fn=_dispatch_post,
    heartbeat_fn=push_heartbeat,
) -> int:
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
        log.info("Tenant %s paused; skipping GBP post", tenant_id)
        if not dry_run:
            heartbeat_fn(
                tenant_id=tenant_id,
                pipeline_id=PIPELINE_ID,
                status="success",
                summary="Paused; no post drafted.",
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
    except Exception as exc:  # noqa: BLE001
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
    except Exception as exc:  # noqa: BLE001
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
    topics = topics_for_tenant(ctx)
    topic, next_idx = pick_next_topic(topics, state)
    body = draft_post_fn(ctx, topic)

    if dry_run:
        print(json.dumps({"topic": topic, "draft": body, "next_topic_index": next_idx}, indent=2))
        return 0

    outcome = dispatch_fn(tenant_id, topic, body, account_path, location_path)
    action = outcome.get("action")

    posts_published = int(state.get("posts_published") or 0)
    last_post = {
        "topic": topic,
        "drafted_at": datetime.now(timezone.utc).isoformat(),
        "dispatch_action": action,
    }
    if action == "delivered":
        posts_published += 1
        last_post["delivered_at"] = last_post["drafted_at"]
    elif action == "queued":
        last_post["draft_id"] = outcome.get("draft_id")

    ctx.write_state(
        PIPELINE_ID,
        {
            "topic_index": next_idx,
            "last_post": last_post,
            "posts_published": posts_published,
            "drafted_total": int(state.get("drafted_total") or 0) + 1,
        },
    )

    if action == "queued":
        status = "success"
        summary = f"Drafted post; queued for approval. Topic: {topic[:80]}"
    elif action == "delivered":
        status = "success"
        summary = f"Drafted + published post. Topic: {topic[:80]}"
    elif action == "skipped":
        status = "success"
        summary = "Tenant became paused mid-run; no post dispatched."
    elif action == "no_dispatcher":
        status = "success"
        summary = (
            "Drafted post; no auto-publish handler yet (turn on Approve-Before-Send "
            "in /settings to start queueing for review)."
        )
    else:
        status = "error"
        summary = f"Dispatch {action}: {outcome.get('reason') or outcome}"

    heartbeat_fn(
        tenant_id=tenant_id,
        pipeline_id=PIPELINE_ID,
        status=status,
        summary=summary,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generic per-tenant GBP weekly post pipeline (W4).",
    )
    parser.add_argument("--tenant", required=True, help="tenant_id slug")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print topic + draft, do not dispatch or POST heartbeat.",
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

    return run(tenant_id=args.tenant, dry_run=args.dry_run)


__all__ = [
    "PIPELINE_ID",
    "DEFAULT_TOPICS",
    "discover_location",
    "topics_for_tenant",
    "pick_next_topic",
    "draft_post",
    "run",
    "main",
]


if __name__ == "__main__":
    sys.exit(main())
