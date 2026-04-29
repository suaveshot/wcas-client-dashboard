"""Tests for dashboard_app.services.platform_master.

Covers the whitelist + slug guard, file resolution, missing-file None
return, and the critical isolation invariant: tenant code paths can't
reach into _platform/.
"""

from __future__ import annotations

import json
import os

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import pytest

from dashboard_app.services import platform_master


# ---------------------------------------------------------------------------
# whitelist + slug guard
# ---------------------------------------------------------------------------


def test_load_master_rejects_unknown_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_ROOT", str(tmp_path))
    with pytest.raises(platform_master.PlatformMasterError):
        platform_master.load_master("unknown_provider")


def test_load_master_rejects_traversal_slug(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_ROOT", str(tmp_path))
    with pytest.raises(platform_master.PlatformMasterError):
        platform_master.load_master("../etc/passwd")


def test_load_master_rejects_uppercase(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_ROOT", str(tmp_path))
    with pytest.raises(platform_master.PlatformMasterError):
        platform_master.load_master("BrightLocal")


def test_load_master_rejects_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_ROOT", str(tmp_path))
    with pytest.raises(platform_master.PlatformMasterError):
        platform_master.load_master("")


# ---------------------------------------------------------------------------
# file resolution
# ---------------------------------------------------------------------------


def test_load_master_returns_none_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_ROOT", str(tmp_path))
    assert platform_master.load_master("brightlocal") is None


def test_load_master_returns_parsed_dict(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_ROOT", str(tmp_path))
    bl_dir = tmp_path / "brightlocal"
    bl_dir.mkdir()
    (bl_dir / "master.json").write_text(
        json.dumps({"api_key": "live-secret", "account": "wcas-master"}),
        encoding="utf-8",
    )
    data = platform_master.load_master("brightlocal")
    assert data == {"api_key": "live-secret", "account": "wcas-master"}


def test_load_master_uses_provider_specific_filename(tmp_path, monkeypatch):
    """ghl uses agency.json, airtable uses workspace.json, etc."""
    monkeypatch.setenv("PLATFORM_ROOT", str(tmp_path))
    ghl_dir = tmp_path / "ghl"
    ghl_dir.mkdir()
    (ghl_dir / "agency.json").write_text(
        json.dumps({"agency_id": "agcy-1", "api_key": "ghl-secret"}),
        encoding="utf-8",
    )
    data = platform_master.load_master("ghl")
    assert data is not None
    assert data["agency_id"] == "agcy-1"


def test_load_master_swallows_malformed_json(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_ROOT", str(tmp_path))
    bl_dir = tmp_path / "brightlocal"
    bl_dir.mkdir()
    (bl_dir / "master.json").write_text("not-json", encoding="utf-8")
    assert platform_master.load_master("brightlocal") is None


def test_load_master_returns_none_when_top_level_is_not_dict(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_ROOT", str(tmp_path))
    bl_dir = tmp_path / "brightlocal"
    bl_dir.mkdir()
    (bl_dir / "master.json").write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    assert platform_master.load_master("brightlocal") is None


# ---------------------------------------------------------------------------
# platform_root + env override
# ---------------------------------------------------------------------------


def test_platform_root_uses_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv("PLATFORM_ROOT", str(tmp_path))
    assert platform_master.platform_root() == tmp_path


def test_platform_root_falls_back_to_default(monkeypatch):
    from pathlib import Path
    monkeypatch.delenv("PLATFORM_ROOT", raising=False)
    assert platform_master.platform_root() == Path(platform_master.DEFAULT_PLATFORM_ROOT)


# ---------------------------------------------------------------------------
# is_provisioned helper
# ---------------------------------------------------------------------------


def test_is_provisioned_true_when_file_present(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_ROOT", str(tmp_path))
    (tmp_path / "brightlocal").mkdir()
    (tmp_path / "brightlocal" / "master.json").write_text(json.dumps({"x": 1}), encoding="utf-8")
    assert platform_master.is_provisioned("brightlocal") is True


def test_is_provisioned_false_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_ROOT", str(tmp_path))
    assert platform_master.is_provisioned("brightlocal") is False


def test_is_provisioned_false_for_unknown_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_ROOT", str(tmp_path))
    assert platform_master.is_provisioned("unknown_provider") is False


# ---------------------------------------------------------------------------
# isolation: tenant code can't reach _platform
# ---------------------------------------------------------------------------


def test_tenant_runtime_does_not_import_platform_master():
    """The TenantContext is the one object every pipeline holds. It must
    NOT depend on platform_master - tenant code reads tenant creds via
    services.credentials, never the platform-master root."""
    from wc_solns_pipelines.shared import tenant_runtime
    src = tenant_runtime.__file__
    text = open(src, encoding="utf-8").read()
    assert "platform_master" not in text


def test_tenant_runtime_module_does_not_expose_platform_master():
    from wc_solns_pipelines.shared import tenant_runtime
    assert not hasattr(tenant_runtime, "platform_master")
    assert not hasattr(tenant_runtime, "load_master")
    assert not hasattr(tenant_runtime, "platform_root")
