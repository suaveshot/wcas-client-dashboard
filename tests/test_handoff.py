"""Tests for dashboard_app.services.handoff."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import pytest

from dashboard_app.services import (
    activation_tools,
    email_sender,
    handoff,
    tenant_automations,
)


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------


def test_render_requires_tenant_and_owner():
    with pytest.raises(ValueError):
        handoff.render(tenant_id="", owner_name="Sam")
    with pytest.raises(ValueError):
        handoff.render(tenant_id="acme", owner_name="")


def test_render_returns_html_and_text(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    tenant_automations.seed_for_tier("acme", "starter")
    subject, html_body, text_body = handoff.render(
        tenant_id="acme",
        owner_name="Sam Alarcon",
        business_name="Acme HVAC",
    )
    assert "Acme HVAC" in subject
    assert "Sam" in html_body
    assert "Sam" in text_body
    # Heads-up: the letter must mention the owner's first name only.
    assert "Hi Sam" in html_body
    assert "Hi Sam" in text_body


def test_render_lists_enabled_automations(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    tenant_automations.seed_for_tier("acme", "starter")
    _, html_body, text_body = handoff.render(
        tenant_id="acme",
        owner_name="Sam",
    )
    # Starter tier seeds GBP at minimum; verify it shows up.
    assert "Google Business Profile" in html_body or "gbp" in html_body.lower()


def test_render_handles_tenant_with_no_enabled_automations(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _, html_body, text_body = handoff.render(
        tenant_id="brand_new",
        owner_name="Sam",
    )
    assert "no automations enabled" in text_body.lower()


def test_render_business_name_falls_back_to_owner(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    subject, _, _ = handoff.render(tenant_id="acme", owner_name="Sam Alarcon")
    assert "Sam Alarcon" in subject


def test_render_uses_provided_now_for_date(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    pinned = datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc)
    _, html_body, text_body = handoff.render(
        tenant_id="acme",
        owner_name="Sam",
        now=pinned,
    )
    assert "May 01, 2026" in html_body
    assert "May 01, 2026" in text_body


def test_render_escapes_owner_name_to_prevent_injection(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _, html_body, _ = handoff.render(
        tenant_id="acme",
        owner_name="<script>alert(1)</script> Sam",
    )
    assert "<script>" not in html_body
    assert "&lt;script&gt;" in html_body


# ---------------------------------------------------------------------------
# send_handoff
# ---------------------------------------------------------------------------


class _CapturingSender:
    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


def test_send_handoff_calls_sender(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    tenant_automations.seed_for_tier("acme", "starter")
    sender = _CapturingSender()
    ok = handoff.send_handoff(
        tenant_id="acme",
        owner_name="Sam Alarcon",
        owner_email="sam@example.com",
        business_name="Acme HVAC",
        sender=sender,
    )
    assert ok is True
    assert len(sender.calls) == 1
    call = sender.calls[0]
    assert call["to_email"] == "sam@example.com"
    assert "Acme HVAC" in call["subject"]
    assert call["channel"] == "support"


def test_send_handoff_returns_false_for_missing_email(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    sender = _CapturingSender()
    ok = handoff.send_handoff(
        tenant_id="acme",
        owner_name="Sam",
        owner_email="",
        sender=sender,
    )
    assert ok is False
    assert sender.calls == []


def test_send_handoff_returns_false_on_sender_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))

    def boom(**_kw):
        raise email_sender.EmailSendError("smtp 500")

    ok = handoff.send_handoff(
        tenant_id="acme",
        owner_name="Sam",
        owner_email="sam@example.com",
        sender=boom,
    )
    assert ok is False


# ---------------------------------------------------------------------------
# activation_tools wiring
# ---------------------------------------------------------------------------


def test_mark_activation_complete_sends_handoff_when_email_provided(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    tenant_automations.seed_for_tier("acme", "starter")

    captured: dict = {}

    def fake_send(**kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(handoff, "send_handoff", fake_send)
    ok, payload = activation_tools.dispatch(
        "acme",
        "mark_activation_complete",
        {
            "owner_name": "Sam Alarcon",
            "owner_email": "sam@example.com",
            "business_name": "Acme HVAC",
        },
    )
    assert ok is True
    assert payload["status"] == "activated"
    assert payload["handoff_sent"] is True
    assert captured["owner_email"] == "sam@example.com"


def test_mark_activation_complete_skips_handoff_without_email(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))

    called = {"hit": False}

    def fake_send(**_kw):
        called["hit"] = True
        return True

    monkeypatch.setattr(handoff, "send_handoff", fake_send)
    ok, payload = activation_tools.dispatch(
        "acme",
        "mark_activation_complete",
        {},
    )
    assert ok is True
    assert payload["status"] == "activated"
    assert payload["handoff_sent"] is False
    assert called["hit"] is False


def test_mark_activation_complete_swallows_handoff_exception(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))

    def boom(**_kw):
        raise RuntimeError("send failed hard")

    monkeypatch.setattr(handoff, "send_handoff", boom)
    ok, payload = activation_tools.dispatch(
        "acme",
        "mark_activation_complete",
        {
            "owner_name": "Sam",
            "owner_email": "sam@example.com",
        },
    )
    # Activation must succeed even if the letter send blows up.
    assert ok is True
    assert payload["status"] == "activated"
    assert payload["handoff_sent"] is False
