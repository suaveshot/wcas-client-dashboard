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
