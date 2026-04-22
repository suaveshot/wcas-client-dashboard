"""
Recommendation generator and guardrail-reviewer.

Call shape:

    candidate = build_candidate(
        tenant_id="americal_patrol",
        evidence_bundle={...}   # assembled upstream from heartbeat + Airtable
    )
    approved, reason = guardrails.review_recommendation(tenant_id, candidate)
    if approved:
        surface_to_client(candidate)
    else:
        stash_for_sam_review(candidate, reason)

This module defines the canonical recommendation dict shape that the
generator must produce and the guardrail knows how to inspect. Keep
this schema stable; adding optional keys is fine, removing required
ones is not.

Required keys:
  id              stable string (hash of tenant+role+headline)
  goal            "GROW LEADS" | "GROW REVIEWS" | "HEALTH" | "EFFICIENCY" | "COST"
  role_slug       the pipeline the rec is about (e.g. "ads", "reviews")
  headline        <= 120 char one-line finding, in plain English
  reason          1-3 sentence explanation with numbers
  proposed_tool   tool name from guardrails._KNOWN_SAFE_TOOLS or "noop"
  proposed_args   JSON-serializable dict of args to that tool
  impact          {"metric": str, "estimate": number, "unit": str, "calculation": str}
  confidence      int 1-10
  reversibility   "instant" | "session" | "slow" | "permanent"
  evidence        [{"source": "airtable|heartbeat|event_bus|external",
                   "datapoint": str, "value": any, "observed_at": iso8601}, ...]

Optional keys:
  external_sources   list of URLs backing any base-rate / industry claims
  related_rec_ids    past recs that informed this one
  draft              True if surfacing was blocked by guardrails
  draft_reason       reason from guardrail review_recommendation
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from . import guardrails


def rec_id(tenant_id: str, role_slug: str, headline: str) -> str:
    seed = f"{tenant_id}|{role_slug}|{headline}".encode("utf-8")
    return hashlib.sha256(seed).hexdigest()[:16]


def finalize(tenant_id: str, candidate: dict[str, Any]) -> dict[str, Any]:
    """
    Ensure id + draft fields are set. Run the guardrail. Callers dispatch
    either to the client surface (approved) or Sam's admin queue (drafts).
    """
    if "id" not in candidate:
        candidate["id"] = rec_id(
            tenant_id,
            candidate.get("role_slug", "unknown"),
            candidate.get("headline", ""),
        )
    approved, reason = guardrails.review_recommendation(tenant_id, candidate)
    if not approved:
        candidate["draft"] = True
        candidate["draft_reason"] = reason
    else:
        candidate.setdefault("draft", False)
    return candidate


def to_json(rec: dict[str, Any]) -> str:
    """Stable JSON for writing to dashboard_decisions.jsonl."""
    return json.dumps(rec, sort_keys=True, default=str)
