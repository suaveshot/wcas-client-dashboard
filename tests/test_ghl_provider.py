"""Tests for dashboard_app.services.ghl_provider.

Every CRMProvider method has a covering test against a fake HTTP session.
The point is to lock in the full method surface so we can never repeat
the 2026-04-23 regression where a partial provider class shipped without
the methods consumers were actually calling.
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
    ghl_provider,
)


# ---------------------------------------------------------------------------
# fake HTTP session
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
    """Minimal stand-in for the requests module. Records every call and
    pops responses from `queue` in order so tests can assert
    method/url/headers/body without spinning up a real HTTP server."""

    def __init__(self, queue: list[_FakeResponse] | None = None):
        self.queue: list[_FakeResponse] = queue or []
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.queue:
            return _FakeResponse(200, {})
        return self.queue.pop(0)


def _provider(session: _FakeSession, *, from_email: str | None = "sam@example.com"):
    return ghl_provider.GHLProvider(
        api_key="test-key",
        location_id="loc_abc",
        from_email=from_email,
        session=session,
    )


# ---------------------------------------------------------------------------
# protocol shape
# ---------------------------------------------------------------------------


def test_ghl_provider_satisfies_crm_protocol():
    p = _provider(_FakeSession())
    assert isinstance(p, crm_provider.CRMProvider)


def test_every_protocol_method_is_implemented():
    """Hard backstop: enumerate every method on the Protocol and assert
    GHLProvider has it. Prevents a future Protocol expansion from silently
    leaving GHLProvider behind."""
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
        assert callable(getattr(p, name, None)), f"GHLProvider missing {name!r}"


# ---------------------------------------------------------------------------
# constructor validation
# ---------------------------------------------------------------------------


def test_constructor_rejects_blank_api_key():
    with pytest.raises(ValueError):
        ghl_provider.GHLProvider(api_key="", location_id="loc")


def test_constructor_rejects_blank_location():
    with pytest.raises(ValueError):
        ghl_provider.GHLProvider(api_key="k", location_id="")


# ---------------------------------------------------------------------------
# auth + headers
# ---------------------------------------------------------------------------


def test_each_request_sends_bearer_and_version():
    s = _FakeSession([_FakeResponse(200, {"contact": {"id": "abc"}})])
    p = _provider(s)
    p.get_contact("abc")
    call = s.calls[0]
    assert call["method"] == "GET"
    assert call["url"].endswith("/contacts/abc")
    assert call["headers"]["Authorization"] == "Bearer test-key"
    assert call["headers"]["Version"] == ghl_provider.DEFAULT_API_VERSION


# ---------------------------------------------------------------------------
# list_contacts pagination
# ---------------------------------------------------------------------------


def test_list_contacts_paginates_via_meta_cursor():
    s = _FakeSession([
        _FakeResponse(200, {
            "contacts": [{"id": "1"}, {"id": "2"}],
            "meta": {"startAfter": 100, "startAfterId": "2"},
        }),
        _FakeResponse(200, {
            "contacts": [{"id": "3"}],
            "meta": {},
        }),
        _FakeResponse(200, {"contacts": []}),
    ])
    p = _provider(s)
    out = p.list_contacts(page_size=50)
    assert [c["id"] for c in out] == ["1", "2", "3"]
    # cursor advanced after the first page
    assert s.calls[1]["params"]["startAfter"] == 100
    assert s.calls[1]["params"]["startAfterId"] == "2"


def test_list_contacts_stops_on_empty_batch():
    s = _FakeSession([_FakeResponse(200, {"contacts": []})])
    p = _provider(s)
    assert p.list_contacts() == []


# ---------------------------------------------------------------------------
# contact CRUD
# ---------------------------------------------------------------------------


def test_get_contact_unwraps_envelope():
    s = _FakeSession([_FakeResponse(200, {"contact": {"id": "c1", "firstName": "Sam"}})])
    p = _provider(s)
    c = p.get_contact("c1")
    assert c["firstName"] == "Sam"


def test_get_contact_returns_raw_when_no_envelope():
    s = _FakeSession([_FakeResponse(200, {"id": "c1", "firstName": "Sam"})])
    p = _provider(s)
    c = p.get_contact("c1")
    assert c["firstName"] == "Sam"


def test_update_contact_puts_with_body():
    s = _FakeSession([_FakeResponse(200, {"contact": {"id": "c1", "firstName": "Updated"}})])
    p = _provider(s)
    p.update_contact("c1", {"firstName": "Updated"})
    call = s.calls[0]
    assert call["method"] == "PUT"
    assert call["url"].endswith("/contacts/c1")
    assert call["json"] == {"firstName": "Updated"}


# ---------------------------------------------------------------------------
# conversations + messages
# ---------------------------------------------------------------------------


def test_search_conversations_passes_location_and_contact():
    s = _FakeSession([_FakeResponse(200, {"conversations": [{"id": "conv1"}]})])
    p = _provider(s)
    out = p.search_conversations("c1")
    assert out == [{"id": "conv1"}]
    params = s.calls[0]["params"]
    assert params["locationId"] == "loc_abc"
    assert params["contactId"] == "c1"


def test_get_conversation_messages_handles_dict_envelope():
    s = _FakeSession([_FakeResponse(200, {
        "messages": {"messages": [{"id": "m1"}, {"id": "m2"}]}
    })])
    p = _provider(s)
    msgs = p.get_conversation_messages("conv1")
    assert [m["id"] for m in msgs] == ["m1", "m2"]


def test_get_conversation_messages_handles_list_envelope():
    s = _FakeSession([_FakeResponse(200, {"messages": [{"id": "m1"}]})])
    p = _provider(s)
    msgs = p.get_conversation_messages("conv1")
    assert [m["id"] for m in msgs] == ["m1"]


def test_get_full_conversation_history_sorts_by_timestamp():
    s = _FakeSession([
        # search_conversations
        _FakeResponse(200, {"conversations": [{"id": "convA"}, {"id": "convB"}]}),
        # get_conversation_messages convA
        _FakeResponse(200, {"messages": [
            {"id": "m2", "dateAdded": "2026-04-29T12:00:00Z", "body": "two"},
        ]}),
        # get_conversation_messages convB
        _FakeResponse(200, {"messages": [
            {"id": "m1", "dateAdded": "2026-04-28T12:00:00Z", "body": "one"},
            {"id": "m3", "dateAdded": "2026-04-30T12:00:00Z", "body": "three"},
        ]}),
    ])
    p = _provider(s)
    history = p.get_full_conversation_history("c1")
    assert [m["body"] for m in history] == ["one", "two", "three"]


def test_get_full_conversation_history_empty_when_no_conversations():
    s = _FakeSession([_FakeResponse(200, {"conversations": []})])
    p = _provider(s)
    assert p.get_full_conversation_history("c1") == []


# ---------------------------------------------------------------------------
# send_email / send_sms
# ---------------------------------------------------------------------------


def test_send_email_posts_html_with_from():
    s = _FakeSession([_FakeResponse(200, {"messageId": "msg1"})])
    p = _provider(s)
    msg_id = p.send_email("c1", "Hello", "<p>hi</p>")
    assert msg_id == "msg1"
    body = s.calls[0]["json"]
    assert body["type"] == "Email"
    assert body["html"] == "<p>hi</p>"
    assert body["emailFrom"] == "sam@example.com"
    assert "scheduledTimestamp" not in body


def test_send_email_includes_scheduled_timestamp():
    s = _FakeSession([_FakeResponse(200, {"messageId": "msg2"})])
    p = _provider(s)
    when = datetime(2026, 4, 30, 12, 0, tzinfo=timezone.utc)
    p.send_email("c1", "S", "<p>b</p>", scheduled_at=when)
    body = s.calls[0]["json"]
    assert body["scheduledTimestamp"] == int(when.timestamp())


def test_send_email_includes_attachments():
    s = _FakeSession([_FakeResponse(200, {"messageId": "msg3"})])
    p = _provider(s)
    p.send_email("c1", "S", "<p>b</p>", attachment_urls=["https://x/a.pdf"])
    body = s.calls[0]["json"]
    assert body["attachments"] == ["https://x/a.pdf"]


def test_send_email_without_from_email_raises():
    s = _FakeSession([_FakeResponse(200, {"messageId": "msg"})])
    p = _provider(s, from_email=None)
    with pytest.raises(ghl_provider.GHLProviderError):
        p.send_email("c1", "S", "<p>b</p>")


def test_send_sms_posts_message():
    s = _FakeSession([_FakeResponse(200, {"messageId": "smsm1"})])
    p = _provider(s)
    out = p.send_sms("c1", "ping")
    assert out == "smsm1"
    body = s.calls[0]["json"]
    assert body["type"] == "SMS"
    assert body["message"] == "ping"


def test_send_sms_supports_scheduled_at():
    s = _FakeSession([_FakeResponse(200, {"messageId": "smsm2"})])
    p = _provider(s)
    when = datetime(2026, 5, 1, 9, 0, tzinfo=timezone.utc)
    p.send_sms("c1", "ping", scheduled_at=when)
    body = s.calls[0]["json"]
    assert body["scheduledTimestamp"] == int(when.timestamp())


# ---------------------------------------------------------------------------
# cancel_scheduled_message
# ---------------------------------------------------------------------------


def test_cancel_scheduled_message_returns_true_on_success():
    s = _FakeSession([_FakeResponse(200, payload=None, text="")])
    p = _provider(s)
    assert p.cancel_scheduled_message("msg1") is True
    assert s.calls[0]["method"] == "DELETE"
    assert s.calls[0]["url"].endswith("/conversations/messages/msg1/schedule")


def test_cancel_scheduled_message_returns_false_on_failure():
    s = _FakeSession([_FakeResponse(404, payload={"error": "not found"})])
    p = _provider(s)
    assert p.cancel_scheduled_message("msg404") is False


# ---------------------------------------------------------------------------
# has_viewed_estimate
# ---------------------------------------------------------------------------


def test_has_viewed_estimate_returns_viewed_true_for_viewed_status():
    s = _FakeSession([_FakeResponse(200, {
        "estimate": {"status": "viewed", "updatedAt": "2026-04-29T12:00:00Z"}
    })])
    p = _provider(s)
    out = p.has_viewed_estimate("est1")
    assert out["viewed"] is True
    assert out["status"] == "viewed"
    assert out["viewed_at"] == "2026-04-29T12:00:00Z"


def test_has_viewed_estimate_returns_viewed_false_for_draft_status():
    s = _FakeSession([_FakeResponse(200, {"estimate": {"status": "draft"}})])
    p = _provider(s)
    out = p.has_viewed_estimate("est2")
    assert out["viewed"] is False
    assert out["status"] == "draft"
    assert out["viewed_at"] is None


def test_has_viewed_estimate_swallows_api_error():
    s = _FakeSession([_FakeResponse(500, payload={"error": "boom"})])
    p = _provider(s)
    out = p.has_viewed_estimate("est3")
    assert out == {"viewed": False, "viewed_at": None, "status": "unknown"}


# ---------------------------------------------------------------------------
# opportunities
# ---------------------------------------------------------------------------


def test_search_opportunities_passes_pipeline_id():
    s = _FakeSession([_FakeResponse(200, {"opportunities": [{"id": "o1"}]})])
    p = _provider(s)
    out = p.search_opportunities("pipe1")
    assert out == [{"id": "o1"}]
    params = s.calls[0]["params"]
    assert params["pipeline_id"] == "pipe1"
    assert params["location_id"] == "loc_abc"
    assert "pipeline_stage_id" not in params


def test_search_opportunities_includes_stage_filter_when_provided():
    s = _FakeSession([_FakeResponse(200, {"opportunities": []})])
    p = _provider(s)
    p.search_opportunities("pipe1", stage_id="stageX")
    assert s.calls[0]["params"]["pipeline_stage_id"] == "stageX"


def test_update_opportunity_stage_sends_put():
    s = _FakeSession([_FakeResponse(200, {})])
    p = _provider(s)
    p.update_opportunity_stage("op1", "stage_won")
    call = s.calls[0]
    assert call["method"] == "PUT"
    assert call["url"].endswith("/opportunities/op1")
    assert call["json"] == {"pipelineStageId": "stage_won"}


# ---------------------------------------------------------------------------
# error handling
# ---------------------------------------------------------------------------


def test_4xx_raises_ghl_provider_error():
    s = _FakeSession([_FakeResponse(400, text='{"error":"bad"}')])
    p = _provider(s)
    with pytest.raises(ghl_provider.GHLProviderError) as ei:
        p.get_contact("c1")
    assert ei.value.status_code == 400


def test_429_retries_once_then_succeeds():
    s = _FakeSession([
        _FakeResponse(429, text="rate limited"),
        _FakeResponse(200, {"contact": {"id": "c1"}}),
    ])
    p = _provider(s)
    # If the retry didn't fire, this would raise GHLProviderError(429).
    # We patch time.sleep so the test stays fast.
    import dashboard_app.services.ghl_provider as gp
    saved = gp.time.sleep
    gp.time.sleep = lambda *_a, **_k: None
    try:
        c = p.get_contact("c1")
    finally:
        gp.time.sleep = saved
    assert c["id"] == "c1"
    assert len(s.calls) == 2


# ---------------------------------------------------------------------------
# for_tenant factory
# ---------------------------------------------------------------------------


def test_for_tenant_returns_none_when_no_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    assert ghl_provider.for_tenant("acme") is None


def test_for_tenant_builds_provider_from_stored_creds(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _credentials.store_paste(
        "acme",
        "ghl",
        {
            "api_key": "tk-123",
            "location_id": "loc-ac",
            "from_email": "owner@acme.test",
        },
    )
    p = ghl_provider.for_tenant("acme")
    assert p is not None
    assert p._location_id == "loc-ac"
    assert p._from_email == "owner@acme.test"


def test_for_tenant_returns_none_when_creds_incomplete(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _credentials.store_paste("acme", "ghl", {"api_key": "tk-123"})
    assert ghl_provider.for_tenant("acme") is None
