"""Tests for dashboard_app.services.crm_detect."""

from __future__ import annotations

import os
from typing import Any

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import pytest

from dashboard_app.services import (
    activation_tools,
    credentials as _credentials,
    crm_detect,
)


# ---------------------------------------------------------------------------
# fake httpx.get
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, body: str = "", headers: dict[str, str] | None = None,
                 final_url: str = "https://acme.test/"):
        self.text = body
        self.headers = headers or {}
        self.url = final_url


def _http(body: str = "", headers: dict[str, str] | None = None,
          final_url: str = "https://acme.test/"):
    def fn(url: str, *, timeout: float = 8.0) -> _FakeResp:
        return _FakeResp(body=body, headers=headers, final_url=final_url)
    return fn


def _http_raises(exc: Exception):
    def fn(url: str, *, timeout: float = 8.0) -> _FakeResp:
        raise exc
    return fn


# ---------------------------------------------------------------------------
# fingerprint detection
# ---------------------------------------------------------------------------


def test_detects_ghl_via_msgsndr_fingerprint():
    out = crm_detect.detect(
        "https://acme.test/",
        http_get=_http(body='<script src="https://msgsndr.com/widget.js"></script>'),
    )
    assert out["detected"] == "ghl"
    assert out["confidence"] == "high"
    assert out["supported"] is True
    assert any("msgsndr.com" in s for s in out["signals"])


def test_detects_ghl_via_leadconnector_fingerprint():
    out = crm_detect.detect(
        "https://acme.test/",
        http_get=_http(body='<script src="https://leadconnectorhq.com/x.js"></script>'),
    )
    assert out["detected"] == "ghl"


def test_detects_hubspot_via_hs_scripts():
    out = crm_detect.detect(
        "https://acme.test/",
        http_get=_http(body='<script src="https://js.hs-scripts.com/123.js"></script>'),
    )
    assert out["detected"] == "hubspot"
    assert out["confidence"] == "high"
    # HubSpotProvider shipped in Phase 2E - hubspot is now actionable.
    assert out["supported"] is True


def test_detects_hubspot_via_response_header():
    out = crm_detect.detect(
        "https://acme.test/",
        http_get=_http(body="", headers={"X-Hubspot-Trace": "abc"}),
    )
    assert out["detected"] == "hubspot"


def test_detects_pipedrive():
    out = crm_detect.detect(
        "https://acme.test/",
        http_get=_http(body='<script src="https://leadbooster-chat.pipedrive.com/x.js"></script>'),
    )
    assert out["detected"] == "pipedrive"


def test_detects_intercom():
    out = crm_detect.detect(
        "https://acme.test/",
        http_get=_http(body='widget.intercom.io/widget/abc'),
    )
    assert out["detected"] == "intercom"


def test_detects_calendly():
    out = crm_detect.detect(
        "https://acme.test/",
        http_get=_http(body='<script src="https://assets.calendly.com/x.js"></script>'),
    )
    assert out["detected"] == "calendly"


def test_detects_salesforce():
    out = crm_detect.detect(
        "https://acme.test/",
        http_get=_http(body='<div class="force.com/embeddedservice"></div>'),
    )
    assert out["detected"] == "salesforce"


def test_detects_zoho():
    out = crm_detect.detect(
        "https://acme.test/",
        http_get=_http(body='<script src="https://salesiq.zoho.com/x.js"></script>'),
    )
    assert out["detected"] == "zoho"


# ---------------------------------------------------------------------------
# multi-match + tie-breaking
# ---------------------------------------------------------------------------


def test_ghl_wins_when_multiple_match():
    """GHL is both CRM and comms; prefer it when both GHL + Calendly fire."""
    out = crm_detect.detect(
        "https://acme.test/",
        http_get=_http(body=(
            "msgsndr.com/widget.js "
            "assets.calendly.com/embed.js"
        )),
    )
    assert out["detected"] == "ghl"
    assert out["confidence"] == "medium"
    assert "calendly" in out["candidates"]
    assert "ghl" in out["candidates"]


def test_first_candidate_wins_when_no_ghl():
    """Two non-GHL CRMs both match - earliest in fingerprint order wins."""
    out = crm_detect.detect(
        "https://acme.test/",
        http_get=_http(body=(
            "js.hs-scripts.com/x.js "
            "leadbooster-chat.pipedrive.com/y.js"
        )),
    )
    # _FINGERPRINTS lists hubspot before pipedrive.
    assert out["detected"] == "hubspot"
    assert out["confidence"] == "medium"


# ---------------------------------------------------------------------------
# stored credentials override site signals
# ---------------------------------------------------------------------------


