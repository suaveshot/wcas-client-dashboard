"""Tests for the per-tenant knowledge base."""

import os

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import pytest

from dashboard_app.services import heartbeat_store, tenant_kb


@pytest.fixture
def _tenant_root(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    return tmp_path


def test_write_section_roundtrip(_tenant_root):
    tenant_kb.write_section("acme", "company", "Business name: Acme HVAC\nCity: Oxnard\n")
    body = tenant_kb.read_section("acme", "company")
    assert body is not None
    assert "# Company" in body  # generated header
    assert "Acme HVAC" in body
    assert body.endswith("\n")


def test_write_section_overwrites(_tenant_root):
    tenant_kb.write_section("acme", "voice", "first")
    tenant_kb.write_section("acme", "voice", "second")
    body = tenant_kb.read_section("acme", "voice")
    assert "first" not in body
    assert "second" in body


def test_read_section_none_for_missing(_tenant_root):
    assert tenant_kb.read_section("acme", "company") is None


def test_read_section_none_for_unknown_tenant(_tenant_root):
    assert tenant_kb.read_section("never_existed", "company") is None


def test_unknown_section_rejected(_tenant_root):
    with pytest.raises(tenant_kb.KbError):
        tenant_kb.write_section("acme", "random_section", "x")
    with pytest.raises(tenant_kb.KbError):
        tenant_kb.read_section("acme", "random_section")


def test_invalid_section_slug_rejected(_tenant_root):
    with pytest.raises(tenant_kb.KbError):
        tenant_kb.write_section("acme", "With Space", "x")
    with pytest.raises(tenant_kb.KbError):
        tenant_kb.write_section("acme", "../escape", "x")
    with pytest.raises(tenant_kb.KbError):
        tenant_kb.write_section("acme", "UPPER", "x")


def test_invalid_tenant_rejected(_tenant_root):
    with pytest.raises(heartbeat_store.HeartbeatError):
        tenant_kb.write_section("../escape", "company", "x")


def test_list_sections_sorted_and_whitelisted(_tenant_root):
    tenant_kb.write_section("acme", "company", "x")
    tenant_kb.write_section("acme", "services", "x")
    tenant_kb.write_section("acme", "voice", "x")
    # Write a junk file directly - list_sections must ignore it.
    junk_root = heartbeat_store.tenant_root("acme") / "kb"
    (junk_root / "random.md").write_text("hi", encoding="utf-8")
    assert tenant_kb.list_sections("acme") == ["company", "services", "voice"]


def test_list_sections_empty_for_unknown_tenant(_tenant_root):
    assert tenant_kb.list_sections("nothing_here") == []


def test_delete_section_removes_file(_tenant_root):
    tenant_kb.write_section("acme", "faq", "x")
    assert tenant_kb.delete_section("acme", "faq") is True
    assert tenant_kb.read_section("acme", "faq") is None
    assert tenant_kb.delete_section("acme", "faq") is False


def test_write_section_strips_trailing_whitespace(_tenant_root):
    tenant_kb.write_section("acme", "company", "Name: X\n\n\n\n\n")
    body = tenant_kb.read_section("acme", "company")
    # Exactly one trailing newline.
    assert body.endswith("Name: X\n")
    assert not body.endswith("\n\n")


def test_existing_stack_section_roundtrip(_tenant_root):
    """existing_stack captures the client's current tools during onboarding."""
    body = "- CRM: GoHighLevel\n- Email: Gmail\n- Phone: Google Voice\n"
    tenant_kb.write_section("acme", "existing_stack", body)
    read = tenant_kb.read_section("acme", "existing_stack")
    assert read is not None
    assert "# Existing Stack" in read
    assert "GoHighLevel" in read


def test_provisioning_plan_section_roundtrip(_tenant_root):
    """provisioning_plan is Sam's concierge handoff doc."""
    body = "## sales_pipeline\n- Strategy: connect_existing\n- Owner task: paste key\n"
    tenant_kb.write_section("acme", "provisioning_plan", body)
    read = tenant_kb.read_section("acme", "provisioning_plan")
    assert read is not None
    assert "# Provisioning Plan" in read
    assert "connect_existing" in read


def test_new_sections_appear_in_sections_frozenset():
    """Guard against accidental removal of the onboarding sections."""
    assert "existing_stack" in tenant_kb.SECTIONS
    assert "provisioning_plan" in tenant_kb.SECTIONS
    # And the original seven are still there.
    for expected in ("company", "services", "voice", "policies", "pricing", "faq", "known_contacts"):
        assert expected in tenant_kb.SECTIONS
