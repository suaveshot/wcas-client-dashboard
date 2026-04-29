"""SEO Recommendations Engine.

Synthesizes ranked, dollar-impact-estimated, "what to change on your
site this week" recommendations from four data sources:

    GSC          - exact queries + position + CTR + impressions
    GA4          - traffic, engagement, conversions
    BrightLocal  - local-pack rankings + competitor benchmarking
                   (Pattern C - via brightlocal_master)
    site fetch   - actual HTML, headings, meta tags, content length

Output goes to /opt/wc-solns/<tenant>/pipeline_state/seo_recommendations.json
so the dashboard's /roles/seo panel can render it without re-running the
synthesis. The synthesis itself runs once a week via run_weekly().

The recommender is differentiation: BrightLocal sells "rankings reports,"
SEO consultants sell "we'll do it for you." Almost nobody sells "AI tells
you exactly what to change on your site this week, ranked by traffic
lift, in your voice." This is the engine.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Callable

from . import heartbeat_store, opus

log = logging.getLogger(__name__)

CACHE_FILENAME = "seo_recommendations.json"
PIPELINE_STATE_DIR = "pipeline_state"
DEFAULT_TOP_N = 5
MAX_RECS = 10
SITE_FETCH_CAP_CHARS = 30_000

# Recommendation schema. Anything missing required fields is dropped.
_REQUIRED_KEYS = {"id", "title", "rationale", "specific_action"}
_OPTIONAL_KEYS = {
    "evidence",  # list[str]
    "estimated_traffic_lift",  # str - "+12-18 sessions/week"
    "urgency",  # "high" | "medium" | "low"
    "category",  # "title_tag" | "content" | "schema" | "internal_linking" | "local_pack" | "other"
    "page",  # affected URL or path
}
_ALL_KEYS = _REQUIRED_KEYS | _OPTIONAL_KEYS

_URGENCY_ORDER = {"high": 0, "medium": 1, "low": 2}


# ---------------------------------------------------------------------------
# cache I/O
# ---------------------------------------------------------------------------


def _cache_path(tenant_id: str) -> Path:
    return heartbeat_store.tenant_root(tenant_id) / PIPELINE_STATE_DIR / CACHE_FILENAME


def get_cached(tenant_id: str) -> list[dict[str, Any]]:
    """Return the most recent recommendations list, or [] if no cache."""
    try:
        path = _cache_path(tenant_id)
    except heartbeat_store.HeartbeatError:
        return []
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    recs = data.get("recommendations") or []
    return [r for r in recs if isinstance(r, dict)][:MAX_RECS]


def get_cache_meta(tenant_id: str) -> dict[str, Any]:
    """Return {generated_at, source_summary, recommendation_count} or {}."""
    try:
        path = _cache_path(tenant_id)
    except heartbeat_store.HeartbeatError:
        return {}
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        "generated_at": data.get("generated_at"),
        "source_summary": data.get("source_summary") or {},
        "recommendation_count": len(data.get("recommendations") or []),
    }


def _write_cache(tenant_id: str, recommendations: list[dict[str, Any]], source_summary: dict[str, Any]) -> Path:
    path = _cache_path(tenant_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": int(time.time()),
        "source_summary": source_summary,
        "recommendations": recommendations,
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    import os as _os
    _os.replace(tmp, path)
    return path


# ---------------------------------------------------------------------------
# parsing + ranking
# ---------------------------------------------------------------------------


def _parse_json_response(text: str) -> list[dict[str, Any]]:
    """Pull the JSON array out of an Opus response. Tolerant of:
      - bare JSON arrays
      - JSON wrapped in ```json fences
      - a JSON object with "recommendations" key
    Returns [] on any parse failure (the caller logs + falls back)."""
    if not text:
        return []
    cleaned = text.strip()

    # Strip ```json fences
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    # Try direct parse first
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # Try to extract the first JSON array span
        match = re.search(r"\[\s*\{.*?\}\s*\]", cleaned, re.DOTALL)
        if not match:
            log.warning("Recommender: no JSON array found in response (len=%d)", len(cleaned))
            return []
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            log.warning("Recommender: JSON parse failed even after extraction: %s", exc)
            return []

    if isinstance(data, dict) and isinstance(data.get("recommendations"), list):
        data = data["recommendations"]
    if not isinstance(data, list):
        return []

    return [item for item in data if isinstance(item, dict)]


