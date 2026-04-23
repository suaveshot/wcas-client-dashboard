"""
Real-Opus recommendations generator.

The Day 4 flagship pulled into Day 3 evening. Single Opus call against
the full tenant context that global_ask already composes (heartbeats +
decisions + goals + brand + KB + receipts summary). Asks the model to
return a JSON array of recommendation candidates that match the
ADR-024 schema. Every candidate flows through `recommendations.finalize`
so the same guardrail that gates seeded_recs gates these too.

Why a single direct Messages-API call (not a Managed Agent):
  - One-shot text-to-JSON is exactly what the Messages API is for (ADR-002).
  - No session lifecycle, no tool dispatch, no event history needed.
  - Predictable cost: one input-cache-friendly call per refresh.
  - Reuses every existing seam (cost tracker, scrubber, guardrails, finalize).

Why we keep seeded_recs as the fallback:
  - Empty / cold-start tenants get rule-based recs immediately (no model call).
  - Model unavailability (missing key, budget, parse failure) degrades to seeded.
  - Demo never goes blank.

Error contract:
  RecsGenerationError  -  the model produced something we couldn't parse as
  JSON-of-recs. Caller logs and falls back to seeded.
  opus.OpusBudgetExceeded / opus.OpusUnavailable propagate unchanged.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from . import global_ask, opus, recommendations

log = logging.getLogger("dashboard.recs_generator")


class RecsGenerationError(RuntimeError):
    """Model output could not be parsed as a JSON array of recommendations."""


_MAX_RECS = 7

# System prompt locks the schema. Cache-flagged in opus.chat so repeat
# refreshes within ~5 minutes hit the prompt cache (same schema text every call).
_SYSTEM_PROMPT = """You are an automation analyst embedded in a small-shop owner-operator's dashboard. Your job is to read the current state of the business (telemetry from every running pipeline, recent owner decisions, pinned goals, brand voice, knowledge base, and outbound receipts) and propose 0 to 7 concrete, evidence-grounded recommendations the owner could act on this week.

You return ONLY a JSON object with a single key "recommendations" whose value is an array of recommendation objects. No prose before or after. No markdown fences. If no recommendations are warranted, return {"recommendations": []}.

Each recommendation object MUST have these keys:
  goal           one of: "GROW LEADS" | "GROW REVIEWS" | "HEALTH" | "EFFICIENCY" | "COST"
  role_slug      the pipeline this rec is about, lower-snake (e.g. "ads", "reviews", "sales_pipeline")
  headline       <= 120 chars, plain English, no jargon, ends with a period
  reason         1 to 3 sentences explaining the finding with specific numbers
  proposed_tool  exactly one of: "update_tenant_config" | "queue_pipeline_run" | "create_activity" | "schedule_followup" | "set_schedule" | "set_preference" | "write_kb_entry" | "noop"
  proposed_args  JSON object of args to that tool. Use {} for noop.
  impact         object with keys "metric" (str), "estimate" (number), "unit" (str), "calculation" (str showing the math)
  confidence     integer 1 to 10. Anything under 6 will be filtered out, so do not propose what you cannot defend.
  reversibility  one of: "instant" | "session" | "slow" | "permanent"
  evidence       non-empty array of objects with keys "source", "datapoint", "value", "observed_at"
                 source is one of "heartbeat" | "decision" | "goal" | "kb" | "receipts" | "external"

Hard rules:
  - Do not use em dashes. Use periods or commas.
  - Do not use the words guaranteed, always, never, certain to, definitely, 100%.
  - Do not claim impact >= 100% lift / increase / boost.
  - Do not mention any AI vendor by name.
  - Cite specific pipeline ids, timestamps, and numbers from the evidence. Do not invent values.
  - If evidence is thin for a candidate, lower the confidence rather than over-claim.
  - Prefer tight, surgical recs to vague ones.

Output shape (no other keys, no commentary):
{
  "recommendations": [
    {
      "goal": "HEALTH",
      "role_slug": "ads",
      "headline": "Ads has been erroring for 9 days.",
      "reason": "The last run failed with 'OAuth token expired' on 2026-04-14. A reconnect usually clears this.",
      "proposed_tool": "queue_pipeline_run",
      "proposed_args": {"pipeline_id": "ads"},
      "impact": {"metric": "health_restoration", "estimate": 1, "unit": "pipeline restored", "calculation": "9 days of missed runs at daily cadence"},
      "confidence": 9,
      "reversibility": "instant",
      "evidence": [{"source": "heartbeat", "datapoint": "status", "value": "error", "observed_at": "2026-04-14T07:01:22Z"}]
    }
  ]
}
"""


def _strip_fences(text: str) -> str:
    """Tolerate ```json ... ``` or ``` ... ``` wrappers around the JSON."""
    s = (text or "").strip()
    if not s:
        return s
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", s, re.DOTALL | re.IGNORECASE)
    if fence:
        return fence.group(1).strip()
    return s


def _parse_recs(text: str) -> list[dict[str, Any]]:
    """Parse the model output into a list of rec dicts.

    Accepts either {"recommendations": [...]} or a bare [...] for resilience.
    Raises RecsGenerationError on anything else.
    """
    cleaned = _strip_fences(text)
    if not cleaned:
        raise RecsGenerationError("empty model output")
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise RecsGenerationError(f"json parse failed: {exc}") from exc

    if isinstance(payload, dict) and "recommendations" in payload:
        recs = payload.get("recommendations")
    elif isinstance(payload, list):
        recs = payload
    else:
        raise RecsGenerationError("unexpected json shape (expected object with 'recommendations' or a bare array)")

    if not isinstance(recs, list):
        raise RecsGenerationError("'recommendations' is not a list")

    out: list[dict[str, Any]] = []
    for item in recs[:_MAX_RECS]:
        if isinstance(item, dict):
            out.append(item)
    return out


def generate(tenant_id: str, *, model: str | None = None) -> dict[str, Any]:
    """Run a single Opus call against the tenant's full state and return
    finalized recommendations plus model/cost metadata.

    Returns dict with:
      recs           list of finalized rec dicts (live + draft, both stamped)
      model          model name actually used
      usd            cost of this call
      input_tokens   prompt tokens consumed
      output_tokens  completion tokens generated

    Callers separate live (`draft=False`) from drafts (`draft=True`) at the
    surface layer. The home page renders only live; the /recommendations
    admin tab renders drafts.
    """
    context = global_ask.compose_context(tenant_id)
    user_content = (
        "Here is the current state of this business.\n\n"
        f"{context['prompt']}\n\n"
        "---\n\n"
        f"Return up to {_MAX_RECS} recommendations as JSON in the exact shape locked above. "
        "Return {\"recommendations\": []} if nothing warrants action."
    )

    picked_model = model or os.getenv("WCAS_RECS_MODEL") or None
    result = opus.chat(
        tenant_id=tenant_id,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
        model=picked_model,
        max_tokens=4096,
        temperature=0.3,
        kind="recommendations",
        note="recs_generator.refresh",
        cache_system=True,
    )

    candidates = _parse_recs(result.text)
    finalized = [recommendations.finalize(tenant_id, rec) for rec in candidates]

    return {
        "recs": finalized,
        "model": result.model,
        "usd": result.usd,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
    }
