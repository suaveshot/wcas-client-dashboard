"""Tests for wc_solns_pipelines.shared.tenant_runtime.TenantContext.

The TenantContext is a thin facade over dashboard_app.services so we test
that delegation works (the underlying services have their own dedicated
test suites). The interesting cases are the pipeline-side helpers
(read_state, write_state, is_paused gate, state_path).
"""

from __future__ import annotations

import json
import os

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import pytest

from dashboard_app.services import credentials as _credentials, tenant_prefs as _tenant_prefs
from wc_solns_pipelines.shared.tenant_runtime import TenantContext, TenantNotFound


# ---------------------------------------------------------------------------
# construction
# ---------------------------------------------------------------------------


def test_invalid_tenant_id_raises_tenant_not_found(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    with pytest.raises(TenantNotFound):
        TenantContext("../etc/passwd")
    with pytest.raises(TenantNotFound):
        TenantContext("")


def test_valid_tenant_id_resolves_root(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    ctx = TenantContext("acme")
    assert ctx.tenant_id == "acme"
    assert ctx.root == tmp_path / "acme"


# ---------------------------------------------------------------------------
# config + paused gate
# ---------------------------------------------------------------------------


def test_config_returns_empty_dict_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    ctx = TenantContext("acme")
    assert ctx.config() == {}


def test_config_returns_parsed_json_when_present(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    ctx = TenantContext("acme")
    ctx.root.mkdir(parents=True, exist_ok=True)
    (ctx.root / "tenant_config.json").write_text(
        json.dumps({"status": "active", "industry": "fitness"}), encoding="utf-8"
    )
    cfg = ctx.config()
    assert cfg["status"] == "active"
    assert cfg["industry"] == "fitness"


def test_is_paused_reflects_tenant_config_status(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    ctx = TenantContext("acme")
    assert ctx.is_paused is False  # no config -> not paused
    ctx.root.mkdir(parents=True, exist_ok=True)
    (ctx.root / "tenant_config.json").write_text(
        json.dumps({"status": "paused"}), encoding="utf-8"
    )
    assert ctx.is_paused is True


# ---------------------------------------------------------------------------
# prefs + per-pipeline approval gate
# ---------------------------------------------------------------------------


def test_prefs_reads_through_tenant_prefs_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    ctx = TenantContext("acme")
    prefs = ctx.prefs
    assert prefs["timezone"] == "America/Los_Angeles"  # default
    assert prefs["require_approval"] == {}


def test_requires_approval_reflects_pref(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    ctx = TenantContext("acme")
    assert ctx.requires_approval("reviews") is False
    _tenant_prefs.set_require_approval("acme", "reviews", True)
    assert ctx.requires_approval("reviews") is True


# ---------------------------------------------------------------------------
# credentials delegation
# ---------------------------------------------------------------------------


def test_credentials_returns_none_when_provider_not_connected(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    ctx = TenantContext("acme")
    assert ctx.credentials("google") is None


def test_credentials_returns_stored_record(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _credentials.store(
        "acme",
        "google",
        refresh_token="1//fake-test-token",
        scopes=["openid", "https://www.googleapis.com/auth/business.manage"],
    )
    ctx = TenantContext("acme")
    rec = ctx.credentials("google")
    assert rec is not None
    assert rec["provider"] == "google"
    assert rec["refresh_token"] == "1//fake-test-token"


def test_has_scope_reads_credential_scopes(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _credentials.store(
        "acme",
        "google",
        refresh_token="1//x",
        scopes=["https://www.googleapis.com/auth/business.manage"],
    )
    ctx = TenantContext("acme")
    assert ctx.has_scope("google", "https://www.googleapis.com/auth/business.manage") is True
    assert ctx.has_scope("google", "https://www.googleapis.com/auth/calendar") is False


# ---------------------------------------------------------------------------
# KB delegation
# ---------------------------------------------------------------------------


def test_kb_returns_none_when_section_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    ctx = TenantContext("acme")
    assert ctx.kb("voice") is None


def test_kb_returns_section_text_when_written(tmp_path, monkeypatch):
    from dashboard_app.services import tenant_kb
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    tenant_kb.write_section("acme", "voice", "Warm, plain-language, never corporate.")
    ctx = TenantContext("acme")
    text = ctx.kb("voice")
    assert text is not None
    assert "Warm, plain-language" in text


def test_list_kb_sections_reflects_what_was_written(tmp_path, monkeypatch):
    from dashboard_app.services import tenant_kb
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    tenant_kb.write_section("acme", "voice", "x")
    tenant_kb.write_section("acme", "company", "y")
    ctx = TenantContext("acme")
    sections = ctx.list_kb_sections()
    assert "voice" in sections
    assert "company" in sections


# ---------------------------------------------------------------------------
# voice card + CRM mapping (just confirm None passthrough; underlying
# services have full coverage)
# ---------------------------------------------------------------------------


def test_voice_card_returns_none_when_unset(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    ctx = TenantContext("acme")
    assert ctx.voice_card() is None


def test_crm_mapping_returns_none_when_unset(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    ctx = TenantContext("acme")
    assert ctx.crm_mapping() is None


# ---------------------------------------------------------------------------
# per-pipeline state
# ---------------------------------------------------------------------------


def test_state_path_is_under_pipeline_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    ctx = TenantContext("acme")
    p = ctx.state_path("reviews")
    assert p == tmp_path / "acme" / "pipeline_state" / "reviews.json"


def test_read_state_returns_empty_dict_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    ctx = TenantContext("acme")
    assert ctx.read_state("reviews") == {}


def test_write_state_persists_atomically_and_round_trips(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    ctx = TenantContext("acme")
    ctx.write_state("reviews", {"last_check": "2026-04-29T10:00:00Z", "seen_review_ids": ["rev1", "rev2"]})
    again = ctx.read_state("reviews")
    assert again["last_check"] == "2026-04-29T10:00:00Z"
    assert again["seen_review_ids"] == ["rev1", "rev2"]
    assert "updated_at" in again  # auto-stamped by write


def test_write_state_creates_parent_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    ctx = TenantContext("acme")
    # No pipeline_state dir yet
    ctx.write_state("gbp", {"runs": 0})
    assert (tmp_path / "acme" / "pipeline_state" / "gbp.json").exists()


def test_write_state_then_read_state_isolates_pipelines(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    ctx = TenantContext("acme")
    ctx.write_state("reviews", {"x": 1})
    ctx.write_state("gbp", {"y": 2})
    assert ctx.read_state("reviews")["x"] == 1
    assert ctx.read_state("gbp")["y"] == 2
    assert "y" not in ctx.read_state("reviews")
