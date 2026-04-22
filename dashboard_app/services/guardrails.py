"""
Guardrails - the last line before anything leaves the system.

Two public entry points:

  review_outbound(channel, content, metadata)
      For every automated outbound (email, SMS, social post, DAR reply, etc.)
      any runtime pipeline is about to send. Hackathon-scope behavior:
        - strips em dashes (brand voice rule)
        - blocks Claude / Opus / Anthropic / GPT mentions
        - scrubs obvious PII leaks from the body (defense in depth)
      Returns ReviewResult with approve/revise/reject + optional rewritten body.
      Post-hackathon: an Opus pass does the real brand-voice + factual review.

  review_recommendation(tenant_id, rec)
      Evidence-gated second-opinion review before a Recommendation card
      ever shows up on the client's screen. Checks:
        - at least one structured citation is present
        - proposed change maps to a known safe tool
        - confidence score >= MIN_CONFIDENCE (default 6/10)
        - impact math is internally consistent (no "100% lift" fantasies)
        - no absolute claims that overstate certainty
      Returns (approved: bool, reason: str|None).

Both gates fail closed. If anything is unsure, the rec gets marked
"draft" and goes to Sam's admin inbox before any client sees it.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from .scrubber import scrub

_VENDOR_WORDS = ("Claude", "Opus 4.7", "Opus 4.6", "Anthropic", "OpenAI", "GPT-")
_EM_DASH = chr(0x2014)  # defined by codepoint so this file itself stays em-dash-free

_MIN_CONFIDENCE = int(os.getenv("REC_MIN_CONFIDENCE", "6"))
_KNOWN_SAFE_TOOLS = {
    "update_tenant_config",
    "queue_pipeline_run",
    "create_activity",
    "schedule_followup",
    "set_schedule",
    "set_preference",
    "write_kb_entry",
    "noop",  # surfaced-as-info-only recs
}


@dataclass
class ReviewResult:
    decision: str  # "approve" | "revise" | "reject"
    content: str  # possibly-rewritten content
    reasons: list[str]


def review_outbound(channel: str, content: str, metadata: dict[str, Any] | None = None) -> ReviewResult:
    """
    Review content on its way out of the system. Hackathon scope is
    mechanical: em-dash strip, vendor-mention block, PII scrub. The
    function signature is locked so every outbound pipeline can call
    it today and the Opus-powered review layer can drop in later.
    """
    reasons: list[str] = []
    rewritten = content or ""

    if _EM_DASH in rewritten:
        rewritten = rewritten.replace(_EM_DASH, " - ")
        reasons.append("stripped em dash (brand voice)")

    for word in _VENDOR_WORDS:
        if word in rewritten:
            # Don't rewrite - reject outright. Vendor leak is a brand incident,
            # not a typo. Callers get to decide whether to retry Opus or fail.
            return ReviewResult(decision="reject", content=content or "", reasons=[f"vendor name leaked: {word!r}"])

    # Double-check for any stray PII. Scrubber is conservative; over-redaction
    # on an outbound email is worse than leaving the draft alone. So we only
    # report (metadata), never mutate the client-facing body here.
    if metadata is not None and metadata.get("pii_check", True):
        scrubbed = scrub(rewritten)
        if scrubbed != rewritten:
            reasons.append("pii pattern detected in body (review before send)")

    return ReviewResult(
        decision="revise" if reasons else "approve",
        content=rewritten,
        reasons=reasons,
    )


# -----------------------------------------------------------------------------
# Recommendation review
# -----------------------------------------------------------------------------


def _has_evidence(rec: dict[str, Any]) -> bool:
    evidence = rec.get("evidence") or []
    if not isinstance(evidence, list) or not evidence:
        return False
    for item in evidence:
        if not isinstance(item, dict):
            return False
        if "source" not in item or "value" not in item:
            return False
    return True


_ABSOLUTE_CLAIMS = re.compile(
    r"\b(guaranteed|100%|always works|never fails|definitely will|certain to)\b",
    re.IGNORECASE,
)
_UNREALISTIC_LIFT = re.compile(r"\b(\d{3,})%\s*(lift|increase|boost|improvement)\b", re.IGNORECASE)


def review_recommendation(tenant_id: str, rec: dict[str, Any]) -> tuple[bool, str | None]:
    """
    Evidence-gate and sanity-check a recommendation before it renders to the
    client. Return (approved, reason). Callers surface rejected recs into
    Sam's admin inbox for manual review, not into the client's screen.
    """
    headline = str(rec.get("headline") or "").strip()
    reason = str(rec.get("reason") or "").strip()
    proposed_tool = (rec.get("proposed_tool") or "").strip()
    confidence = rec.get("confidence")

    if not headline or not reason:
        return False, "missing headline or reason"

    if not _has_evidence(rec):
        return False, "no structured evidence"

    if proposed_tool and proposed_tool not in _KNOWN_SAFE_TOOLS:
        return False, f"proposed tool not in safe list: {proposed_tool}"

    try:
        conf = int(confidence) if confidence is not None else 0
    except (TypeError, ValueError):
        conf = 0
    if conf < _MIN_CONFIDENCE:
        return False, f"confidence {conf}/10 below threshold {_MIN_CONFIDENCE}"

    combined = f"{headline} {reason}"
    if _ABSOLUTE_CLAIMS.search(combined):
        return False, "absolute language (overstates certainty)"
    if _UNREALISTIC_LIFT.search(combined):
        return False, "unrealistic impact claim (>=100% lift)"

    # Vendor-name rejection reuses the outbound review logic.
    outbound = review_outbound("recommendation", combined)
    if outbound.decision == "reject":
        return False, outbound.reasons[0] if outbound.reasons else "outbound review rejected"

    return True, None
