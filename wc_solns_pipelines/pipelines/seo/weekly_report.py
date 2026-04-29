"""Generic per-tenant weekly SEO digest pipeline.

Run via:

    python -m wc_solns_pipelines.pipelines.seo.weekly_report --tenant <tenant_id>

What it does on each run:
  1. Build a TenantContext. Bail with error heartbeat on invalid slug.
  2. Honor the tenant pause flag.
  3. Resolve Google credentials. SEO needs analytics.readonly +
     webmasters.readonly. Missing scopes -> error heartbeat.
  4. Read tenant_config.json for `ga4_property_id` + `gsc_site_url`.
     These get set during activation when the owner picks their property
     and verified site (admin step today; auto-pick from probe ships
     later). Either missing -> error heartbeat with "set GA4 property /
     GSC site in /settings".
  5. Pull GA4 + GSC summaries for trailing 7 days vs prior 7 days.
     Both fetches inject through callables for testability.
  6. Drop both summaries + the prior week's `last_metrics` snapshot into
     services.opus.chat to compose a plain-language digest in the
     tenant's voice.
  7. Dispatch via dispatch.send (channel="email", pipeline="seo",
     recipient_hint=primary owner email). Approval-gated tenants land it
     in the queue; auto-send tenants currently get no_dispatcher (the
     real email handler ships W5 alongside email_assistant).
  8. Persist state with this week's metrics for next week's deltas.
  9. Push heartbeat with summary + a counts hint.

Always exits 0; errors surface via heartbeat status.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dashboard_app.services import dispatch
from dashboard_app.services.opus import OpusBudgetExceeded, OpusUnavailable, chat
from wc_solns_pipelines.shared.push_heartbeat import push as push_heartbeat
from wc_solns_pipelines.shared.tenant_runtime import TenantContext, TenantNotFound

PIPELINE_ID = "seo"
SCOPE_GA4 = "https://www.googleapis.com/auth/analytics.readonly"
SCOPE_GSC = "https://www.googleapis.com/auth/webmasters.readonly"
DEFAULT_TIMEOUT = 15.0
TOP_N = 10

log = logging.getLogger("wcas.pipelines.seo")


# ---------------------------------------------------------------------------
# date windows
# ---------------------------------------------------------------------------


def _date_windows(today: datetime | None = None) -> tuple[str, str, str, str]:
    """Returns (current_start, current_end, prior_start, prior_end) as
    YYYY-MM-DD strings. Both ranges inclusive.

    current  = [today-7, today-1]
    prior    = [today-14, today-8]
    """
    today = today or datetime.now(timezone.utc)
    current_end = (today - timedelta(days=1)).date().isoformat()
    current_start = (today - timedelta(days=7)).date().isoformat()
    prior_end = (today - timedelta(days=8)).date().isoformat()
    prior_start = (today - timedelta(days=14)).date().isoformat()
    return current_start, current_end, prior_start, prior_end


# ---------------------------------------------------------------------------
# GA4 + GSC fetchers
# ---------------------------------------------------------------------------


def _http_post_json(
    url: str,
    access_token: str,
    payload: dict[str, Any],
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return json.loads(body) if body else {}


def fetch_ga4_summary(
    access_token: str,
    property_id: str,
    *,
    today: datetime | None = None,
) -> dict[str, Any]:
    """Pull a 7-day GA4 summary. Returns {totals, top_pages, error?}.

    totals = {sessions: int, totalUsers: int, conversions: int} for
             the trailing 7 days.
    top_pages = list of {path, sessions} length up to TOP_N.

    Empty/error results return {totals: {...zeros}, top_pages: [],
    error: "..."} so the digest always renders something readable.
    """
    cur_start, cur_end, _, _ = _date_windows(today)
    pid = property_id.replace("properties/", "")
    out: dict[str, Any] = {
        "totals": {"sessions": 0, "totalUsers": 0, "conversions": 0},
        "top_pages": [],
    }

    # Totals
    try:
        body = _http_post_json(
            f"https://analyticsdata.googleapis.com/v1beta/properties/{pid}:runReport",
            access_token,
            {
                "dateRanges": [{"startDate": cur_start, "endDate": cur_end}],
                "metrics": [
                    {"name": "sessions"},
                    {"name": "totalUsers"},
                    {"name": "conversions"},
                ],
            },
        )
        rows = body.get("rows") or []
        if rows:
            metric_values = rows[0].get("metricValues") or []
            if len(metric_values) >= 3:
                out["totals"]["sessions"] = int(float(metric_values[0].get("value", 0) or 0))
                out["totals"]["totalUsers"] = int(float(metric_values[1].get("value", 0) or 0))
                out["totals"]["conversions"] = int(float(metric_values[2].get("value", 0) or 0))
    except (HTTPError, URLError, OSError, json.JSONDecodeError, ValueError) as exc:
        out["error"] = f"GA4 totals fetch failed: {exc}"
        return out

    # Top pages
    try:
        body = _http_post_json(
            f"https://analyticsdata.googleapis.com/v1beta/properties/{pid}:runReport",
            access_token,
            {
                "dateRanges": [{"startDate": cur_start, "endDate": cur_end}],
                "dimensions": [{"name": "pagePath"}],
                "metrics": [{"name": "sessions"}],
                "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}],
                "limit": str(TOP_N),
            },
        )
        rows = body.get("rows") or []
        for r in rows:
            dim = (r.get("dimensionValues") or [{}])[0].get("value", "")
            metric = (r.get("metricValues") or [{}])[0].get("value", "0")
            try:
                count = int(float(metric))
            except ValueError:
                count = 0
            out["top_pages"].append({"path": dim, "sessions": count})
    except (HTTPError, URLError, OSError, json.JSONDecodeError, ValueError) as exc:
        out["error"] = f"GA4 top_pages fetch failed: {exc}"

    return out


def fetch_gsc_summary(
    access_token: str,
    site_url: str,
    *,
    today: datetime | None = None,
) -> dict[str, Any]:
    """Pull a 7-day GSC summary. Returns {totals, top_queries, error?}.

    totals = {clicks, impressions, ctr, position}
    top_queries = list of {query, clicks, impressions, position} length TOP_N.

    Empty/error results return safe zeros + an error string.
    """
    cur_start, cur_end, _, _ = _date_windows(today)
    out: dict[str, Any] = {
        "totals": {"clicks": 0, "impressions": 0, "ctr": 0.0, "position": 0.0},
        "top_queries": [],
    }

    base = f"https://searchconsole.googleapis.com/webmasters/v3/sites/{site_url}/searchAnalytics/query"

    # Totals: query without dimensions returns one row of aggregate metrics.
    try:
        body = _http_post_json(
            base,
            access_token,
            {"startDate": cur_start, "endDate": cur_end, "rowLimit": 1},
        )
        rows = body.get("rows") or []
        if rows:
            r = rows[0]
            out["totals"]["clicks"] = int(r.get("clicks") or 0)
            out["totals"]["impressions"] = int(r.get("impressions") or 0)
            out["totals"]["ctr"] = round(float(r.get("ctr") or 0.0), 4)
            out["totals"]["position"] = round(float(r.get("position") or 0.0), 2)
    except (HTTPError, URLError, OSError, json.JSONDecodeError, ValueError) as exc:
        out["error"] = f"GSC totals fetch failed: {exc}"
        return out

    # Top queries
    try:
        body = _http_post_json(
            base,
            access_token,
            {
                "startDate": cur_start,
                "endDate": cur_end,
                "dimensions": ["query"],
                "rowLimit": TOP_N,
            },
        )
        rows = body.get("rows") or []
        for r in rows:
            keys = r.get("keys") or [""]
            out["top_queries"].append(
                {
                    "query": keys[0] if keys else "",
                    "clicks": int(r.get("clicks") or 0),
                    "impressions": int(r.get("impressions") or 0),
                    "position": round(float(r.get("position") or 0.0), 2),
                }
            )
    except (HTTPError, URLError, OSError, json.JSONDecodeError, ValueError) as exc:
        out["error"] = f"GSC top_queries fetch failed: {exc}"

    return out


# ---------------------------------------------------------------------------
# digest composition
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
        parts.append(
            "Write in a warm, plain-language voice. Speak directly to the owner. "
            "No jargon. No corporate phrases."
        )
    parts.append(
        "You are writing this owner's weekly SEO digest. They are not technical. "
        "Open with the headline metric and what changed week over week. Then "
        "name the top page and the top query, both with numbers. Close with "
        "one specific suggestion the owner could act on this week. "
        "Plain text, no markdown headings, no emojis. "
        "Length: 5-8 short paragraphs."
    )
    return "\n\n".join(parts)


def _delta_pct(curr: float, prior: float) -> str:
    if prior <= 0:
        return "n/a (no prior data)"
    pct = ((curr - prior) / prior) * 100.0
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.1f}%"


def _format_metrics_block(ga4: dict[str, Any], gsc: dict[str, Any], prior: dict[str, Any]) -> str:
    """Build a compact, factual numbers block to feed Claude. The model
    rewrites this into the owner's voice; we keep the source numbers
    structured so it can't hallucinate them."""
    prior_ga4_totals = (prior.get("ga4") or {}).get("totals") or {}
    prior_gsc_totals = (prior.get("gsc") or {}).get("totals") or {}

    cur_sessions = ga4["totals"]["sessions"]
    prior_sessions = int(prior_ga4_totals.get("sessions") or 0)
    cur_users = ga4["totals"]["totalUsers"]
    prior_users = int(prior_ga4_totals.get("totalUsers") or 0)

    cur_clicks = gsc["totals"]["clicks"]
    prior_clicks = int(prior_gsc_totals.get("clicks") or 0)
    cur_impr = gsc["totals"]["impressions"]
    prior_impr = int(prior_gsc_totals.get("impressions") or 0)

    lines = [
        "GA4 (this week vs last week):",
        f"  Sessions: {cur_sessions} (was {prior_sessions}, {_delta_pct(cur_sessions, prior_sessions)})",
        f"  Users:    {cur_users} (was {prior_users}, {_delta_pct(cur_users, prior_users)})",
        f"  Conversions: {ga4['totals']['conversions']}",
        "",
        "Top pages by sessions this week:",
    ]
    for p in ga4["top_pages"][:5]:
        lines.append(f"  {p['path']}  -  {p['sessions']} sessions")
    if not ga4["top_pages"]:
        lines.append("  (none)")

    lines += [
        "",
        "Google Search Console (this week vs last week):",
        f"  Clicks:      {cur_clicks} (was {prior_clicks}, {_delta_pct(cur_clicks, prior_clicks)})",
        f"  Impressions: {cur_impr} (was {prior_impr}, {_delta_pct(cur_impr, prior_impr)})",
        f"  CTR:         {gsc['totals']['ctr'] * 100:.2f}%",
        f"  Avg position: {gsc['totals']['position']:.2f}",
        "",
        "Top queries this week:",
    ]
    for q in gsc["top_queries"][:5]:
        lines.append(
            f"  {q['query']}  -  {q['clicks']} clicks, "
            f"{q['impressions']} impressions, pos {q['position']:.1f}"
        )
    if not gsc["top_queries"]:
        lines.append("  (none)")

    if "error" in ga4:
        lines += ["", f"GA4 NOTE: {ga4['error']}"]
    if "error" in gsc:
        lines += ["", f"GSC NOTE: {gsc['error']}"]

    return "\n".join(lines)


