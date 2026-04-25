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
    # Bypass the onboarding-approval gate so provisioning-tool tests can
    # exercise the real handlers. Safety clause in clients_repo refuses
    # the bypass when COOKIE_DOMAIN points at the public prod domain.
    monkeypatch.setenv("DISABLE_ONBOARDING_APPROVAL_GATE", "1")
    monkeypatch.delenv("COOKIE_DOMAIN", raising=False)
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


def test_schemas_have_voice_and_data_tools():
    """v0.6.0 voice + personalization tools are part of the surface."""
    names = {t["name"] for t in activation_tools.TOOL_SCHEMAS}
    assert {"propose_voice_card", "fetch_airtable_schema", "propose_crm_mapping"} <= names


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


def test_detect_website_platform_wordpress_via_generator(monkeypatch):
    """Generator meta tag reveals WordPress cleanly."""
    class FakeResponse:
        status_code = 200
        text = '<html><head><meta name="generator" content="WordPress 6.4"></head></html>'
        headers = {}
        url = "https://acmehvac.com/"
        extensions = {}
    monkeypatch.setattr(activation_tools, "_httpx_get", lambda url, timeout=10.0: FakeResponse())
    ok, payload = activation_tools.dispatch("acme", "detect_website_platform", {"url": "https://acmehvac.com"})
    assert ok is True
    assert payload["platform"] == "wordpress"
    assert payload["takeover_feasible"] is True
    assert any("wordpress" in s.lower() for s in payload["signals"])


def test_detect_website_platform_shopify_via_cdn(monkeypatch):
    class FakeResponse:
        status_code = 200
        text = '<html><body><script src="https://cdn.shopify.com/foo.js"></script></body></html>'
        headers = {}
        url = "https://garciafolklorico.com/"
        extensions = {}
    monkeypatch.setattr(activation_tools, "_httpx_get", lambda url, timeout=10.0: FakeResponse())
    ok, payload = activation_tools.dispatch("acme", "detect_website_platform", {"url": "garciafolklorico.com"})
    assert ok is True
    assert payload["platform"] == "shopify"
    # Shopify is a managed platform we don't try to migrate off of.
    assert payload["takeover_feasible"] is False


def test_detect_website_platform_rejects_blank_url():
    ok, payload = activation_tools.dispatch("acme", "detect_website_platform", {"url": ""})
    assert ok is False
    assert "url is required" in payload["error"]


def test_detect_website_platform_falls_back_to_static(monkeypatch):
    """Plain HTML with zero fingerprints should default to static + takeover_feasible."""
    class FakeResponse:
        status_code = 200
        text = "<html><body><h1>Hi</h1></body></html>"
        headers = {}
        url = "https://tiny.example/"
        extensions = {}
    monkeypatch.setattr(activation_tools, "_httpx_get", lambda url, timeout=10.0: FakeResponse())
    ok, payload = activation_tools.dispatch("acme", "detect_website_platform", {"url": "tiny.example"})
    assert ok is True
    assert payload["platform"] == "static"
    assert payload["takeover_feasible"] is True


def test_record_provisioning_plan_writes_markdown_and_json(tmp_path, monkeypatch):
    import json
    from dashboard_app.services import tenant_kb
    ok, payload = activation_tools.dispatch("acme", "record_provisioning_plan", {
        "items": [
            {"service": "gbp", "strategy": "connect_existing", "credential_method": "oauth",
             "owner_task": "Approve Google consent", "sam_task": "Run Week-1 check-in"},
            {"service": "social", "strategy": "owner_signup", "credential_method": "screenshot",
             "owner_task": "Create FB Business Page", "sam_task": "Walk owner through Meta setup"},
        ],
    })
    assert ok is True
    assert payload["status"] == "saved"
    assert payload["item_count"] == 2

    md = tenant_kb.read_section("acme", "provisioning_plan")
    assert md is not None
    assert "gbp" in md and "Connect to their existing account" in md
    # Matches "Owner signs up with WCAS walking them through" row label.
    assert "signs up" in md.lower()

    json_path = tmp_path / "acme" / "state_snapshot" / "provisioning_plan.json"
    assert json_path.exists()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["tenant_id"] == "acme"
    assert len(data["items"]) == 2
    assert data["items"][0]["service"] == "gbp"


def test_record_provisioning_plan_rejects_invalid_strategy():
    ok, payload = activation_tools.dispatch("acme", "record_provisioning_plan", {
        "items": [{"service": "gbp", "strategy": "invalid_one"}],
    })
    assert ok is False
    assert "strategy must be one of" in payload["error"]


def test_record_provisioning_plan_requires_non_empty_items():
    ok, payload = activation_tools.dispatch("acme", "record_provisioning_plan", {"items": []})
    assert ok is False
    assert "non-empty array" in payload["error"]


