"""Tests for dashboard_app.services.pipedrive_provider."""

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
    pipedrive_provider,
)


# ---------------------------------------------------------------------------
# fakes
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


def _provider(session: _FakeSession):
    return pipedrive_provider.PipedriveProvider(
        api_token="pd-token",
        company_domain="acme",
        from_email="sam@example.com",
        session=session,
    )


# ---------------------------------------------------------------------------
# protocol shape + constructor
# ---------------------------------------------------------------------------


def test_pipedrive_provider_satisfies_crm_protocol():
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
        assert callable(getattr(p, name, None)), f"PipedriveProvider missing {name!r}"


def test_constructor_rejects_blank_token():
    with pytest.raises(ValueError):
        pipedrive_provider.PipedriveProvider(
            api_token="", company_domain="acme",
        )


def test_constructor_rejects_blank_company_domain():
    with pytest.raises(ValueError):
        pipedrive_provider.PipedriveProvider(
            api_token="pd", company_domain="",
        )


def test_base_url_uses_company_subdomain():
    p = pipedrive_provider.PipedriveProvider(
        api_token="pd", company_domain="garcia-folklorico",
    )
    assert p._base == "https://garcia-folklorico.pipedrive.com"


# ---------------------------------------------------------------------------
# auth pattern: api_token in query string
# ---------------------------------------------------------------------------


def test_each_request_includes_api_token_query_param():
    s = _FakeSession([_FakeResponse(200, {"data": {"id": 42}})])
    p = _provider(s)
    p.get_contact("42")
    assert s.calls[0]["params"]["api_token"] == "pd-token"


def test_no_authorization_header_set():
    """Pipedrive uses query-param auth, not bearer; bearer would be ignored
    and could leak the token in unintended logs."""
    s = _FakeSession([_FakeResponse(200, {"data": {"id": 1}})])
    p = _provider(s)
    p.get_contact("1")
    assert "Authorization" not in s.calls[0]["headers"]


# ---------------------------------------------------------------------------
# contacts
# ---------------------------------------------------------------------------


def test_list_contacts_paginates_via_cursor():
    s = _FakeSession([
        _FakeResponse(200, {
            "data": [{"id": 1}, {"id": 2}],
            "additional_data": {"next_cursor": "ABC"},
        }),
        _FakeResponse(200, {
            "data": [{"id": 3}],
            "additional_data": {},
        }),
    ])
    p = _provider(s)
    out = p.list_contacts(page_size=50)
    assert [c["id"] for c in out] == [1, 2, 3]
    assert s.calls[1]["params"]["cursor"] == "ABC"


def test_list_contacts_caps_page_size_at_500():
    s = _FakeSession([_FakeResponse(200, {"data": []})])
    p = _provider(s)
    p.list_contacts(page_size=9999)
    assert s.calls[0]["params"]["limit"] == 500


def test_get_contact_unwraps_data_envelope():
    s = _FakeSession([_FakeResponse(200, {"data": {"id": 7, "name": "Sam"}})])
    p = _provider(s)
    out = p.get_contact("7")
    assert out["id"] == 7
    assert out["name"] == "Sam"


def test_update_contact_patches_with_bare_props():
    s = _FakeSession([_FakeResponse(200, {"data": {"id": 7}})])
    p = _provider(s)
    p.update_contact("7", {"name": "Updated"})
    assert s.calls[0]["method"] == "PATCH"
    assert s.calls[0]["json"] == {"name": "Updated"}


# ---------------------------------------------------------------------------
# conversations (notes)
# ---------------------------------------------------------------------------


def test_search_conversations_filters_by_person_id():
    s = _FakeSession([_FakeResponse(200, {"data": [{"id": 1, "content": "hi"}]})])
    p = _provider(s)
    out = p.search_conversations("42")
    assert out == [{"id": 1, "content": "hi"}]
    assert s.calls[0]["params"]["person_id"] == "42"


def test_get_conversation_messages_wraps_single_note_as_list():
    s = _FakeSession([_FakeResponse(200, {"data": {"id": 1, "content": "x"}})])
    p = _provider(s)
    msgs = p.get_conversation_messages("1")
    assert msgs == [{"id": 1, "content": "x"}]


def test_get_conversation_messages_empty_when_no_data():
    s = _FakeSession([_FakeResponse(200, {"data": None})])
    p = _provider(s)
    msgs = p.get_conversation_messages("1")
    assert msgs == []


