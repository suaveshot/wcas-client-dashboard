"""Tests for the Activation Orchestrator tool layer.

Covers tool schemas + dispatch behavior + each fully-implemented
handler. Every network-touching path (`fetch_site_facts`) is
monkeypatched so no real HTTP call fires during tests.
"""

import os

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import pytest

from dashboard_app.services import (
    activation_state,
    activation_tools,
    credentials,
    tenant_kb,
    validation_probe,
)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    credentials.clear_access_token_cache()
    yield
    credentials.clear_access_token_cache()


# --- Schema contract -------------------------------------------------------


def test_every_schema_has_matching_handler():
    schema_names = {t["name"] for t in activation_tools.TOOL_SCHEMAS}
    handler_names = set(activation_tools.HANDLERS.keys())
    assert schema_names == handler_names, (
        f"schema/handler mismatch. schema only: {schema_names - handler_names}, "
        f"handler only: {handler_names - schema_names}"
    )


def test_schemas_have_adr005_tools():
    names = {t["name"] for t in activation_tools.TOOL_SCHEMAS}
    required_adr005 = {
        "confirm_company_facts", "activate_pipeline", "request_credential",
        "set_schedule", "set_preference", "set_timezone", "capture_baseline",
        "set_goals", "write_kb_entry", "mark_activation_complete",
    }
    assert required_adr005 <= names


def test_schemas_have_tier2_tools():
    names = {t["name"] for t in activation_tools.TOOL_SCHEMAS}
    tier2 = {"fetch_site_facts", "lookup_gbp_public", "create_ga4_property", "verify_gsc_domain"}
    assert tier2 <= names


def test_every_schema_has_type_custom_and_input_schema():
    for tool in activation_tools.TOOL_SCHEMAS:
        assert tool["type"] == "custom"
        assert tool["name"]
        assert tool["description"]
        assert isinstance(tool["input_schema"], dict)
        assert tool["input_schema"].get("type") == "object"


# --- Dispatch layer --------------------------------------------------------


def test_dispatch_unknown_tool_returns_error():
    ok, payload = activation_tools.dispatch("acme", "nope", {})
    assert ok is False
    assert "unknown tool" in payload["error"]


def test_dispatch_catches_unexpected_exception(monkeypatch):
    def boom(tid, args):
        raise RuntimeError("simulated bug")
    monkeypatch.setitem(activation_tools.HANDLERS, "activate_pipeline", boom)
    ok, payload = activation_tools.dispatch("acme", "activate_pipeline", {})
    assert ok is False
    assert "internal error" in payload["error"]


def test_dispatch_surfaces_tool_error_cleanly():
    ok, payload = activation_tools.dispatch("acme", "activate_pipeline", {})
    assert ok is False
    assert "role_slug is required" in payload["error"]


# --- confirm_company_facts -------------------------------------------------


def test_confirm_company_facts_writes_company_md():
    ok, payload = activation_tools.dispatch("acme", "confirm_company_facts", {
        "name": "Acme HVAC",
        "website": "https://acmehvac.com",
        "phone": "(805) 555-0100",
        "address": "123 Main St",
        "city": "Oxnard",
        "state": "CA",
        "timezone": "America/Los_Angeles",
        "hours": "Mon-Fri 7am-6pm",
        "categories": ["HVAC contractor", "Heating contractor"],
    })
    assert ok is True
    assert payload["status"] == "saved"
    body = tenant_kb.read_section("acme", "company")
    assert "Acme HVAC" in body
    assert "(805) 555-0100" in body
    assert "HVAC contractor, Heating contractor" in body


def test_confirm_company_facts_rejects_missing_name():
    ok, payload = activation_tools.dispatch("acme", "confirm_company_facts", {"website": "https://x.com"})
    assert ok is False
    assert "name is required" in payload["error"]


def test_confirm_company_facts_ignores_empty_optional_fields():
    ok, _ = activation_tools.dispatch("acme", "confirm_company_facts", {
        "name": "Minimal",
        "phone": "",
        "website": "",
    })
    assert ok is True
    body = tenant_kb.read_section("acme", "company")
    assert "Minimal" in body
    assert "**Phone:**" not in body  # empty field shouldn't surface


# --- write_kb_entry --------------------------------------------------------


def test_write_kb_entry_writes_section():
    ok, payload = activation_tools.dispatch("acme", "write_kb_entry", {
        "section": "voice",
        "content": "Warm, owner-to-owner. Never pushy. Signs off 'Sam'.",
    })
    assert ok is True
    assert payload["section"] == "voice"
    body = tenant_kb.read_section("acme", "voice")
    assert "Warm, owner-to-owner" in body


