"""Tests for the post-OAuth validation probe.

Every test monkeypatches the single HTTP seam (`_get_json`) and the
credentials exchange so no real Google call ever fires.
"""

import os

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import pytest

from dashboard_app.services import credentials, validation_probe


@pytest.fixture
def _acme_with_google(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    credentials.clear_access_token_cache()
    credentials.store("acme", "google", refresh_token="refresh-x", scopes=["gmail.modify"])
    monkeypatch.setattr(credentials, "_exchange_google_refresh", lambda _r: "ya29.access-xyz")
    yield
    credentials.clear_access_token_cache()


def _route_map(responses: dict[str, dict]):
    """Turn {substring: body} into a fake _get_json that routes by URL substring."""
    def fake(url, access_token, params=None):
        for key, body in responses.items():
            if key in url:
                return body
        raise validation_probe._ProbeError(f"no mock for url {url}")
    return fake


def test_probe_google_happy_path(_acme_with_google, monkeypatch):
    monkeypatch.setattr(
        validation_probe,
        "_get_json",
        _route_map({
            "gmail.googleapis.com": {
                "emailAddress": "owner@acme.com",
                "messagesTotal": 12345,
            },
            "calendar/v3/users/me/calendarList": {
                "items": [
                    {"id": "owner@acme.com", "primary": True},
                    {"id": "team@acme.com"},
                    {"id": "holidays@group.v.calendar.google.com"},
                ]
            },
            "searchconsole.googleapis.com": {
                "siteEntry": [
                    {"siteUrl": "https://acme.com/", "permissionLevel": "siteOwner"},
                    {"siteUrl": "sc-domain:acme.com", "permissionLevel": "siteOwner"},
                ]
            },
            "analyticsadmin.googleapis.com": {
                "accountSummaries": [
                    {"name": "accountSummaries/1", "propertySummaries": [{"property": "properties/1"}, {"property": "properties/2"}]},
                ]
            },
            "mybusinessaccountmanagement.googleapis.com": {
                "accounts": [{"name": "accounts/12345", "accountName": "Acme HVAC"}],
            },
            "mybusinessbusinessinformation.googleapis.com": {
                "locations": [
                    {"name": "locations/5555", "title": "Acme HVAC Main"},
                    {"name": "locations/5556", "title": "Acme HVAC West"},
                ]
            },
            "mybusiness.googleapis.com/v4": {
                "totalReviewCount": 312,
                "averageRating": 4.6,
                "reviews": [],
            },
        }),
    )
    result = validation_probe.probe_google("acme")
    assert result["ok"] is True
    assert result["errors"] == {}
    s = result["summary"]
    assert s["gmail"] == {"email": "owner@acme.com", "messages_total": 12345}
    assert s["calendar"]["calendar_count"] == 3
    assert s["calendar"]["primary"] == "owner@acme.com"
    assert s["gsc"]["site_count"] == 2
    assert s["gsc"]["first_site"] == "https://acme.com/"
    assert s["ga4"] == {"account_count": 1, "property_count": 2}
    assert s["gbp"]["account_count"] == 1
    assert s["gbp"]["location_count"] == 2
    assert s["gbp"]["total_review_count"] == 312
    assert s["gbp"]["average_rating"] == 4.6


def test_probe_google_missing_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    credentials.clear_access_token_cache()
    result = validation_probe.probe_google("acme")
    assert result["ok"] is False
    assert "access_token" in result["errors"]
    assert result["summary"] == {}


def test_probe_google_token_exchange_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    credentials.clear_access_token_cache()
    credentials.store("acme", "google", refresh_token="refresh-x")

    def fail(refresh_token: str):
        raise credentials.ProviderExchangeError("google rejected refresh: HTTP 400")

    monkeypatch.setattr(credentials, "_exchange_google_refresh", fail)
    result = validation_probe.probe_google("acme")
    assert result["ok"] is False
    assert "google rejected" in result["errors"]["access_token"]


def test_one_failing_sub_probe_does_not_blank_others(_acme_with_google, monkeypatch):
    def partial(url, access_token, params=None):
        if "gmail.googleapis.com" in url:
            raise validation_probe._ProbeError("http 403: Gmail API disabled")
        if "calendar/v3/users/me/calendarList" in url:
            return {"items": [{"id": "owner@acme.com", "primary": True}]}
        # Everything else returns the smallest valid body for its probe.
        if "searchconsole.googleapis.com" in url:
            return {"siteEntry": []}
        if "analyticsadmin.googleapis.com" in url:
            return {"accountSummaries": []}
        if "mybusinessaccountmanagement.googleapis.com" in url:
            return {"accounts": []}
        raise validation_probe._ProbeError(f"no mock for {url}")

    monkeypatch.setattr(validation_probe, "_get_json", partial)
    result = validation_probe.probe_google("acme")
    assert result["ok"] is True
    assert "gmail" in result["errors"]
    assert "Gmail API disabled" in result["errors"]["gmail"]
    # Other probes still rendered their empty-but-valid results.
    assert result["summary"]["calendar"]["calendar_count"] == 1
    assert result["summary"]["gsc"] == {"site_count": 0, "first_site": ""}
    assert result["summary"]["gbp"]["account_count"] == 0


def test_unexpected_exception_in_sub_probe_is_caught(_acme_with_google, monkeypatch):
    def boom(url, access_token, params=None):
        if "gmail.googleapis.com" in url:
            raise RuntimeError("something weird")
        # Minimal valid responses for the rest.
        if "calendar" in url:
            return {"items": []}
        if "searchconsole" in url:
            return {"siteEntry": []}
        if "analyticsadmin" in url:
            return {"accountSummaries": []}
        if "mybusinessaccountmanagement" in url:
            return {"accounts": []}
        return {}

    monkeypatch.setattr(validation_probe, "_get_json", boom)
    result = validation_probe.probe_google("acme")
    assert "gmail" in result["errors"]
    assert "unexpected" in result["errors"]["gmail"]


def test_gbp_probe_handles_zero_accounts(_acme_with_google, monkeypatch):
    def empty(url, access_token, params=None):
        if "mybusinessaccountmanagement" in url:
            return {"accounts": []}
        if "gmail" in url:
            return {"emailAddress": "x@y.com", "messagesTotal": 0}
        if "calendar" in url:
            return {"items": []}
        if "searchconsole" in url:
            return {"siteEntry": []}
        if "analyticsadmin" in url:
            return {"accountSummaries": []}
        return {}

    monkeypatch.setattr(validation_probe, "_get_json", empty)
    result = validation_probe.probe_google("acme")
    assert result["summary"]["gbp"] == {
        "account_count": 0,
        "location_count": 0,
        "total_review_count": 0,
        "average_rating": 0.0,
    }


def test_gbp_probe_handles_zero_locations_in_account(_acme_with_google, monkeypatch):
    def no_locations(url, access_token, params=None):
        if "mybusinessaccountmanagement" in url:
            return {"accounts": [{"name": "accounts/1"}]}
        if "mybusinessbusinessinformation" in url:
            return {"locations": []}
        if "gmail" in url:
            return {"emailAddress": "x@y.com", "messagesTotal": 0}
        if "calendar" in url:
            return {"items": []}
        if "searchconsole" in url:
            return {"siteEntry": []}
        if "analyticsadmin" in url:
            return {"accountSummaries": []}
        return {}

    monkeypatch.setattr(validation_probe, "_get_json", no_locations)
    result = validation_probe.probe_google("acme")
    assert result["summary"]["gbp"]["account_count"] == 1
    assert result["summary"]["gbp"]["location_count"] == 0
    # No reviews call should have been made because no location to query.
    assert result["summary"]["gbp"]["total_review_count"] == 0


def test_get_json_routes_through_httpx(monkeypatch):
    captured = {}

    class FakeResp:
        status_code = 200

        def json(self):
            return {"ok": True}

    def fake_get(url, *args, headers=None, params=None, timeout=None, **kwargs):
        captured["url"] = url
        captured["headers"] = headers
        captured["params"] = params
        captured["timeout"] = timeout
        return FakeResp()

    import httpx
    monkeypatch.setattr(httpx, "get", fake_get)
    body = validation_probe._get_json("https://example.test/foo", "access-abc", params={"q": "x"})
    assert body == {"ok": True}
    assert captured["headers"]["Authorization"] == "Bearer access-abc"
    assert captured["params"] == {"q": "x"}
    assert captured["timeout"] == validation_probe._HTTP_TIMEOUT_SECONDS


def test_get_json_raises_on_http_error(monkeypatch):
    class FakeResp:
        status_code = 403
        text = "insufficient permissions"

    import httpx
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: FakeResp())
    with pytest.raises(validation_probe._ProbeError):
        validation_probe._get_json("https://example.test/foo", "tok")


def test_get_json_raises_on_network_error(monkeypatch):
    import httpx

    def raise_timeout(*args, **kwargs):
        raise httpx.ConnectTimeout("timed out")

    monkeypatch.setattr(httpx, "get", raise_timeout)
    with pytest.raises(validation_probe._ProbeError):
        validation_probe._get_json("https://example.test/foo", "tok")


# --- save_result / load_result --------------------------------------------


def test_save_and_load_result_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    payload = {"ok": True, "errors": {}, "summary": {"gmail": {"email": "x@y.com"}}}
    path = validation_probe.save_result("acme", "google", payload)
    assert path is not None and path.exists()
    loaded = validation_probe.load_result("acme", "google")
    assert loaded is not None
    assert loaded["ok"] is True
    assert loaded["summary"]["gmail"]["email"] == "x@y.com"
    assert loaded["saved_at"]


def test_load_result_none_for_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    assert validation_probe.load_result("acme", "google") is None


def test_save_result_rejects_invalid_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    assert validation_probe.save_result("acme", "../escape", {"ok": True}) is None


def test_save_result_rejects_invalid_tenant(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    # Invalid tenant slug leads save_result to return None (not raise).
    assert validation_probe.save_result("../escape", "google", {"ok": True}) is None