def _fallback_digest(metrics_block: str, week_label: str) -> str:
    return (
        f"Here's the SEO snapshot for {week_label}.\n\n"
        + metrics_block
        + "\n\nIf any number looks surprising, hit reply and we'll dig in."
    )


def compose_digest(
    ctx: TenantContext,
    ga4: dict[str, Any],
    gsc: dict[str, Any],
    prior_state: dict[str, Any],
    *,
    week_label: str,
) -> str:
    """Build the readable owner-facing digest body. Falls back to the
    metrics-block-with-cover-paragraph format on any Anthropic error."""
    metrics_block = _format_metrics_block(ga4, gsc, prior_state)

    user = (
        f"Compose this week's SEO digest. Week: {week_label}\n\n"
        "Source numbers (do not invent any others):\n"
        + metrics_block
        + "\n\nWrite the email body now. Speak to the owner directly."
    )

    try:
        result = chat(
            tenant_id=ctx.tenant_id,
            messages=[{"role": "user", "content": user}],
            system=_build_voice_system(ctx),
            max_tokens=1200,
            temperature=0.4,
            kind="seo_digest_draft",
            note=f"week={week_label}",
            cache_system=True,
        )
    except (OpusUnavailable, OpusBudgetExceeded) as exc:
        log.warning("Opus digest failed: %s; using fallback", exc)
        return _fallback_digest(metrics_block, week_label)
    except Exception as exc:  # noqa: BLE001
        log.warning("Opus digest errored: %s; using fallback", exc)
        return _fallback_digest(metrics_block, week_label)

    text = (result.text or "").strip()
    return text or _fallback_digest(metrics_block, week_label)


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------