def _normalize_rec(raw: dict[str, Any], idx: int) -> dict[str, Any] | None:
    """Coerce a single rec into our schema. Returns None if the rec is
    missing required fields (so we never surface half-baked recs)."""
    rec: dict[str, Any] = {}
    for k, v in raw.items():
        if k in _ALL_KEYS and v not in (None, "", []):
            rec[k] = v

    if not _REQUIRED_KEYS.issubset(rec.keys()):
        return None

    # Normalize urgency
    urgency = str(rec.get("urgency") or "medium").lower().strip()
    if urgency not in _URGENCY_ORDER:
        urgency = "medium"
    rec["urgency"] = urgency

    # Normalize evidence to list[str]
    evidence = rec.get("evidence")
    if evidence is None:
        rec["evidence"] = []
    elif isinstance(evidence, str):
        rec["evidence"] = [evidence]
    elif isinstance(evidence, list):
        rec["evidence"] = [str(e) for e in evidence if e]
    else:
        rec["evidence"] = []

    # Normalize id
    rid = str(rec.get("id") or f"rec-{idx + 1:03d}").strip()
    rec["id"] = rid[:64]

    # Trim long fields so the dashboard panel stays readable
    for key, cap in (("title", 140), ("rationale", 600), ("specific_action", 600)):
        if key in rec:
            rec[key] = str(rec[key])[:cap]

    return rec