def test_write_kb_entry_rejects_unknown_section():
    ok, payload = activation_tools.dispatch("acme", "write_kb_entry", {
        "section": "random_stuff",
        "content": "hi",
    })
    assert ok is False
    assert "unknown section" in payload["error"]


def test_write_kb_entry_rejects_empty_content():
    ok, payload = activation_tools.dispatch("acme", "write_kb_entry", {
        "section": "voice",
        "content": "   ",
    })
    assert ok is False


# --- request_credential ----------------------------------------------------


def test_request_credential_google_oauth_returns_start_url():
    ok, payload = activation_tools.dispatch("acme", "request_credential", {
        "service": "google",
        "method": "oauth",
    })
    assert ok is True
    assert payload["oauth_start_url"] == "/auth/oauth/google/start"
    assert payload["service"] == "google"
    assert payload["button_label"]


def test_request_credential_non_google_is_honest_stub():
    ok, payload = activation_tools.dispatch("acme", "request_credential", {
        "service": "meta",
        "method": "oauth",
    })
    assert ok is True
    assert payload["status"] == "not_yet_implemented"


# --- activate_pipeline -----------------------------------------------------


def test_activate_pipeline_advances_state():
    ok, payload = activation_tools.dispatch("acme", "activate_pipeline", {
        "role_slug": "gbp",
        "step": "credentials",
    })
    assert ok is True
    assert payload["step"] == "credentials"
    assert activation_state.role_step("acme", "gbp") == "credentials"


def test_activate_pipeline_rejects_regression():
    activation_tools.dispatch("acme", "activate_pipeline", {"role_slug": "gbp", "step": "connected"})
    ok, payload = activation_tools.dispatch("acme", "activate_pipeline", {
        "role_slug": "gbp",
        "step": "credentials",
    })
    assert ok is False
    assert "regress" in payload["error"]


# --- capture_baseline ------------------------------------------------------


def test_capture_baseline_runs_probe_and_saves(monkeypatch):
    fake = {
        "ok": True,
        "errors": {},
        "summary": {"gmail": {"email": "x@y.com", "messages_total": 100}},
    }
    monkeypatch.setattr(validation_probe, "probe_google", lambda _t: fake)
    ok, payload = activation_tools.dispatch("acme", "capture_baseline", {})
    assert ok is True
    assert payload["status"] == "ok"
    assert validation_probe.load_result("acme", "google") is not None


def test_capture_baseline_reports_partial_on_error():
    fake = {"ok": False, "errors": {"access_token": "revoked"}, "summary": {}}
    import dashboard_app.services.validation_probe as vp
    original = vp.probe_google
    try:
        vp.probe_google = lambda _t: fake
        ok, payload = activation_tools.dispatch("acme", "capture_baseline", {})
    finally:
        vp.probe_google = original
    assert ok is True
    assert payload["status"] == "partial"
    assert "access_token" in payload["errors"]


# --- mark_activation_complete ---------------------------------------------


def test_mark_activation_complete_uses_note():
    activation_tools.dispatch("acme", "activate_pipeline", {"role_slug": "gbp", "step": "credentials"})
    ok, payload = activation_tools.dispatch("acme", "mark_activation_complete", {
        "note": "All set. Reviews and Morning Reports running by tomorrow.",
    })
    assert ok is True
    assert payload["status"] == "activated"
    assert payload["role_count"] == 1
    assert "Reviews and Morning Reports" in payload["note"]


def test_mark_activation_complete_default_note():
    ok, payload = activation_tools.dispatch("acme", "mark_activation_complete", {})
    assert ok is True
    assert "Pipelines will fill" in payload["note"]


# --- fetch_site_facts ------------------------------------------------------


def test_fetch_site_facts_returns_truncated_html(monkeypatch):
    import httpx

    class FakeResp:
        status_code = 200
        headers = {"content-type": "text/html"}
        text = "<html><body>" + ("x" * 50_000) + "</body></html>"

        @property
        def url(self):
            return "https://example.com/"

    monkeypatch.setattr(httpx, "get", lambda *a, **kw: FakeResp())
    ok, payload = activation_tools.dispatch("acme", "fetch_site_facts", {"url": "https://example.com/"})
    assert ok is True
    assert payload["url"] == "https://example.com/"
    assert len(payload["pages"]) == 1
    page = payload["pages"][0]
    assert page["status"] == 200
    assert page["truncated"] is True
    assert len(page["html"]) == 30_000
    assert "Raw HTML" in payload["note"]