def test_dispatch_audits_tool_calls(tmp_path, monkeypatch):
    """Every dispatch invocation should append a JSONL line to the audit log."""
    import json as _json
    from dashboard_app.services import audit_log

    activation_tools.dispatch("acme", "activate_pipeline", {"role_slug": "gbp", "step": "credentials"})
    log_path = tmp_path / "acme" / "audit" / "activation.log"
    assert log_path.exists()
    lines = [_json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(entry.get("tool") == "activate_pipeline" and entry.get("ok") is True for entry in lines)


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


# --- v0.6.0 voice + personalization tools ----------------------------------


def test_propose_voice_card_persists_card_and_voice_kb():
    from dashboard_app.services import voice_card

    ok, payload = activation_tools.dispatch("acme", "propose_voice_card", {
        "traits": ["warm", "family-oriented", "bilingual"],
        "generic_sample": "Hi! Don't forget your appointment tomorrow.",
        "voice_sample": "Hola familia, see you in class tomorrow.",
        "sample_context": "re-engagement reminder",
        "source_pages": ["https://garciafolklorico.com/about"],
    })
    assert ok is True
    assert payload["status"] == "rendered"
    assert payload["card_id"].startswith("vc_")
    assert payload["trait_count"] == 3

    # Voice card persisted to state_snapshot/voice_card.json
    saved = voice_card.load("acme")
    assert saved is not None
    assert saved["traits"] == ["warm", "family-oriented", "bilingual"]
    assert "Hola familia" in saved["voice_sample"]

    # Voice KB section populated for downstream Opus surfaces
    voice_md = tenant_kb.read_section("acme", "voice")
    assert voice_md is not None
    assert "warm" in voice_md
    assert "Hola familia" in voice_md


def test_propose_voice_card_rejects_empty_traits():
    ok, payload = activation_tools.dispatch("acme", "propose_voice_card", {
        "traits": [],
        "generic_sample": "x",
        "voice_sample": "y",
    })
    assert ok is False
    assert "traits" in payload["error"]


def test_propose_voice_card_rejects_missing_samples():
    ok, payload = activation_tools.dispatch("acme", "propose_voice_card", {
        "traits": ["warm"],
        "generic_sample": "",
        "voice_sample": "y",
    })
    assert ok is False
    assert "generic_sample" in payload["error"] or "voice_sample" in payload["error"]


def test_propose_crm_mapping_persists_payload_and_md():
    from dashboard_app.services import crm_mapping as cm

    ok, payload = activation_tools.dispatch("acme", "propose_crm_mapping", {
        "base_id": "appXXX1234567890",
        "table_name": "Students",
        "field_mapping": {
            "first_name": "Child Name",
            "last_engagement": "Registered On",
            "contact_email": "Email",
        },
        "segments": [
            {"slug": "active", "label": "Active", "count": 15, "sample_names": ["Sofia", "Diego"]},
            {"slug": "inactive_30d", "label": "Inactive 30+ days", "count": 12,
             "sample_names": ["Maria Sanchez", "Juan Diaz"]},
            {"slug": "brand_new", "label": "Brand new", "count": 3, "sample_names": ["Olivia"]},
        ],
        "proposed_actions": [
            {"segment": "inactive_30d", "playbook": "re_engagement", "automation": "email_assistant"},
        ],
    })
    assert ok is True
    assert payload["status"] == "rendered"
    assert payload["mapping_id"].startswith("cm_")
    assert payload["segment_count"] == 3
    assert payload["action_count"] == 1

    saved = cm.load("acme")
    assert saved is not None
    assert saved["table_name"] == "Students"
    assert saved["segments"][1]["slug"] == "inactive_30d"

    md = tenant_kb.read_section("acme", "crm_mapping")
    assert md is not None
    assert "Students" in md
    assert "inactive_30d" in md


def test_propose_crm_mapping_rejects_empty_segments():
    ok, payload = activation_tools.dispatch("acme", "propose_crm_mapping", {
        "base_id": "appXXX",
        "table_name": "T",
        "field_mapping": {"a": "b"},
        "segments": [],
    })
    assert ok is False
    assert "segments" in payload["error"]


def test_fetch_airtable_schema_no_base_configured(tmp_path, monkeypatch):
    """When no whitelist exists for the tenant, return a clean status not an error."""
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    ok, payload = activation_tools.dispatch("acme", "fetch_airtable_schema", {"base_id": ""})
    assert ok is True  # graceful response, not a failure
    assert payload["status"] == "no_base_configured"


def test_fetch_airtable_schema_whitelist_mismatch(tmp_path, monkeypatch):
    """A base_id outside the tenant's whitelist is rejected."""
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    # Set up a whitelist for "acme" pointing at appAAA, then try to read appBBB.
    cfg_dir = tmp_path / "acme"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "tenant_config.json").write_text(
        '{"airtable_bookings": {"base_id": "appAAA1234567890", "table_name": "T"}}',
        encoding="utf-8",
    )
    ok, payload = activation_tools.dispatch("acme", "fetch_airtable_schema",
                                            {"base_id": "appBBB1234567890"})
    assert ok is False
    assert "whitelist" in payload["error"]