def _rank(recs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort by urgency (high -> low), then preserve original order
    within an urgency bucket."""
    sorted_pairs = sorted(
        enumerate(recs),
        key=lambda pair: (_URGENCY_ORDER.get(pair[1].get("urgency", "medium"), 1), pair[0]),
    )
    return [pair[1] for pair in sorted_pairs]


# ---------------------------------------------------------------------------
# prompt building
# ---------------------------------------------------------------------------


_OUTPUT_SCHEMA_REMINDER = """\
Return ONLY a JSON array. No prose, no markdown fences. Each element is a
recommendation object with these keys:
  id              short stable identifier (e.g. "rec-001")
  title           one-line summary (under 140 chars)
  rationale       why this matters; cite the data point that triggered it
  specific_action concrete change to make on the site this week
  evidence        array of short data-point strings (GSC/GA4/BrightLocal numbers)
  estimated_traffic_lift  short string like "+12-18 sessions/week" or "n/a"
  urgency         "high" | "medium" | "low"
  category        "title_tag" | "content" | "schema" | "internal_linking" | "local_pack" | "other"
  page            the URL/path affected (when applicable)

Generate up to 6 recommendations. Skip generic advice ("optimize for
mobile") - every recommendation must reference at least one number from
the source data. If the data is too thin to ground a recommendation,
return an empty array [].
"""


def _build_data_block(
    *,
    ga4: dict[str, Any] | None,
    gsc: dict[str, Any] | None,
    rankings: dict[str, Any] | None,
    site_facts: dict[str, Any] | None,
) -> str:
    """Squash the four sources into a compact source-of-truth block. The
    model rewrites it into recommendations; we keep the numbers
    structured so it can't fabricate them."""
    lines: list[str] = []

    if ga4 and ga4.get("totals"):
        t = ga4["totals"]
        lines.append("=== GA4 (last 7 days) ===")
        lines.append(
            f"Sessions: {t.get('sessions', 0)}  "
            f"Users: {t.get('totalUsers', 0)}  "
            f"Conversions: {t.get('conversions', 0)}"
        )
        for p in (ga4.get("top_pages") or [])[:8]:
            lines.append(f"  page {p.get('path')}  -  {p.get('sessions')} sessions")
        lines.append("")

    if gsc and gsc.get("totals"):
        t = gsc["totals"]
        lines.append("=== Google Search Console (last 7 days) ===")
        lines.append(
            f"Clicks: {t.get('clicks', 0)}  "
            f"Impressions: {t.get('impressions', 0)}  "
            f"CTR: {(t.get('ctr', 0) or 0) * 100:.2f}%  "
            f"Avg position: {t.get('position', 0):.2f}"
        )
        for q in (gsc.get("top_queries") or [])[:10]:
            lines.append(
                f"  query '{q.get('query')}': "
                f"{q.get('clicks', 0)} clicks, "
                f"{q.get('impressions', 0)} impressions, "
                f"pos {q.get('position', 0):.1f}"
            )
        lines.append("")

    if rankings and rankings.get("results"):
        lines.append("=== BrightLocal local rankings ===")
        for r in (rankings.get("results") or [])[:10]:
            lines.append(
                f"  '{r.get('keyword')}': rank {r.get('rank') or 'unranked'} "
                f"on {r.get('search-engine') or r.get('engine') or 'google'}"
            )
        lines.append("")

    if site_facts:
        url = site_facts.get("url") or ""
        if url:
            lines.append(f"=== Site fetch ({url}) ===")
        pages = site_facts.get("pages") or []
        if pages:
            page = pages[0]
            html = (page.get("html") or "")[:8000]
            if html:
                lines.append(f"HTML excerpt (first {min(len(html), 8000)} chars):")
                lines.append(html)
        lines.append("")

    if not lines:
        return "(no source data available)"
    return "\n".join(lines)


def _build_system_prompt(tenant_id: str) -> str:
    return (
        "You are an SEO advisor for a local services business. Your output "
        "is consumed verbatim by a dashboard panel - the owner reads it. "
        "Recommendations must be specific, grounded in the source data "
        "below, and actionable in under an hour each. "
        "Never invent numbers. Never recommend hiring a consultant. "
        "Speak plainly; assume the owner is not technical. "
        f"Tenant: {tenant_id}.\n\n"
        + _OUTPUT_SCHEMA_REMINDER
    )


# ---------------------------------------------------------------------------
# synthesis
# ---------------------------------------------------------------------------


def synthesize(
    tenant_id: str,
    *,
    ga4: dict[str, Any] | None = None,
    gsc: dict[str, Any] | None = None,
    rankings: dict[str, Any] | None = None,
    site_facts: dict[str, Any] | None = None,
    chat_fn: Callable[..., Any] | None = None,
) -> list[dict[str, Any]]:
    """Pure function: take the four data sources, return a list of
    normalized + ranked Recommendation dicts.

    Returns [] on Opus error or unparseable response (logged).
    chat_fn is injectable for tests; defaults to opus.chat.
    """
    chatter = chat_fn or opus.chat

    data_block = _build_data_block(ga4=ga4, gsc=gsc, rankings=rankings, site_facts=site_facts)
    system = _build_system_prompt(tenant_id)
    user = (
        "Source data:\n\n"
        + data_block
        + "\n\nReturn the JSON array now."
    )

    try:
        result = chatter(
            tenant_id=tenant_id,
            messages=[{"role": "user", "content": user}],
            system=system,
            max_tokens=2400,
            temperature=0.3,
            kind="seo_recommendations",
            cache_system=True,
        )
    except Exception as exc:  # noqa: BLE001 - synthesis must never crash callers
        log.warning("seo_recommender chat failed for %s: %s", tenant_id, exc)
        return []

    text = getattr(result, "text", "") or ""
    raw_recs = _parse_json_response(text)
    if not raw_recs:
        return []

    normalized: list[dict[str, Any]] = []
    for idx, raw in enumerate(raw_recs[:MAX_RECS]):
        norm = _normalize_rec(raw, idx)
        if norm is not None:
            normalized.append(norm)
    return _rank(normalized)


def _summarize_sources(
    *,
    ga4: dict[str, Any] | None,
    gsc: dict[str, Any] | None,
    rankings: dict[str, Any] | None,
    site_facts: dict[str, Any] | None,
) -> dict[str, Any]:
    """Compact dict of which sources had real data, for the cache header."""
    return {
        "ga4_sessions": int(((ga4 or {}).get("totals") or {}).get("sessions", 0)),
        "gsc_clicks": int(((gsc or {}).get("totals") or {}).get("clicks", 0)),
        "rankings_count": len((rankings or {}).get("results") or []),
        "site_fetched": bool(site_facts and site_facts.get("pages")),
    }


def run_weekly(
    tenant_id: str,
    *,
    ga4: dict[str, Any] | None = None,
    gsc: dict[str, Any] | None = None,
    rankings: dict[str, Any] | None = None,
    site_facts: dict[str, Any] | None = None,
    synthesize_fn: Callable[..., list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Run synthesis with whatever data was collected upstream and write
    the result to the per-tenant cache. Returns
    {recommendations, source_summary, cache_path}.

    Designed to be called from a separate orchestrator that fetches
    GA4/GSC/BrightLocal/site itself (so the orchestrator owns the
    credentials + can fall back gracefully on per-source failure).
    """
    synth = synthesize_fn or synthesize
    recs = synth(
        tenant_id,
        ga4=ga4,
        gsc=gsc,
        rankings=rankings,
        site_facts=site_facts,
    )
    summary = _summarize_sources(ga4=ga4, gsc=gsc, rankings=rankings, site_facts=site_facts)
    path = _write_cache(tenant_id, recs, summary)
    return {
        "recommendations": recs,
        "source_summary": summary,
        "cache_path": str(path),
    }


__all__ = [
    "CACHE_FILENAME",
    "DEFAULT_TOP_N",
    "MAX_RECS",
    "get_cached",
    "get_cache_meta",
    "synthesize",
    "run_weekly",
]