def test_fetch_site_facts_rejects_bad_url():
    ok, payload = activation_tools.dispatch("acme", "fetch_site_facts", {"url": "ftp://nope"})
    assert ok is False
    assert "http://" in payload["error"] or "https://" in payload["error"]


def test_fetch_site_facts_surfaces_http_error(monkeypatch):
    import httpx

    class FakeResp:
        status_code = 404
        headers = {}
        text = ""

        @property
        def url(self):
            return "https://example.com/"

    monkeypatch.setattr(httpx, "get", lambda *a, **kw: FakeResp())
    ok, payload = activation_tools.dispatch("acme", "fetch_site_facts", {"url": "https://example.com/"})
    assert ok is False
    assert "404" in payload["error"]


def test_fetch_site_facts_surfaces_network_error(monkeypatch):
    import httpx

    def raise_timeout(*a, **kw):
        raise httpx.ConnectTimeout("timed out")

    monkeypatch.setattr(httpx, "get", raise_timeout)
    ok, payload = activation_tools.dispatch("acme", "fetch_site_facts", {"url": "https://example.com/"})
    assert ok is False
    assert "fetch failed" in payload["error"]


# --- Stub behavior ---------------------------------------------------------


def test_stubs_return_honest_not_yet_implemented():
    for name in ("set_schedule", "set_preference", "set_timezone", "set_goals",
                 "lookup_gbp_public"):
        ok, payload = activation_tools.dispatch("acme", name, {})
        assert ok is True, f"stub {name} returned ok=False"
        assert payload["status"] == "not_yet_implemented", f"stub {name} has wrong status"
        assert payload["tool"] == name


# --- create_ga4_property ---------------------------------------------------


def _seed_google_credential(tenant_id: str, scopes: list[str]) -> None:
    credentials.store(tenant_id, "google", refresh_token="refresh-x", scopes=scopes)