def _dispatch_digest(
    tenant_id: str,
    body: str,
    recipient: str,
    week_label: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    return dispatch.send(
        tenant_id=tenant_id,
        pipeline_id=PIPELINE_ID,
        channel="email",
        recipient_hint=recipient,
        subject=f"Weekly SEO digest - {week_label}",
        body=body,
        metadata=metadata,
    )


def _resolve_owner_email(ctx: TenantContext) -> str:
    """Where does the digest go? Today: tenant_config.json:owner_email or
    contact.email. Future: per-pipeline override in prefs. Empty string
    means the dispatcher will route via the queue's contact lookup."""
    cfg = ctx.config()
    candidates = [
        cfg.get("owner_email"),
        (cfg.get("contact") or {}).get("email") if isinstance(cfg.get("contact"), dict) else None,
        cfg.get("email"),
    ]
    for c in candidates:
        if isinstance(c, str) and "@" in c:
            return c
    return ""


def run(
    tenant_id: str,
    *,
    dry_run: bool = False,
    fetch_ga4_fn=fetch_ga4_summary,
    fetch_gsc_fn=fetch_gsc_summary,
    compose_digest_fn=compose_digest,
    dispatch_fn=_dispatch_digest,
    heartbeat_fn=push_heartbeat,
    today: datetime | None = None,
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
        if not dry_run:
            heartbeat_fn(
                tenant_id=tenant_id,
                pipeline_id=PIPELINE_ID,
                status="success",
                summary="Paused; no digest drafted.",
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

    missing_scopes: list[str] = []
    if not ctx.has_scope("google", SCOPE_GA4):
        missing_scopes.append("analytics.readonly")
    if not ctx.has_scope("google", SCOPE_GSC):
        missing_scopes.append("webmasters.readonly")
    if missing_scopes:
        if not dry_run:
            heartbeat_fn(
                tenant_id=tenant_id,
                pipeline_id=PIPELINE_ID,
                status="error",
                summary=f"Google credential missing scopes: {', '.join(missing_scopes)}.",
            )
        return 0

    cfg = ctx.config()
    property_id = (cfg.get("ga4_property_id") or "").strip()
    site_url = (cfg.get("gsc_site_url") or "").strip()
    if not property_id:
        if not dry_run:
            heartbeat_fn(
                tenant_id=tenant_id,
                pipeline_id=PIPELINE_ID,
                status="error",
                summary="Set GA4 property in /settings (tenant_config.ga4_property_id missing).",
            )
        return 0
    if not site_url:
        if not dry_run:
            heartbeat_fn(
                tenant_id=tenant_id,
                pipeline_id=PIPELINE_ID,
                status="error",
                summary="Set GSC site in /settings (tenant_config.gsc_site_url missing).",
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

    state = ctx.read_state(PIPELINE_ID)
    prior = (state.get("last_metrics") or {})

    ga4 = fetch_ga4_fn(access_token, property_id, today=today)
    gsc = fetch_gsc_fn(access_token, site_url, today=today)

    cur_start, cur_end, _, _ = _date_windows(today)
    week_label = f"{cur_start} - {cur_end}"

    body = compose_digest_fn(ctx, ga4, gsc, prior, week_label=week_label)
    recipient = _resolve_owner_email(ctx)

    if dry_run:
        print(json.dumps(
            {"week": week_label, "recipient": recipient, "body": body, "ga4": ga4, "gsc": gsc},
            indent=2, default=str,
        ))
        return 0

    metadata = {
        "ga4_totals": ga4["totals"],
        "gsc_totals": gsc["totals"],
        "week_start": cur_start,
        "week_end": cur_end,
        "property_id": property_id,
        "site_url": site_url,
    }
    outcome = dispatch_fn(tenant_id, body, recipient, week_label, metadata)
    action = outcome.get("action")

    # Persist for next-week's deltas. Always update; the digest already
    # rendered with prior data, so storing the new week is correct.
    ctx.write_state(
        PIPELINE_ID,
        {
            "last_metrics": {"ga4": ga4, "gsc": gsc},
            "last_week": week_label,
            "drafted_total": int(state.get("drafted_total") or 0) + 1,
            "last_dispatch_action": action,
        },
    )

    if action == "queued":
        status = "success"
        summary = f"Drafted digest; queued for approval. Sessions: {ga4['totals']['sessions']}, clicks: {gsc['totals']['clicks']}."
    elif action == "delivered":
        status = "success"
        summary = f"Drafted + sent digest. Sessions: {ga4['totals']['sessions']}, clicks: {gsc['totals']['clicks']}."
    elif action == "skipped":
        status = "success"
        summary = "Tenant became paused mid-run; no digest dispatched."
    elif action == "no_dispatcher":
        status = "success"
        summary = "Drafted digest; no auto-send handler yet (turn on Approve-Before-Send to start queueing)."
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
        description="Generic per-tenant weekly SEO digest pipeline (W4).",
    )
    parser.add_argument("--tenant", required=True, help="tenant_id slug")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print metrics + digest body, do not dispatch or POST heartbeat.",
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
    "fetch_ga4_summary",
    "fetch_gsc_summary",
    "compose_digest",
    "run",
    "main",
]


if __name__ == "__main__":
    sys.exit(main())
