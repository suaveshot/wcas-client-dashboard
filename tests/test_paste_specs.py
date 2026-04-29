"""Tests for dashboard_app.services.paste_specs + the request_credential
api_key_paste path it powers."""

from __future__ import annotations

import os

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import pytest

from dashboard_app.services import (
    activation_tools,
    credentials as _credentials,
    paste_specs,
)


# ---------------------------------------------------------------------------
# core spec API
# ---------------------------------------------------------------------------


def test_supported_services_returns_paste_providers():
    services = paste_specs.supported_services()
    # Whitelist invariant: every service with a spec must be in PASTE_PROVIDERS.
    for svc in services:
        assert svc in _credentials.PASTE_PROVIDERS, (
            f"paste spec exists for {svc!r} but it's not in PASTE_PROVIDERS - "
            f"add it to credentials.PASTE_PROVIDERS or remove the spec"
        )


def test_has_spec_for_known_provider():
    assert paste_specs.has_spec("gmail_app_password") is True
    assert paste_specs.has_spec("ghl") is True


def test_has_spec_false_for_unknown():
    assert paste_specs.has_spec("totally_made_up") is False


def test_get_spec_returns_dataclass():
    spec = paste_specs.get_spec("ghl")
    assert spec is not None
    assert spec.service == "ghl"
    assert spec.label == "GoHighLevel"
    assert len(spec.fields) >= 2


def test_get_spec_returns_none_for_unknown():
    assert paste_specs.get_spec("totally_made_up") is None


# ---------------------------------------------------------------------------
# JSON form spec shape
# ---------------------------------------------------------------------------


def test_get_form_spec_serializes_to_plain_dict():
    form = paste_specs.get_form_spec("gmail_app_password")
    assert form is not None
    assert isinstance(form, dict)
    assert form["service"] == "gmail_app_password"
    assert isinstance(form["fields"], list)
    for field in form["fields"]:
        assert {"name", "label", "type", "required", "placeholder"}.issubset(field.keys())


def test_form_spec_fields_have_valid_types():
    for service in paste_specs.supported_services():
        form = paste_specs.get_form_spec(service)
        for field in form["fields"]:
            assert field["type"] in {"text", "password", "email"}, (
                f"{service}.{field['name']} has unexpected type {field['type']!r}"
            )


def test_form_spec_includes_unlocks_copy():
    """Every spec should narrate WHY we need the credential."""
    for service in paste_specs.supported_services():
        form = paste_specs.get_form_spec(service)
        assert form["unlocks"], f"{service!r} missing unlocks copy"


def test_form_spec_includes_instructions():
    for service in paste_specs.supported_services():
        form = paste_specs.get_form_spec(service)
        assert form["instructions"], f"{service!r} missing instructions"
        assert len(form["instructions"]) >= 30, (
            f"{service!r} instructions too terse"
        )


def test_form_spec_for_unknown_returns_none():
    assert paste_specs.get_form_spec("totally_made_up") is None


# ---------------------------------------------------------------------------
# per-provider invariants
# ---------------------------------------------------------------------------


def test_gmail_app_password_has_email_and_password_fields():
    form = paste_specs.get_form_spec("gmail_app_password")
    field_names = {f["name"] for f in form["fields"]}
    assert "email_address" in field_names
    assert "app_password" in field_names


def test_ghl_has_api_key_and_location_id():
    form = paste_specs.get_form_spec("ghl")
    field_names = {f["name"] for f in form["fields"]}
    assert "api_key" in field_names
    assert "location_id" in field_names


def test_airtable_has_pat():
    form = paste_specs.get_form_spec("airtable")
    field_names = {f["name"] for f in form["fields"]}
    assert "personal_access_token" in field_names


def test_twilio_has_account_sid_and_auth_token():
    form = paste_specs.get_form_spec("twilio_paste")
    field_names = {f["name"] for f in form["fields"]}
    assert "account_sid" in field_names
    assert "auth_token" in field_names


def test_password_fields_use_password_type():
    """Sensitive fields must be type=password so the dashboard masks them."""
    sensitive = {
        ("gmail_app_password", "app_password"),
        ("ghl", "api_key"),
        ("airtable", "personal_access_token"),
        ("twilio_paste", "auth_token"),
        ("connecteam", "api_key"),
        ("brightlocal", "api_key"),
        ("brightlocal", "api_secret"),
    }
    for service, fname in sensitive:
        form = paste_specs.get_form_spec(service)
        field = next(f for f in form["fields"] if f["name"] == fname)
        assert field["type"] == "password", (
            f"{service}.{fname} should be type=password, got {field['type']!r}"
        )


# ---------------------------------------------------------------------------
# request_credential wiring
# ---------------------------------------------------------------------------


def test_request_credential_paste_returns_form_spec(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    ok, payload = activation_tools.dispatch(
        "acme",
        "request_credential",
        {"service": "gmail_app_password", "method": "api_key_paste"},
    )
    assert ok is True
    assert payload["status"] == "render_paste_form"
    assert payload["service"] == "gmail_app_password"
    assert payload["paste_endpoint"] == "/api/credentials/gmail_app_password/paste"
    assert "form" in payload
    assert payload["form"]["fields"]


def test_request_credential_paste_unknown_service_is_honest(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    ok, payload = activation_tools.dispatch(
        "acme",
        "request_credential",
        {"service": "totally_made_up", "method": "api_key_paste"},
    )
    assert ok is True
    assert payload["status"] == "not_yet_implemented"
    assert "Supported services" in payload["hint"]


def test_request_credential_google_oauth_path_unchanged(tmp_path, monkeypatch):
    """Regression: paste support must not break the existing Google OAuth path."""
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    ok, payload = activation_tools.dispatch(
        "acme",
        "request_credential",
        {"service": "google", "method": "oauth"},
    )
    assert ok is True
    assert payload["status"] == "render_button"
    assert payload["service"] == "google"