def test_get_full_conversation_history_orders_by_add_time():
    s = _FakeSession([
        _FakeResponse(200, {"data": [
            {"id": 2, "content": "two", "add_time": "2026-04-29T12:00:00Z"},
            {"id": 1, "content": "one", "add_time": "2026-04-28T12:00:00Z"},
        ]}),
    ])
    p = _provider(s)
    history = p.get_full_conversation_history("1")
    assert [m["body"] for m in history] == ["one", "two"]


# ---------------------------------------------------------------------------
# unsupported send paths
# ---------------------------------------------------------------------------


def test_send_email_raises_explicit_not_supported():
    p = _provider(_FakeSession())
    with pytest.raises(pipedrive_provider.PipedriveProviderError):
        p.send_email("1", "S", "<p>b</p>")


def test_send_sms_raises_explicit_not_supported():
    p = _provider(_FakeSession())
    with pytest.raises(pipedrive_provider.PipedriveProviderError):
        p.send_sms("1", "ping")


def test_cancel_scheduled_message_returns_false():
    p = _provider(_FakeSession())
    assert p.cancel_scheduled_message("evt") is False


# ---------------------------------------------------------------------------
# estimates (deal-stage proxy)
# ---------------------------------------------------------------------------


def test_has_viewed_estimate_when_stage_is_proposal():
    s = _FakeSession([_FakeResponse(200, {
        "data": {"id": 1, "stage_name": "Proposal Sent",
                 "update_time": "2026-04-29T12:00:00Z"},
    })])
    p = _provider(s)
    out = p.has_viewed_estimate("1")
    assert out["viewed"] is True


def test_has_viewed_estimate_unknown_when_404():
    s = _FakeSession([_FakeResponse(404, payload={"error": "missing"})])
    p = _provider(s)
    out = p.has_viewed_estimate("missing")
    assert out["status"] == "unknown"
    assert out["viewed"] is False


# ---------------------------------------------------------------------------
# deals
# ---------------------------------------------------------------------------


def test_search_opportunities_passes_pipeline_id():
    s = _FakeSession([_FakeResponse(200, {"data": [{"id": 1}]})])
    p = _provider(s)
    out = p.search_opportunities("3")
    assert out == [{"id": 1}]
    assert s.calls[0]["params"]["pipeline_id"] == "3"
    assert "stage_id" not in s.calls[0]["params"]


def test_search_opportunities_includes_stage_filter():
    s = _FakeSession([_FakeResponse(200, {"data": []})])
    p = _provider(s)
    p.search_opportunities("3", stage_id="9")
    assert s.calls[0]["params"]["stage_id"] == "9"


def test_update_opportunity_stage_patches_stage_id():
    s = _FakeSession([_FakeResponse(200, {})])
    p = _provider(s)
    p.update_opportunity_stage("d1", "9")
    assert s.calls[0]["method"] == "PATCH"
    assert s.calls[0]["json"] == {"stage_id": "9"}


# ---------------------------------------------------------------------------
# error handling
# ---------------------------------------------------------------------------


def test_4xx_raises_provider_error():
    s = _FakeSession([_FakeResponse(401, text="bad")])
    p = _provider(s)
    with pytest.raises(pipedrive_provider.PipedriveProviderError) as ei:
        p.get_contact("1")
    assert ei.value.status_code == 401


def test_429_retries_once():
    s = _FakeSession([
        _FakeResponse(429, text="rate limited"),
        _FakeResponse(200, {"data": {"id": 1}}),
    ])
    import dashboard_app.services.pipedrive_provider as pp
    saved = pp.time.sleep
    pp.time.sleep = lambda *_a, **_k: None
    try:
        p = _provider(s)
        out = p.get_contact("1")
    finally:
        pp.time.sleep = saved
    assert out["id"] == 1
    assert len(s.calls) == 2


# ---------------------------------------------------------------------------
# for_tenant factory
# ---------------------------------------------------------------------------


def test_for_tenant_none_when_no_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    assert pipedrive_provider.for_tenant("acme") is None


def test_for_tenant_builds_from_stored_creds(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _credentials.store_paste(
        "acme",
        "pipedrive",
        {
            "api_token": "pd-abc",
            "company_domain": "acme-llc",
            "from_email": "owner@acme.test",
        },
    )
    p = pipedrive_provider.for_tenant("acme")
    assert p is not None
    assert p._token == "pd-abc"
    assert p._company == "acme-llc"


def test_for_tenant_returns_none_when_company_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _credentials.store_paste("acme", "pipedrive", {"api_token": "pd-abc"})
    assert pipedrive_provider.for_tenant("acme") is None
