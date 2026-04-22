"""Guardrail + recommendation-shape tests."""

import os

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

from dashboard_app.services import guardrails, recommendations


def _good_rec():
    return {
        "goal": "GROW REVIEWS",
        "role_slug": "ads",
        "headline": "Ads is pacing 18% under goal this month",
        "reason": "Nine keyword drops since Monday. Raising the CPC cap on Brand by $0.40 should recover the lost traffic within a week.",
        "proposed_tool": "update_tenant_config",
        "proposed_args": {"path": "ads.brand.cpc_cap", "value": 2.40},
        "impact": {"metric": "brand_impressions", "estimate": 12, "unit": "%",
                   "calculation": "3-week avg impressions 820/day; 18% lift = ~148 more/day"},
        "confidence": 7,
        "reversibility": "instant",
        "evidence": [
            {"source": "heartbeat", "datapoint": "ads.pacing_pct", "value": -0.18, "observed_at": "2026-04-22T08:00:00Z"},
            {"source": "airtable", "datapoint": "Deals.first_touch_source=ads_brand", "value": 9, "observed_at": "2026-04-22T08:00:00Z"},
        ],
    }


def test_good_rec_approves():
    ok, reason = guardrails.review_recommendation("acme", _good_rec())
    assert ok is True, reason


def test_rec_rejects_when_no_evidence():
    rec = _good_rec()
    rec["evidence"] = []
    ok, reason = guardrails.review_recommendation("acme", rec)
    assert ok is False
    assert "evidence" in (reason or "")


def test_rec_rejects_unsafe_tool():
    rec = _good_rec()
    rec["proposed_tool"] = "drop_all_tables"
    ok, reason = guardrails.review_recommendation("acme", rec)
    assert ok is False


def test_rec_rejects_low_confidence():
    rec = _good_rec()
    rec["confidence"] = 3
    ok, reason = guardrails.review_recommendation("acme", rec)
    assert ok is False
    assert "confidence" in (reason or "")


def test_rec_rejects_absolute_language():
    rec = _good_rec()
    rec["reason"] = "This is guaranteed to 100% lift opens by Monday."
    ok, reason = guardrails.review_recommendation("acme", rec)
    assert ok is False


def test_rec_rejects_vendor_leak():
    rec = _good_rec()
    rec["headline"] = "Claude thinks Ads is pacing under goal"
    ok, reason = guardrails.review_recommendation("acme", rec)
    assert ok is False


def test_outbound_strips_em_dash():
    em = chr(0x2014)  # avoid literal em dash in source so the em-dash test stays green
    result = guardrails.review_outbound("email", f"Hey sam {em} quick note.")
    assert em not in result.content
    assert result.decision == "revise"


def test_outbound_rejects_vendor_mention():
    result = guardrails.review_outbound("email", "Powered by Claude.")
    assert result.decision == "reject"


def test_recommendations_finalize_sets_id_and_draft():
    rec = _good_rec()
    final = recommendations.finalize("acme", rec)
    assert "id" in final
    assert final.get("draft") is False


def test_recommendations_finalize_marks_draft_when_low_confidence():
    rec = _good_rec()
    rec["confidence"] = 2
    final = recommendations.finalize("acme", rec)
    assert final.get("draft") is True
    assert final.get("draft_reason")