def test_create_ga4_property_requires_analytics_edit_scope(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _seed_google_credential("acme", ["https://www.googleapis.com/auth/analytics.readonly"])
    ok, payload = activation_tools.dispatch("acme", "create_ga4_property", {
        "display_name": "Acme HVAC",
        "website_url": "https://acmehvac.com",
        "timezone": "America/Los_Angeles",
    })
    assert ok is True
    assert payload["status"] == "reconnect_required"
    assert payload["missing_scope"].endswith("/analytics.edit")


def test_create_ga4_property_rejects_missing_args(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _seed_google_credential("acme", ["https://www.googleapis.com/auth/analytics.edit"])
    ok, payload = activation_tools.dispatch("acme", "create_ga4_property", {"display_name": "X"})
    assert ok is False
    assert "website_url" in payload["error"]


def test_create_ga4_property_rejects_http_only_url(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _seed_google_credential("acme", ["https://www.googleapis.com/auth/analytics.edit"])
    ok, payload = activation_tools.dispatch("acme", "create_ga4_property", {
        "display_name": "X", "website_url": "acmehvac.com",
    })
    assert ok is False


def test_create_ga4_property_happy_path(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _seed_google_credential("acme", ["https://www.googleapis.com/auth/analytics.edit"])

    calls: list[tuple[str, str]] = []

    def fake(method, url, tid, *, json_body=None, params=None):
        calls.append((method, url))
        if "accountSummaries" in url:
            return 200, {"accountSummaries": [{"account": "accounts/9988"}]}
        if url.endswith("/v1beta/properties"):
            return 201, {"name": "properties/4242", "displayName": json_body["displayName"]}
        if "/dataStreams" in url:
            return 201, {
                "name": "properties/4242/dataStreams/7",
                "webStreamData": {"measurementId": "G-ABC123XYZ"},
            }
        raise AssertionError(f"unexpected call {method} {url}")

    monkeypatch.setattr(activation_tools, "_google_api_call", fake)
    ok, payload = activation_tools.dispatch("acme", "create_ga4_property", {
        "display_name": "Acme HVAC",
        "website_url": "https://acmehvac.com",
        "timezone": "America/Los_Angeles",
        "industry": "home_services",
    })
    assert ok is True
    assert payload["status"] == "created"
    assert payload["property"] == "properties/4242"
    assert payload["measurement_id"] == "G-ABC123XYZ"
    assert "googletagmanager.com/gtag/js?id=G-ABC123XYZ" in payload["install_hint"]
    assert len(calls) == 3


def test_create_ga4_property_no_account(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _seed_google_credential("acme", ["https://www.googleapis.com/auth/analytics.edit"])
    monkeypatch.setattr(activation_tools, "_google_api_call",
                        lambda *a, **kw: (200, {"accountSummaries": []}))
    ok, payload = activation_tools.dispatch("acme", "create_ga4_property", {
        "display_name": "X", "website_url": "https://x.com", "timezone": "UTC",
    })
    assert ok is True
    assert payload["status"] == "no_account"
    assert "analytics.google.com" in payload["hint"]


def test_create_ga4_property_stream_creation_fails_but_property_created(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _seed_google_credential("acme", ["https://www.googleapis.com/auth/analytics.edit"])

    def fake(method, url, tid, *, json_body=None, params=None):
        if "accountSummaries" in url:
            return 200, {"accountSummaries": [{"account": "accounts/1"}]}
        if url.endswith("/v1beta/properties"):
            return 201, {"name": "properties/99"}
        if "/dataStreams" in url:
            return 500, {"error": "internal"}
        raise AssertionError(url)

    monkeypatch.setattr(activation_tools, "_google_api_call", fake)
    ok, payload = activation_tools.dispatch("acme", "create_ga4_property", {
        "display_name": "X", "website_url": "https://x.com", "timezone": "UTC",
    })
    assert ok is True
    assert payload["status"] == "property_created_stream_failed"
    assert payload["property"] == "properties/99"


# --- verify_gsc_domain ----------------------------------------------------


def test_verify_gsc_domain_requires_webmasters_scope(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _seed_google_credential("acme", ["https://www.googleapis.com/auth/webmasters.readonly"])
    ok, payload = activation_tools.dispatch("acme", "verify_gsc_domain", {
        "site_url": "acmehvac.com",
    })
    assert ok is True
    assert payload["status"] == "reconnect_required"
    assert payload["missing_scope"].endswith("/webmasters")


def test_verify_gsc_domain_normalizes_site_url(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _seed_google_credential("acme", ["https://www.googleapis.com/auth/webmasters"])
    captured = {}

    def fake(method, url, tid, *, json_body=None, params=None):
        captured["method"] = method
        captured["url"] = url
        return 204, {}

    monkeypatch.setattr(activation_tools, "_google_api_call", fake)
    ok, payload = activation_tools.dispatch("acme", "verify_gsc_domain", {
        "site_url": "acmehvac.com",
    })
    assert ok is True
    assert payload["status"] == "added_dns_pending"
    assert payload["gsc_site"] == "sc-domain:acmehvac.com"
    # URL must encode the sc-domain: prefix.
    assert "sc-domain%3Aacmehvac.com" in captured["url"]
    assert captured["method"] == "PUT"


def test_verify_gsc_domain_accepts_sc_domain_prefix(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _seed_google_credential("acme", ["https://www.googleapis.com/auth/webmasters"])
    monkeypatch.setattr(activation_tools, "_google_api_call", lambda *a, **kw: (200, {}))
    ok, payload = activation_tools.dispatch("acme", "verify_gsc_domain", {
        "site_url": "sc-domain:acmehvac.com",
    })
    assert ok is True
    assert payload["gsc_site"] == "sc-domain:acmehvac.com"


def test_verify_gsc_domain_surfaces_api_error(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _seed_google_credential("acme", ["https://www.googleapis.com/auth/webmasters"])
    monkeypatch.setattr(activation_tools, "_google_api_call",
                        lambda *a, **kw: (403, {"error": "forbidden"}))
    ok, payload = activation_tools.dispatch("acme", "verify_gsc_domain", {
        "site_url": "acmehvac.com",
    })
    assert ok is False
    assert "403" in payload["error"]


# --- credentials.has_scope helper -----------------------------------------


def test_has_scope_matches_exact_grant(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    credentials.store("acme", "google", refresh_token="x", scopes=[
        "https://www.googleapis.com/auth/analytics.edit",
        "openid",
    ])
    assert credentials.has_scope("acme", "google", "openid") is True
    assert credentials.has_scope("acme", "google",
                                  "https://www.googleapis.com/auth/analytics.edit") is True
    assert credentials.has_scope("acme", "google",
                                  "https://www.googleapis.com/auth/analytics.readonly") is False


def test_has_scope_false_when_no_credential(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    assert credentials.has_scope("acme", "google", "openid") is False


def test_granted_scopes_returns_list(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    credentials.store("acme", "google", refresh_token="x", scopes=["a", "b"])
    assert credentials.granted_scopes("acme", "google") == ["a", "b"]
    assert credentials.granted_scopes("acme", "missing") == []