def test_stored_ghl_credentials_force_high_confidence(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _credentials.store_paste(
        "acme",
        "ghl",
        {"api_key": "tk", "location_id": "loc"},
    )
    # Site shows nothing CRM-shaped.
    out = crm_detect.detect(
        "https://acme.test/",
        tenant_id="acme",
        http_get=_http(body="<html>plain site</html>"),
    )
    assert out["detected"] == "ghl"
    assert out["confidence"] == "high"
    assert any("credentials stored" in s for s in out["signals"])


def test_credentials_override_site_signals(tmp_path, monkeypatch):
    """Tenant has GHL credentials but the site shows HubSpot; credentials win."""
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _credentials.store_paste(
        "acme",
        "ghl",
        {"api_key": "tk", "location_id": "loc"},
    )
    out = crm_detect.detect(
        "https://acme.test/",
        tenant_id="acme",
        http_get=_http(body='<script src="https://js.hs-scripts.com/123.js"></script>'),
    )
    assert out["detected"] == "ghl"
    # Both CRMs surface as candidates so the orchestrator can flag the conflict.
    assert "ghl" in out["candidates"]
    assert "hubspot" in out["candidates"]


def test_stored_hubspot_credentials_force_high_confidence(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _credentials.store_paste("acme", "hubspot", {"access_token": "pat-xyz"})
    out = crm_detect.detect(
        "https://acme.test/",
        tenant_id="acme",
        http_get=_http(body="<html>plain</html>"),
    )
    assert out["detected"] == "hubspot"
    assert out["confidence"] == "high"
    assert out["supported"] is True
    assert "HubSpotProvider" in out["recommendation"]


def test_stored_pipedrive_credentials_force_high_confidence(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _credentials.store_paste(
        "acme",
        "pipedrive",
        {"api_token": "pd-xyz", "company_domain": "acme-llc"},
    )
    out = crm_detect.detect(
        "https://acme.test/",
        tenant_id="acme",
        http_get=_http(body="<html>plain</html>"),
    )
    assert out["detected"] == "pipedrive"
    assert out["confidence"] == "high"
    assert out["supported"] is True
    assert "PipedriveProvider" in out["recommendation"]


def test_pipedrive_credentials_without_company_domain_skipped(tmp_path, monkeypatch):
    """Pipedrive needs both token AND company_domain to be usable."""
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _credentials.store_paste("acme", "pipedrive", {"api_token": "pd-xyz"})
    out = crm_detect.detect(
        "https://acme.test/",
        tenant_id="acme",
        http_get=_http(body="<html>plain</html>"),
    )
    assert out["detected"] != "pipedrive"


def test_partial_credentials_do_not_count(tmp_path, monkeypatch):
    """Missing location_id means we can't actually call GHL."""
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _credentials.store_paste("acme", "ghl", {"api_key": "tk"})
    out = crm_detect.detect(
        "https://acme.test/",
        tenant_id="acme",
        http_get=_http(body="<html>plain site</html>"),
    )
    assert out["detected"] != "ghl"


# ---------------------------------------------------------------------------
# negative paths
# ---------------------------------------------------------------------------


def test_no_signals_returns_none_with_medium_confidence():
    """We hit the site, found nothing - that's a real 'no CRM' answer."""
    out = crm_detect.detect(
        "https://acme.test/",
        http_get=_http(body="<html><body>marketing copy</body></html>"),
    )
    assert out["detected"] == "none"
    assert out["confidence"] == "medium"
    assert out["candidates"] == []


def test_fetch_failure_returns_unknown_low_confidence():
    import httpx
    out = crm_detect.detect(
        "https://acme.test/",
        http_get=_http_raises(httpx.ConnectError("boom")),
    )
    assert out["detected"] == "unknown"
    assert out["confidence"] == "low"
    assert any("fetch failed" in s for s in out["signals"])


def test_blank_url_returns_unknown():
    out = crm_detect.detect("")
    assert out["detected"] == "unknown"
    assert out["confidence"] == "low"


def test_url_without_scheme_is_normalized():
    captured: dict = {}

    def fn(url: str, *, timeout: float = 8.0) -> _FakeResp:
        captured["url"] = url
        return _FakeResp(body="msgsndr.com")

    out = crm_detect.detect("acme.test", http_get=fn)
    assert captured["url"].startswith("https://")
    assert out["detected"] == "ghl"


# ---------------------------------------------------------------------------
# recommendation strings
# ---------------------------------------------------------------------------


def test_recommendation_for_ghl_mentions_provider():
    out = crm_detect.detect(
        "https://acme.test/",
        http_get=_http(body="msgsndr.com"),
    )
    assert "GHLProvider" in out["recommendation"]


def test_recommendation_for_none_offers_setup():
    out = crm_detect.detect(
        "https://acme.test/",
        http_get=_http(body="<html>plain</html>"),
    )
    assert "No CRM" in out["recommendation"] or "no CRM" in out["recommendation"].lower()


def test_recommendation_for_unknown_asks_owner():
    import httpx
    out = crm_detect.detect(
        "https://acme.test/",
        http_get=_http_raises(httpx.ConnectError("boom")),
    )
    assert "owner" in out["recommendation"].lower()


# ---------------------------------------------------------------------------
# activation_tools dispatch
# ---------------------------------------------------------------------------


def test_detect_crm_tool_is_registered():
    assert "detect_crm" in activation_tools.HANDLERS
    schemas = {s["name"] for s in activation_tools.TOOL_SCHEMAS}
    assert "detect_crm" in schemas


def test_detect_crm_tool_dispatches(monkeypatch):
    captured: dict = {}

    def fake_detect(url: str, *, tenant_id: str | None = None, http_get=None) -> dict:
        captured["url"] = url
        captured["tenant_id"] = tenant_id
        return {"detected": "ghl", "confidence": "high", "signals": [],
                "candidates": ["ghl"], "recommendation": "ok", "supported": True}

    monkeypatch.setattr(crm_detect, "detect", fake_detect)
    ok, payload = activation_tools.dispatch(
        "acme",
        "detect_crm",
        {"url": "https://acme.test/"},
    )
    assert ok is True
    assert payload["detected"] == "ghl"
    assert captured["url"] == "https://acme.test/"
    assert captured["tenant_id"] == "acme"


def test_detect_crm_tool_requires_url():
    ok, payload = activation_tools.dispatch("acme", "detect_crm", {})
    assert ok is False
    assert "url" in payload.get("error", "").lower()
