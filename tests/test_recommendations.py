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


# ---------------------------------------------------------------------------
# W3: API apply endpoint dispatches via services.dispatch
# (closes audits/phase0_recommendations.md::F1)
# ---------------------------------------------------------------------------


def _api_seed_rec(tenant_id, rec):
    from dashboard_app.services import recs_store
    recs_store.write_today(tenant_id, recs=[rec], model="claude-test", usd=0.0)


def _api_signed_cookie(tenant_id="acme"):
    from dashboard_app.services import sessions
    return sessions.issue(tenant_id=tenant_id, email="owner@acme.com", role="client")


def test_api_apply_dispatches_review_reply_draft(tmp_path, monkeypatch):
    """Applying a review_reply_draft rec creates an outgoing draft via the
    reference rec handler. Dispatch outcome surfaces in the response."""
    from fastapi.testclient import TestClient
    from dashboard_app.main import app
    from dashboard_app.services import outgoing_queue

    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _api_seed_rec("acme", {
        "id": "rec-r1",
        "proposed_tool": "review_reply_draft",
        "title": "Reply to Maria",
        "review": {"reviewer": "Maria Sanchez", "stars": 5},
        "draft_body": "Thank you Maria!",
    })

    client = TestClient(app)
    cookie = _api_signed_cookie("acme")
    resp = client.post(
        "/api/recommendations/rec-r1/act",
        json={"action": "apply"},
        cookies={"wcas_session": cookie},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["action"] == "apply"
    assert payload["dispatch"]["ok"] is True
    assert payload["dispatch"]["outcome"]["draft_id"]
    pending = outgoing_queue.list_pending("acme")
    assert len(pending) == 1
    assert pending[0]["pipeline_id"] == "reviews"


def test_api_apply_returns_queued_for_review_for_unknown_proposed_tool(tmp_path, monkeypatch):
    """Recs with proposed_tool not in REC_HANDLERS get queued_for_review per
    the audit's honest-stub recommendation."""
    from fastapi.testclient import TestClient
    from dashboard_app.main import app

    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _api_seed_rec("acme", {
        "id": "rec-x",
        "proposed_tool": "schedule_change",  # not yet implemented
        "title": "Move blog day to Wednesday",
    })

    client = TestClient(app)
    cookie = _api_signed_cookie("acme")
    resp = client.post(
        "/api/recommendations/rec-x/act",
        json={"action": "apply"},
        cookies={"wcas_session": cookie},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["dispatch"]["ok"] is True
    assert payload["dispatch"]["outcome"]["queued_for_review"] is True


def test_api_dismiss_skips_dispatch(tmp_path, monkeypatch):
    """Dismiss action records intent only - no dispatch outcome in response."""
    from fastapi.testclient import TestClient
    from dashboard_app.main import app

    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _api_seed_rec("acme", {
        "id": "rec-d",
        "proposed_tool": "review_reply_draft",
        "title": "x",
    })

    client = TestClient(app)
    cookie = _api_signed_cookie("acme")
    resp = client.post(
        "/api/recommendations/rec-d/act",
        json={"action": "dismiss"},
        cookies={"wcas_session": cookie},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["action"] == "dismiss"
    assert "dispatch" not in payload  # only Apply triggers dispatch


def test_api_apply_skips_when_paused(tmp_path, monkeypatch):
    import json as _json
    from fastapi.testclient import TestClient
    from dashboard_app.main import app
    from dashboard_app.services import heartbeat_store

    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    config_path = heartbeat_store.tenant_root("acme") / "tenant_config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(_json.dumps({"status": "paused"}), encoding="utf-8")

    _api_seed_rec("acme", {"id": "rec-p", "proposed_tool": "review_reply_draft", "title": "x"})

    client = TestClient(app)
    cookie = _api_signed_cookie("acme")
    resp = client.post(
        "/api/recommendations/rec-p/act",
        json={"action": "apply"},
        cookies={"wcas_session": cookie},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["dispatch"]["ok"] is False
    assert payload["dispatch"]["reason"] == "tenant_paused"
