"""Tests for dashboard_app.services.hubspot_provider.

Pinned method-by-method against the CRMProvider Protocol so the W6
incomplete-method-surface lesson never repeats. Vendor-unsupported
operations (SMS, cancel) raise / return False explicitly.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import pytest

from dashboard_app.services import (
    credentials as _credentials,
    crm_provider,
    hubspot_provider,
)


# ---------------------------------------------------------------------------
# fake HTTP session (mirrors the GHL test pattern)
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload: Any | None = None,
                 text: str | None = None):
        self.status_code = status_code
        self._payload = payload
        if text is not None:
            self.text = text
        elif payload is None:
            self.text = ""
        else:
            self.text = json.dumps(payload)

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("no body")
        return self._payload


class _FakeSession:
    def __init__(self, queue: list[_FakeResponse] | None = None):
        self.queue: list[_FakeResponse] = queue or []
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.queue:
            return _FakeResponse(200, {})
        return self.queue.pop(0)


def _provider(session: _FakeSession, *, from_email: str | None = "sam@example.com"):
    return hubspot_provider.HubSpotProvider(
        access_token="pat-test-token",
        from_email=from_email,
        session=session,
    )


# ---------------------------------------------------------------------------
# protocol shape
# ---------------------------------------------------------------------------


def test_hubspot_provider_satisfies_crm_protocol():
    p = _provider(_FakeSession())
    assert isinstance(p, crm_provider.CRMProvider)


def test_every_protocol_method_is_implemented():
    p = _provider(_FakeSession())
    expected = {
        "list_contacts",
        "get_contact",
        "update_contact",
        "search_conversations",
        "get_conversation_messages",
        "get_full_conversation_history",
        "send_email",
        "send_sms",
        "cancel_scheduled_message",
        "has_viewed_estimate",
        "search_opportunities",
        "update_opportunity_stage",
    }
    for name in expected:
        assert callable(getattr(p, name, None)), f"HubSpotProvider missing {name!r}"


def test_constructor_rejects_blank_token():
    with pytest.raises(ValueError):
        hubspot_provider.HubSpotProvider(access_token="")


# ---------------------------------------------------------------------------
# auth headers
# ---------------------------------------------------------------------------


def test_each_request_sends_bearer_token():
    s = _FakeSession([_FakeResponse(200, {"id": "c1"})])
    p = _provider(s)
    p.get_contact("c1")
    assert s.calls[0]["headers"]["Authorization"] == "Bearer pat-test-token"


# ---------------------------------------------------------------------------
# contacts
# ---------------------------------------------------------------------------


def test_list_contacts_paginates_via_after_cursor():
    s = _FakeSession([
        _FakeResponse(200, {
            "results": [{"id": "1"}, {"id": "2"}],
            "paging": {"next": {"after": "ABC"}},
        }),
        _FakeResponse(200, {
            "results": [{"id": "3"}],
            # No paging.next on the last page.
        }),
    ])
    p = _provider(s)
    out = p.list_contacts(page_size=50)
    assert [c["id"] for c in out] == ["1", "2", "3"]
    assert s.calls[1]["params"]["after"] == "ABC"


def test_list_contacts_caps_page_size_at_100():
    s = _FakeSession([_FakeResponse(200, {"results": []})])
    p = _provider(s)
    p.list_contacts(page_size=999)
    assert s.calls[0]["params"]["limit"] == 100


def test_get_contact_returns_raw_dict():
    s = _FakeSession([_FakeResponse(200, {
        "id": "c1",
        "properties": {"firstname": "Sam"},
    })])
    p = _provider(s)
    out = p.get_contact("c1")
    assert out["id"] == "c1"
    assert out["properties"]["firstname"] == "Sam"


def test_update_contact_wraps_bare_properties():
    s = _FakeSession([_FakeResponse(200, {"id": "c1"})])
    p = _provider(s)
    p.update_contact("c1", {"firstname": "Updated"})
    assert s.calls[0]["method"] == "PATCH"
    assert s.calls[0]["json"] == {"properties": {"firstname": "Updated"}}


def test_update_contact_passes_pre_wrapped_properties():
    s = _FakeSession([_FakeResponse(200, {"id": "c1"})])
    p = _provider(s)
    p.update_contact("c1", {"properties": {"firstname": "X"}})
    assert s.calls[0]["json"] == {"properties": {"firstname": "X"}}


# ---------------------------------------------------------------------------
# conversations
# ---------------------------------------------------------------------------


def test_search_conversations_filters_by_contact():
    s = _FakeSession([_FakeResponse(200, {"results": [{"id": "t1"}]})])
    p = _provider(s)
    out = p.search_conversations("c1")
    assert out == [{"id": "t1"}]
    assert s.calls[0]["params"]["associatedContactId"] == "c1"


def test_get_conversation_messages_unwraps_results():
    s = _FakeSession([_FakeResponse(200, {"results": [{"id": "m1"}]})])
    p = _provider(s)
    msgs = p.get_conversation_messages("t1")
    assert msgs == [{"id": "m1"}]


def test_get_full_conversation_history_sorts_chronologically():
    s = _FakeSession([
        _FakeResponse(200, {"results": [{"id": "tA"}, {"id": "tB"}]}),
        _FakeResponse(200, {"results": [
            {"id": "m2", "createdAt": "2026-04-29T12:00:00Z", "text": "two"},
        ]}),
        _FakeResponse(200, {"results": [
            {"id": "m1", "createdAt": "2026-04-28T12:00:00Z", "text": "one"},
            {"id": "m3", "createdAt": "2026-04-30T12:00:00Z", "text": "three"},
        ]}),
    ])
    p = _provider(s)
    history = p.get_full_conversation_history("c1")
    assert [m["body"] for m in history] == ["one", "two", "three"]


# ---------------------------------------------------------------------------
# send_email / send_sms / cancel
# ---------------------------------------------------------------------------


def test_send_email_posts_single_send_payload():
    s = _FakeSession([_FakeResponse(200, {"eventId": {"id": "evt1"}})])
    p = _provider(s)
    out = p.send_email("c1", "Hello", "<p>hi</p>")
    assert out == "evt1"
    body = s.calls[0]["json"]
    assert body["message"]["from"] == "sam@example.com"
    assert body["message"]["html"] == "<p>hi</p>"


def test_send_email_includes_send_at_for_scheduled():
    s = _FakeSession([_FakeResponse(200, {"eventId": {"id": "evt2"}})])
    p = _provider(s)
    when = datetime(2026, 4, 30, 12, 0, tzinfo=timezone.utc)
    p.send_email("c1", "S", "<p>b</p>", scheduled_at=when)
    assert s.calls[0]["json"]["sendAt"] == when.isoformat()


def test_send_email_without_from_email_raises():
    s = _FakeSession([_FakeResponse(200, {"eventId": {"id": "x"}})])
    p = _provider(s, from_email=None)
    with pytest.raises(hubspot_provider.HubSpotProviderError):
        p.send_email("c1", "S", "<p>b</p>")


def test_send_sms_raises_explicit_not_supported():
    """HubSpot has no native SMS - the provider must raise so the caller
    can route to Twilio instead of silently swallowing the message."""
    p = _provider(_FakeSession())
    with pytest.raises(hubspot_provider.HubSpotProviderError) as ei:
        p.send_sms("c1", "ping")
    assert "SMS" in str(ei.value)


def test_cancel_scheduled_message_returns_false_no_op():
    """HubSpot has no cancel endpoint - return False rather than raise."""
    p = _provider(_FakeSession())
    assert p.cancel_scheduled_message("evt1") is False


# ---------------------------------------------------------------------------
# estimates / quotes
# ---------------------------------------------------------------------------


def test_has_viewed_estimate_signed_status_returns_viewed():
    s = _FakeSession([_FakeResponse(200, {
        "id": "q1",
        "properties": {"hs_quote_status": "SIGNED",
                       "hs_lastmodifieddate": "2026-04-29T12:00:00Z"},
    })])
    p = _provider(s)
    out = p.has_viewed_estimate("q1")
    assert out["viewed"] is True
    assert out["viewed_at"] == "2026-04-29T12:00:00Z"


def test_has_viewed_estimate_draft_returns_not_viewed():
    s = _FakeSession([_FakeResponse(200, {
        "id": "q1",
        "properties": {"hs_quote_status": "DRAFT"},
    })])
    p = _provider(s)
    out = p.has_viewed_estimate("q1")
    assert out["viewed"] is False
    assert out["viewed_at"] is None


def test_has_viewed_estimate_404_returns_unknown():
    s = _FakeSession([_FakeResponse(404, {"error": "not found"})])
    p = _provider(s)
    out = p.has_viewed_estimate("missing")
    assert out == {"viewed": False, "viewed_at": None, "status": "unknown"}


# ---------------------------------------------------------------------------
# deals (opportunities)
# ---------------------------------------------------------------------------


def test_search_opportunities_posts_pipeline_filter():
    s = _FakeSession([_FakeResponse(200, {"results": [{"id": "d1"}]})])
    p = _provider(s)
    out = p.search_opportunities("pipeline_default")
    assert out == [{"id": "d1"}]
    body = s.calls[0]["json"]
    flt = body["filterGroups"][0]["filters"][0]
    assert flt["propertyName"] == "pipeline"
    assert flt["value"] == "pipeline_default"


def test_search_opportunities_with_stage_adds_filter():
    s = _FakeSession([_FakeResponse(200, {"results": []})])
    p = _provider(s)
    p.search_opportunities("pipe1", stage_id="stage_won")
    filters = s.calls[0]["json"]["filterGroups"][0]["filters"]
    assert any(f["propertyName"] == "dealstage" and f["value"] == "stage_won"
               for f in filters)


def test_update_opportunity_stage_patches_dealstage():
    s = _FakeSession([_FakeResponse(200, {})])
    p = _provider(s)
    p.update_opportunity_stage("d1", "stage_won")
    assert s.calls[0]["method"] == "PATCH"
    assert s.calls[0]["json"] == {"properties": {"dealstage": "stage_won"}}


# ---------------------------------------------------------------------------
# error handling
# ---------------------------------------------------------------------------


def test_4xx_raises_provider_error():
    s = _FakeSession([_FakeResponse(401, text="bad token")])
    p = _provider(s)
    with pytest.raises(hubspot_provider.HubSpotProviderError) as ei:
        p.get_contact("c1")
    assert ei.value.status_code == 401


def test_429_retries_once():
    s = _FakeSession([
        _FakeResponse(429, text="rate limited"),
        _FakeResponse(200, {"id": "c1"}),
    ])
    import dashboard_app.services.hubspot_provider as hp
    saved = hp.time.sleep
    hp.time.sleep = lambda *_a, **_k: None
    try:
        p = _provider(s)
        out = p.get_contact("c1")
    finally:
        hp.time.sleep = saved
    assert out["id"] == "c1"
    assert len(s.calls) == 2


# ---------------------------------------------------------------------------
# for_tenant factory
# ---------------------------------------------------------------------------


def test_for_tenant_none_when_no_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    assert hubspot_provider.for_tenant("acme") is None


def test_for_tenant_builds_from_stored_creds(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _credentials.store_paste(
        "acme",
        "hubspot",
        {
            "access_token": "pat-abc",
            "portal_id": "12345",
            "from_email": "owner@acme.test",
        },
    )
    p = hubspot_provider.for_tenant("acme")
    assert p is not None
    assert p._token == "pat-abc"
    assert p._from_email == "owner@acme.test"


def test_for_tenant_accepts_legacy_api_key_field(tmp_path, monkeypatch):
    """Older paste forms used `api_key` for the access token; the factory
    falls back to that key for backward compat."""
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _credentials.store_paste("acme", "hubspot", {"api_key": "pat-legacy"})
    p = hubspot_provider.for_tenant("acme")
    assert p is not None
    assert p._token == "pat-legacy"
